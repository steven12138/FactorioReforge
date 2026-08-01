"""The server's local mods directory and its ``mod-list.json``.

Factorio reads mods once, at startup. Nothing here takes effect until the server
restarts, so every mutating operation says so rather than letting an operator
believe a mod is live when it is not.

The other thing worth stating up front: **changing the mod set changes what
clients need**. Players whose mods do not match the server cannot connect, so
installing a mod on a running public server is a disruptive act, not a quiet one.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Iterable, Optional

from factorio_reforge.mods.portal import ModPortal, PortalError, Release

MOD_LIST_FILE = "mod-list.json"
INTENT_FILE = ".reforge-mod-state.json"
#: Mods that ship with the game and must never be touched.
BUILTIN_MODS = frozenset({"base", "elevated-rails", "quality", "space-age"})
_FILENAME = re.compile(r"^(?P<name>.+)_(?P<version>\d+\.\d+\.\d+)\.zip$")


class ModError(Exception):
    pass


@dataclasses.dataclass
class InstalledMod:
    name: str
    version: str
    enabled: bool
    file_name: str = ""
    builtin: bool = False

    def describe(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        version = f" v{self.version}" if self.version else ""
        tag = " [builtin]" if self.builtin else ""
        return f"{self.name}{version} ({state}){tag}"


class ModManager:
    def __init__(
        self,
        mods_directory: Path,
        portal: ModPortal,
        *,
        logger: Optional[logging.Logger] = None,
    ):
        self.mods_directory = Path(mods_directory)
        self.portal = portal
        self.logger = logger or logging.getLogger(__name__)
        self._lock = asyncio.Lock()

    @property
    def mod_list_path(self) -> Path:
        return self.mods_directory / MOD_LIST_FILE

    # -- reading -------------------------------------------------------------

    def list_installed(self) -> list[InstalledMod]:
        """Merge the zips on disk with the enable/disable state in mod-list.json.

        A mod can appear in one and not the other: a zip nobody enabled, or an
        entry left behind after a file was deleted. Both are reported rather
        than hidden, because both are things an operator needs to see.
        """
        enabled_state = self._read_mod_list()
        versions = self._scan_zips()

        names = set(enabled_state) | set(versions) | BUILTIN_MODS
        mods: list[InstalledMod] = []
        for name in sorted(names):
            version, file_name = versions.get(name, ("", ""))
            mods.append(
                InstalledMod(
                    name=name,
                    version=version,
                    enabled=enabled_state.get(name, True),
                    file_name=file_name,
                    builtin=name in BUILTIN_MODS,
                )
            )
        return mods

    def get_installed(self, name: str) -> Optional[InstalledMod]:
        return next((mod for mod in self.list_installed() if mod.name == name), None)

    def _scan_zips(self) -> dict[str, tuple[str, str]]:
        found: dict[str, tuple[str, str]] = {}
        if not self.mods_directory.is_dir():
            return found
        for path in self.mods_directory.glob("*.zip"):
            match = _FILENAME.match(path.name)
            if match:
                found[match.group("name")] = (match.group("version"), path.name)
            else:
                # Fall back to the zip's own info.json for oddly named files.
                info = _read_info_json(path)
                if info:
                    found[info.get("name", path.stem)] = (info.get("version", ""), path.name)
        return found

    def _read_mod_list(self) -> dict[str, bool]:
        if not self.mod_list_path.is_file():
            return {}
        try:
            data = json.loads(self.mod_list_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModError(f"{self.mod_list_path} is unreadable: {exc}") from exc
        return {
            entry["name"]: bool(entry.get("enabled", True))
            for entry in data.get("mods", [])
            if isinstance(entry, dict) and "name" in entry
        }

    def _write_mod_list(self, state: dict[str, bool]) -> None:
        """Rewrite mod-list.json, keeping ``base`` first as Factorio writes it.

        Also records the intent separately -- see :meth:`reapply_intent` for why
        writing this file alone is not enough while the server is running.
        """
        ordered = ["base"] + sorted(name for name in state if name != "base")
        payload = {
            "mods": [{"name": name, "enabled": state.get(name, True)} for name in ordered]
        }
        self.mods_directory.mkdir(parents=True, exist_ok=True)
        temp = self.mod_list_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(self.mod_list_path)
        self._save_intent(state)

    # -- surviving Factorio's own writes -------------------------------------

    @property
    def intent_path(self) -> Path:
        return self.mods_directory / INTENT_FILE

    def _save_intent(self, state: dict[str, bool]) -> None:
        """Remember what we asked for, independently of mod-list.json.

        A running Factorio holds the mod list in memory and rewrites the file on
        exit, silently discarding anything changed underneath it -- measured on
        2.0.77: a mod installed and enabled mid-session was gone from
        mod-list.json after the server stopped. Keeping our own copy lets
        :meth:`reapply_intent` put it back once the server is no longer holding
        the file.
        """
        temp = self.intent_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"mods": state}, indent=2), encoding="utf-8")
        temp.replace(self.intent_path)

    def _load_intent(self) -> dict[str, bool]:
        if not self.intent_path.is_file():
            return {}
        try:
            data = json.loads(self.intent_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        mods = data.get("mods")
        return {str(k): bool(v) for k, v in mods.items()} if isinstance(mods, dict) else {}

    def reapply_intent(self) -> list[str]:
        """Re-write mod-list.json from our recorded intent. Returns what changed.

        Call this when the server is **stopped**. Doing it while Factorio runs
        is pointless: it will overwrite the file again on exit.
        """
        intent = self._load_intent()
        if not intent:
            return []
        current = self._read_mod_list()
        installed = set(self._scan_zips())

        changed: list[str] = []
        merged = dict(current)
        for name, enabled in intent.items():
            # Drop entries for mods whose zip is gone, so a removed mod does not
            # come back as a phantom entry forever.
            if name not in installed and name not in BUILTIN_MODS:
                if name in merged:
                    del merged[name]
                    changed.append(f"-{name}")
                continue
            if merged.get(name) != enabled:
                merged[name] = enabled
                changed.append(f"{'+' if enabled else '~'}{name}")

        if changed:
            self.logger.info("Restoring mod list after Factorio rewrote it: %s", " ".join(changed))
            ordered = ["base"] + sorted(n for n in merged if n != "base")
            payload = {
                "mods": [{"name": n, "enabled": merged.get(n, True)} for n in ordered]
            }
            temp = self.mod_list_path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp.replace(self.mod_list_path)
            self._save_intent(merged)
        return changed

    # -- installing ----------------------------------------------------------

    async def install(
        self,
        name: str,
        *,
        version: Optional[str] = None,
        factorio_version: str = "",
        with_dependencies: bool = True,
        _seen: Optional[set[str]] = None,
    ) -> list[InstalledMod]:
        """Download a mod and enable it. Returns everything that got installed.

        Required dependencies are pulled in too; optional ones are not, since
        installing every ``(?)`` entry on a large mod would drag in dozens of
        unrelated mods nobody asked for.
        """
        async with self._lock:
            return await self._install_locked(
                name, version, factorio_version, with_dependencies, _seen or set()
            )

    async def _install_locked(
        self,
        name: str,
        version: Optional[str],
        factorio_version: str,
        with_dependencies: bool,
        seen: set[str],
    ) -> list[InstalledMod]:
        if name in BUILTIN_MODS:
            raise ModError(f"{name} ships with the game; it cannot be installed separately")
        if name in seen:
            return []
        seen.add(name)

        release = await self._pick_release(name, version, factorio_version)
        installed: list[InstalledMod] = []

        if with_dependencies:
            for dep_name, _spec in release.required_dependencies():
                if dep_name in seen or dep_name in BUILTIN_MODS:
                    continue
                if self.get_installed(dep_name) and self._has_zip(dep_name):
                    continue
                self.logger.info("Installing %s because %s requires it", dep_name, name)
                try:
                    installed.extend(
                        await self._install_locked(
                            dep_name, None, factorio_version, True, seen
                        )
                    )
                except PortalError as exc:
                    # A missing optional-looking dependency should not sink the
                    # whole install; report it and carry on.
                    self.logger.warning("Could not install dependency %s: %s", dep_name, exc)

        target = self.mods_directory / release.file_name
        self.logger.info("Downloading %s v%s", name, release.version)
        await self.portal.download(release, target)

        self._remove_other_versions(name, keep=release.file_name)
        state = self._read_mod_list()
        state[name] = True
        self._write_mod_list(state)

        mod = InstalledMod(
            name=name, version=release.version, enabled=True, file_name=release.file_name
        )
        installed.append(mod)
        self.logger.info("Installed %s", mod.describe())
        return installed

    async def _pick_release(
        self, name: str, version: Optional[str], factorio_version: str
    ) -> Release:
        if version is not None:
            return await self.portal.get_release(name, version)
        if factorio_version:
            release = await self.portal.latest_for_factorio(name, factorio_version)
            if release is not None:
                return release
            # Say what happened rather than silently installing a release the
            # server will refuse to load.
            newest = await self.portal.get_release(name)
            raise ModError(
                f"{name} has no release for Factorio {factorio_version}; its newest is "
                f"v{newest.version} for {newest.factorio_version}. "
                f"Install it explicitly with a version if you are sure."
            )
        return await self.portal.get_release(name)

    def _has_zip(self, name: str) -> bool:
        return name in self._scan_zips()

    def _remove_other_versions(self, name: str, *, keep: str) -> None:
        """One version per mod: Factorio errors out on duplicates."""
        for path in self.mods_directory.glob(f"{name}_*.zip"):
            match = _FILENAME.match(path.name)
            if match and match.group("name") == name and path.name != keep:
                self.logger.info("Removing the older %s", path.name)
                path.unlink(missing_ok=True)

    # -- removing and toggling -----------------------------------------------

    async def remove(self, name: str) -> bool:
        async with self._lock:
            if name in BUILTIN_MODS:
                raise ModError(f"{name} ships with the game and cannot be removed")

            removed = False
            for path in self.mods_directory.glob(f"{name}_*.zip"):
                match = _FILENAME.match(path.name)
                if match and match.group("name") == name:
                    path.unlink(missing_ok=True)
                    removed = True

            state = self._read_mod_list()
            if state.pop(name, None) is not None:
                self._write_mod_list(state)
                removed = True
            if removed:
                self.logger.info("Removed mod %s", name)
            return removed

    async def set_enabled(self, name: str, enabled: bool) -> bool:
        async with self._lock:
            if name == "base":
                raise ModError("the base mod cannot be disabled")
            state = self._read_mod_list()
            if name not in state and not self._has_zip(name) and name not in BUILTIN_MODS:
                return False
            state[name] = enabled
            self._write_mod_list(state)
            self.logger.info("%s %s", "Enabled" if enabled else "Disabled", name)
            return True

    # -- updating ------------------------------------------------------------

    async def check_updates(self, factorio_version: str = "") -> list[tuple[InstalledMod, Release]]:
        """Which installed mods have a newer release available."""
        updates: list[tuple[InstalledMod, Release]] = []
        for mod in self.list_installed():
            if mod.builtin or not mod.version:
                continue
            try:
                if factorio_version:
                    release = await self.portal.latest_for_factorio(mod.name, factorio_version)
                else:
                    release = await self.portal.get_release(mod.name)
            except PortalError as exc:
                self.logger.debug("Could not check %s for updates: %s", mod.name, exc)
                continue
            if release is not None and _newer(release.version, mod.version):
                updates.append((mod, release))
        return updates


def _read_info_json(path: Path) -> Optional[dict]:
    try:
        with zipfile.ZipFile(path) as archive:
            for entry in archive.namelist():
                if entry.count("/") == 1 and entry.endswith("/info.json"):
                    return json.loads(archive.read(entry).decode("utf-8"))
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, KeyError):
        return None
    return None


def _newer(candidate: str, current: str) -> bool:
    return _version_tuple(candidate) > _version_tuple(current)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in (version or "0").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)
