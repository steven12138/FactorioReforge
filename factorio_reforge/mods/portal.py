"""Client for the Factorio mod portal at https://mods.factorio.com/api.

Browsing needs no credentials; downloading needs a factorio.com username and
token from an account that owns the game. The token is a secret and is never
logged or included in an error message -- only ever placed in a query string.

Measured against the live portal: the full mod list is ~22,500 entries and 13 MB
and takes ~14 s to fetch, which is why searching goes through a cached index
rather than a request per query. There is no server-side text search endpoint;
``namelist`` matches exact names only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

BASE_URL = "https://mods.factorio.com"
API_URL = f"{BASE_URL}/api"
USER_AGENT = "FactorioReforge/0.1 (+https://github.com/)"

#: Dependency prefixes, from the mod portal's info.json convention.
#: Everything not listed here is a plain required dependency.
_OPTIONAL_PREFIXES = ("?", "(?)")
_CONFLICT_PREFIX = "!"
_ORDER_ONLY_PREFIXES = ("~", "+")


class PortalError(Exception):
    pass


class AuthRequired(PortalError):
    """Downloading needs credentials that are not configured."""


@dataclasses.dataclass
class Release:
    version: str
    file_name: str
    download_url: str
    sha1: str
    released_at: str
    factorio_version: str = ""
    dependencies: list[str] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Release":
        info = data.get("info_json") or {}
        return cls(
            version=data.get("version", ""),
            file_name=data.get("file_name", ""),
            download_url=data.get("download_url", ""),
            sha1=data.get("sha1", ""),
            released_at=data.get("released_at", ""),
            factorio_version=info.get("factorio_version", ""),
            dependencies=list(info.get("dependencies") or []),
        )

    def required_dependencies(self) -> list[tuple[str, str]]:
        """The dependencies that must also be installed, as ``(name, spec)``.

        Optional (``?``, ``(?)``) and incompatibility (``!``) entries are
        skipped; ``~`` and ``+`` only affect load order but still have to be
        present, so they are included.
        """
        required: list[tuple[str, str]] = []
        for entry in self.dependencies:
            name, spec = parse_dependency(entry)
            if name is None or name == "base":
                continue
            required.append((name, spec))
        return required


def parse_dependency(entry: str) -> tuple[Optional[str], str]:
    """Split a dependency string into ``(name, version_spec)``.

    Returns ``(None, ...)`` for entries that should not be installed --
    optional ones and incompatibilities.

        "base >= 2.1.7"            -> ("base", ">= 2.1.7")
        "? flib >= 0.16"           -> (None, ...)
        "(?) Aircraft >= 1.6.6"    -> (None, ...)
        "! bobores"                -> (None, ...)
        "~ some-mod"               -> ("some-mod", "")
    """
    text = entry.strip()
    for prefix in _OPTIONAL_PREFIXES:
        if text.startswith(prefix):
            return None, ""
    if text.startswith(_CONFLICT_PREFIX):
        return None, ""
    for prefix in _ORDER_ONLY_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break

    for operator in (">=", "<=", "==", ">", "<", "="):
        if operator in text:
            name, _, spec = text.partition(operator)
            return name.strip(), (operator + spec).strip()
    return text.strip() or None, ""


@dataclasses.dataclass
class ModSummary:
    name: str
    title: str
    owner: str
    summary: str
    downloads_count: int = 0
    category: str = ""
    latest_version: str = ""
    latest_factorio_version: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ModSummary":
        latest = data.get("latest_release") or {}
        return cls(
            name=data.get("name", ""),
            title=data.get("title", ""),
            owner=data.get("owner", ""),
            summary=data.get("summary", ""),
            downloads_count=data.get("downloads_count", 0),
            category=data.get("category", "") or "",
            latest_version=latest.get("version", ""),
            latest_factorio_version=(latest.get("info_json") or {}).get("factorio_version", ""),
        )

    def describe(self) -> str:
        return (
            f"{self.title} ({self.name}) v{self.latest_version} "
            f"by {self.owner} - {self.downloads_count:,} downloads"
        )


class ModPortal:
    """Async wrapper over the portal's HTTP API.

    ``urllib`` runs on a worker thread rather than pulling in aiohttp: these are
    a handful of requests, and keeping the dependency list short matters more
    than shaving milliseconds off a download that is already network-bound.
    """

    def __init__(
        self,
        cache_directory: Path,
        *,
        username: str = "",
        token: str = "",
        index_ttl_hours: float = 6.0,
        timeout: float = 60.0,
        logger: Optional[logging.Logger] = None,
    ):
        self.cache_directory = Path(cache_directory)
        self.username = username
        self.token = token
        self.index_ttl = index_ttl_hours * 3600
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)
        self._index: Optional[list[ModSummary]] = None
        self._index_fetched_at = 0.0
        self._index_lock = asyncio.Lock()

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.token)

    @property
    def index_path(self) -> Path:
        return self.cache_directory / "mod_index.json"

    # -- browsing ------------------------------------------------------------

    async def get_mod(self, name: str, *, full: bool = False) -> dict:
        """Fetch one mod. ``full=True`` is needed for dependencies and changelog."""
        suffix = "/full" if full else ""
        try:
            return await self._get_json(f"{API_URL}/mods/{urllib.parse.quote(name)}{suffix}")
        except PortalError as exc:
            if "404" in str(exc):
                raise PortalError(f"no mod named {name!r} on the portal") from exc
            raise

    async def get_releases(self, name: str) -> list[Release]:
        data = await self.get_mod(name, full=True)
        return [Release.from_dict(entry) for entry in data.get("releases", [])]

    async def get_release(self, name: str, version: Optional[str] = None) -> Release:
        """A specific version, or the newest one when ``version`` is None."""
        releases = await self.get_releases(name)
        if not releases:
            raise PortalError(f"{name} has no published releases")
        if version is None:
            return releases[-1]
        for release in releases:
            if release.version == version:
                return release
        available = ", ".join(r.version for r in releases[-8:])
        raise PortalError(f"{name} has no version {version}. Recent versions: {available}")

    async def latest_for_factorio(self, name: str, factorio_version: str) -> Optional[Release]:
        """Newest release built for a given Factorio major.minor, if any.

        Factorio only loads mods whose ``factorio_version`` matches the running
        major.minor, so picking the newest release outright would often install
        something the server cannot use.
        """
        wanted = _major_minor(factorio_version)
        matching = [
            release
            for release in await self.get_releases(name)
            if _major_minor(release.factorio_version) == wanted
        ]
        return matching[-1] if matching else None

    # -- search --------------------------------------------------------------

    async def search(
        self, query: str, *, limit: int = 10, factorio_version: str = ""
    ) -> list[ModSummary]:
        """Rank mods by how well they match ``query``.

        The portal has no text-search endpoint, so this filters a cached copy of
        the full list. Exact and prefix matches outrank substring hits, and
        download count breaks ties, so typing "krastorio" finds Krastorio2
        rather than a mod that merely mentions it.
        """
        index = await self.get_index()
        needle = query.strip().lower()
        if not needle:
            return []

        wanted = _major_minor(factorio_version) if factorio_version else ""
        scored: list[tuple[tuple, ModSummary]] = []
        for mod in index:
            if wanted and mod.latest_factorio_version and _major_minor(
                mod.latest_factorio_version
            ) != wanted:
                continue
            rank = _match_rank(needle, mod)
            if rank is None:
                continue
            scored.append(((rank, -mod.downloads_count), mod))

        scored.sort(key=lambda pair: pair[0])
        return [mod for _, mod in scored[:limit]]

    async def get_index(self, *, force: bool = False) -> list[ModSummary]:
        """The full mod list, cached on disk and refreshed on a TTL."""
        async with self._index_lock:
            if not force and self._index is not None and self._index_fresh():
                return self._index

            if not force and self._load_index_from_disk():
                return self._index or []

            self.logger.info("Refreshing the mod portal index (this takes a few seconds)")
            data = await self._get_json(f"{API_URL}/mods?page_size=max")
            results = data.get("results", [])
            self._index = [ModSummary.from_dict(entry) for entry in results]
            self._index_fetched_at = time.time()
            self._save_index_to_disk(results)
            self.logger.info("Mod index refreshed: %d mods", len(self._index))
            return self._index

    def _index_fresh(self) -> bool:
        return time.time() - self._index_fetched_at < self.index_ttl

    def _load_index_from_disk(self) -> bool:
        if not self.index_path.is_file():
            return False
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            fetched_at = float(payload.get("fetched_at", 0))
            if time.time() - fetched_at >= self.index_ttl:
                return False
            self._index = [ModSummary.from_dict(entry) for entry in payload.get("results", [])]
            self._index_fetched_at = fetched_at
            return True
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self.logger.warning("Cached mod index is unreadable; refetching")
            return False

    def _save_index_to_disk(self, results: list[dict]) -> None:
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        temp = self.index_path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps({"fetched_at": time.time(), "results": results}), encoding="utf-8"
        )
        temp.replace(self.index_path)

    # -- downloading ---------------------------------------------------------

    async def download(self, release: Release, target: Path) -> Path:
        """Fetch a release to ``target``, verifying its sha1 before committing.

        Downloads into a ``.part`` file and renames on success, so an
        interrupted transfer cannot leave a truncated zip in the mods directory
        where Factorio would try to load it.
        """
        if not self.has_credentials:
            raise AuthRequired(
                "downloading needs a factorio.com username and token. Set them in the "
                "mod_manager config, or let it read them from player-data.json"
            )

        url = (
            f"{BASE_URL}{release.download_url}?"
            + urllib.parse.urlencode({"username": self.username, "token": self.token})
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".part")

        try:
            await asyncio.to_thread(self._download_sync, url, temp)
            digest = await asyncio.to_thread(_sha1_of, temp)
            if release.sha1 and digest != release.sha1:
                raise PortalError(
                    f"{release.file_name} failed its checksum "
                    f"(expected {release.sha1}, got {digest}); it was not installed"
                )
            temp.replace(target)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        return target

    def _download_sync(self, url: str, target: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                with target.open("wb") as handle:
                    while chunk := response.read(1 << 16):
                        handle.write(chunk)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # Never echo the URL back: it carries the token.
                raise AuthRequired(
                    "the mod portal rejected the credentials (HTTP %d). Check the "
                    "username and token" % exc.code
                ) from exc
            raise PortalError(f"download failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise PortalError(f"download failed: {exc.reason}") from exc

    # -- transport -----------------------------------------------------------

    async def _get_json(self, url: str) -> dict:
        return await asyncio.to_thread(self._get_json_sync, url)

    def _get_json_sync(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise PortalError(f"portal request failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise PortalError(f"cannot reach the mod portal: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise PortalError(f"the portal returned something that was not JSON: {exc}") from exc


def read_player_data_credentials(path: Path) -> tuple[str, str]:
    """Pull username and token out of a Factorio ``player-data.json``.

    Returns empty strings when the file is absent or has never been logged in,
    so the caller can fall back to explicit configuration.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    return data.get("service-username", "") or "", data.get("service-token", "") or ""


def _match_rank(needle: str, mod: ModSummary) -> Optional[int]:
    """Lower is better; None means no match at all."""
    name = mod.name.lower()
    title = mod.title.lower()
    if name == needle or title == needle:
        return 0
    if name.startswith(needle) or title.startswith(needle):
        return 1
    if needle in name or needle in title:
        return 2
    if needle in (mod.summary or "").lower():
        return 3
    return None


def _major_minor(version: str) -> str:
    parts = (version or "").split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else (version or "")


def _sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()
