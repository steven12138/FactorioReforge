"""Ask a Factorio binary what it is and what it can open.

``--version`` needs no save, no config and no port, and it works on a tree that
has never been started -- which is what makes it usable as a check *before*
committing to a swap. Measured on 2.0.77 headless::

    Version: 2.0.77 (build 84539, linux64, headless)
    Version: 64
    Map input version: 1.0.0-0
    Map output version: 2.0.77-0

The two map lines are the whole compatibility story, stated by the binary
itself. Nothing here needs a table of which versions can read which saves, and
a future build that widens or narrows the window is handled without a change:
the second ``Version:`` line is an internal API number and is ignored.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from pathlib import Path

from factorio_reforge.versions.errors import VersionError
from factorio_reforge.versions.savefile import MapVersion, parse_version

_VERSION_LINE = re.compile(
    r"^Version:\s*(?P<version>\d+\.\d+\.\d+)\s*"
    r"\(build\s*(?P<build>\d+),\s*(?P<platform>[^,)]+),\s*(?P<flavour>[^)]+)\)",
    re.M,
)
_MAP_INPUT = re.compile(r"^Map input version:\s*(?P<version>[\d.\-]+)", re.M)
_MAP_OUTPUT = re.compile(r"^Map output version:\s*(?P<version>[\d.\-]+)", re.M)

#: --version on a healthy binary answers in well under a second. A tree that is
#: half-extracted or on a stalled network mount must not hang a command.
PROBE_TIMEOUT = 30.0


@dataclasses.dataclass(frozen=True)
class BinaryInfo:
    """What one Factorio executable says about itself."""

    version: MapVersion
    build: int
    platform: str
    flavour: str
    map_input: MapVersion
    map_output: MapVersion
    path: Path | None = None

    @property
    def release(self) -> str:
        return self.version.release

    def describe(self) -> str:
        return f"{self.release} (build {self.build}, {self.platform}, {self.flavour})"

    def can_load(self, save: MapVersion) -> bool:
        """Whether this binary can open a save written by ``save``.

        The upper bound is what makes downgrades dangerous and is the reason
        this function exists: a save from a newer build is outside the window,
        and Factorio refuses it at load rather than migrating backwards.
        """
        return self.map_input <= save <= self.map_output


def can_load(binary: BinaryInfo, save: MapVersion) -> bool:
    return binary.can_load(save)


def parse_version_output(text: str, path: Path | None = None) -> BinaryInfo:
    """Turn ``--version`` output into a :class:`BinaryInfo`."""
    match = _VERSION_LINE.search(text or "")
    if match is None:
        raise VersionError(
            "the binary did not report a version; its output was: "
            + (text.strip().splitlines()[0] if text.strip() else "(nothing)")
        )
    version = parse_version(match.group("version"))

    input_match = _MAP_INPUT.search(text)
    output_match = _MAP_OUTPUT.search(text)
    # Older builds may not print the map lines. Falling back to "it can read
    # anything up to itself" is the conservative reading: it still refuses a
    # save from a newer build, which is the direction that loses worlds.
    map_input = parse_version(input_match.group("version")) if input_match else MapVersion(0, 0, 0)
    map_output = parse_version(output_match.group("version")) if output_match else version

    return BinaryInfo(
        version=version,
        build=int(match.group("build")),
        platform=match.group("platform").strip(),
        flavour=match.group("flavour").strip(),
        map_input=map_input,
        map_output=map_output,
        path=path,
    )


async def read_binary(path: Path) -> BinaryInfo:
    """Run ``<path> --version`` and parse it."""
    if not path.is_file():
        raise VersionError(f"there is no Factorio binary at {path}")
    try:
        process = await asyncio.create_subprocess_exec(
            str(path), "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise VersionError(f"could not run {path}: {exc}") from exc
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=PROBE_TIMEOUT)
    except TimeoutError:
        process.kill()
        raise VersionError(f"{path} --version did not answer within {PROBE_TIMEOUT:g}s") from None
    return parse_version_output(stdout.decode("utf-8", "replace"), path)
