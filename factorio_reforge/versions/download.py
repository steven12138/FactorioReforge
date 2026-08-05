"""Fetch a headless build from factorio.com and unpack it into ``versions/``.

Headless downloads need no account, which is why ``install.sh`` can already do
this in one line. What is added here is the part that matters when the download
is going to become the server: the file is checked for being a tarball rather
than a redirect to an error page, it is extracted somewhere else and only moved
into place once complete, and the binary is asked its version so a tree named
``2.0.78`` cannot contain something else.

``urllib`` on a worker thread, matching
:mod:`factorio_reforge.mods.portal` -- this is a handful of requests a year and
not a reason to grow the dependency list.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from factorio_reforge.versions.binary import read_binary
from factorio_reforge.versions.errors import VersionError
from factorio_reforge.versions.layout import BINARY_SUFFIX, Installation

LATEST_RELEASES_URL = "https://factorio.com/api/latest-releases"
DOWNLOAD_URL = "https://factorio.com/get-download/{version}/{build}/{distro}"

USER_AGENT = "FactorioReforge/0.1 (+https://github.com/)"

#: The first bytes of an xz stream. A download that redirects to an HTML error
#: page is otherwise handed to tar, which fails with something unreadable.
XZ_MAGIC = b"\xfd7zXZ\x00"

TIMEOUT = 60.0


class DownloadError(VersionError):
    pass


def download_url(version: str, build: str = "headless", distro: str = "linux64") -> str:
    return DOWNLOAD_URL.format(version=version, build=build, distro=distro)


async def fetch_latest_releases() -> dict[str, dict[str, str]]:
    """``{"stable": {"headless": "2.0.77", ...}, "experimental": {...}}``."""
    return await asyncio.to_thread(_fetch_latest_sync)


def _fetch_latest_sync() -> dict[str, dict[str, str]]:
    request = urllib.request.Request(LATEST_RELEASES_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"factorio.com returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"cannot reach factorio.com: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise DownloadError(f"factorio.com returned something that was not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DownloadError("factorio.com returned an unexpected shape for the release list")
    return data


async def install_version(
    installation: Installation,
    version: str,
    *,
    build: str = "headless",
    distro: str = "linux64",
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download ``version`` and leave it in ``versions/<version>``.

    Safe to run with the server up: nothing outside ``versions/`` is touched,
    and the tree only appears at its final name once it is complete and has
    confirmed its own version.
    """
    target = installation.version_dir(version)
    if installation.is_installed(version):
        raise DownloadError(f"{version} is already installed at {target}")
    if target.exists():
        raise DownloadError(f"{target} exists but has no binary in it; remove it first")

    installation.versions_dir.mkdir(parents=True, exist_ok=True)
    url = download_url(version, build, distro)

    with tempfile.TemporaryDirectory(dir=installation.versions_dir, prefix=".download-") as work:
        workdir = Path(work)
        tarball = workdir / "factorio.tar.xz"
        await asyncio.to_thread(_download_sync, url, tarball, on_progress)
        _check_is_tarball(tarball, version)

        unpacked = workdir / "unpacked"
        await asyncio.to_thread(_extract_sync, tarball, unpacked)

        tree = unpacked / "factorio"
        if not (tree / BINARY_SUFFIX).is_file():
            raise DownloadError(
                f"the {version} tarball did not contain {BINARY_SUFFIX}; "
                "it may not be a headless build"
            )

        found = await read_binary(tree / BINARY_SUFFIX)
        if found.release != version:
            raise DownloadError(
                f"asked factorio.com for {version} and got {found.release}; "
                "nothing was installed"
            )

        # Same filesystem by construction -- the work directory is inside
        # versions/ -- so this is a rename, not a copy of several hundred MB.
        tree.rename(target)

    installation.link_shared(version)
    return target


def _check_is_tarball(path: Path, version: str) -> None:
    with path.open("rb") as handle:
        head = handle.read(len(XZ_MAGIC))
    if head != XZ_MAGIC:
        raise DownloadError(
            f"what came back for {version} is not a tarball. Check that the version "
            "exists -- an unknown one answers with a web page, not a download."
        )


def _download_sync(url: str, target: Path, on_progress=None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            with target.open("wb") as handle:
                while chunk := response.read(1 << 16):
                    handle.write(chunk)
                    received += len(chunk)
                    if on_progress is not None:
                        on_progress(received, total)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DownloadError("factorio.com has no such version") from exc
        raise DownloadError(f"download failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"download failed: {exc.reason}") from exc


def _extract_sync(tarball: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:xz") as archive:
        for member in archive.getmembers():
            # A tarball that writes outside its own directory would be writing
            # into the server install. Refuse rather than trust the source.
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise DownloadError(f"the tarball contains an unsafe path: {member.name}")
        _extract_all(archive, destination)


def _extract_all(archive: tarfile.TarFile, destination: Path) -> None:
    try:
        archive.extractall(destination, filter="tar")
    except TypeError:  # pragma: no cover - Python < 3.11.4
        archive.extractall(destination)


def remove_download_leftovers(installation: Installation) -> None:
    """Clear ``.download-*`` directories a killed process left behind."""
    if not installation.versions_dir.is_dir():
        return
    for entry in installation.versions_dir.glob(".download-*"):
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
