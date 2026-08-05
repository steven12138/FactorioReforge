"""Everything that must be true before a version swap, decided before the stop.

Kept as pure functions over already-gathered facts so the answer can be
rehearsed in a test rather than on a server. The plugin does the reading; this
does the judging.

The interesting asymmetry is that **upgrades are cheap and downgrades are
not**. Factorio migrates an older save forward on load, so going up is
ordinary. Going down is not a swap at all: the binary cannot open a world its
successor has written, so it has to be a binary change *and* a restore, done
together. Findings say so rather than just refusing.
"""

from __future__ import annotations

import dataclasses
import json
import zipfile
from pathlib import Path

from factorio_reforge.versions.binary import BinaryInfo
from factorio_reforge.versions.savefile import MapVersion

#: Shipped inside ``data/`` and versioned in lockstep with the binary --
#: measured: ``data/space-age/info.json`` on a 2.0.77 tree reads
#: ``"version": "2.0.77"``. They therefore move with a version swap and never
#: need checking, which is the reason the expansion is not a hazard here.
BUNDLED_MODS = frozenset({"base", "core", "space-age", "quality", "elevated-rails"})

BLOCK = "block"
WARN = "warn"
NOTE = "note"


@dataclasses.dataclass(frozen=True)
class Finding:
    """One thing worth saying, with the placeholders its message needs."""

    key: str
    severity: str = BLOCK
    values: dict = dataclasses.field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == BLOCK


@dataclasses.dataclass(frozen=True)
class ModCompat:
    name: str
    version: str
    series: str


def read_mod_series(mods_directory: Path) -> list[ModCompat]:
    """What each installed mod zip says it was built for.

    ``factorio_version`` in a mod's ``info.json`` is ``major.minor`` -- ``2.0``,
    never ``2.0.77`` -- so a patch upgrade can never invalidate a mod and a
    ``2.0`` to ``2.1`` move invalidates all of them at once.
    """
    if not mods_directory.is_dir():
        return []
    found: list[ModCompat] = []
    for path in sorted(mods_directory.glob("*.zip")):
        info = _info_json(path)
        if not info:
            continue
        name = info.get("name") or path.stem
        if name in BUNDLED_MODS:
            continue
        found.append(
            ModCompat(
                name=name,
                version=str(info.get("version", "")),
                series=str(info.get("factorio_version", "")),
            )
        )
    return found


def check_switch(
    *,
    target: BinaryInfo,
    current_release: str | None,
    save: MapVersion | None,
    save_error: str = "",
    mods: list[ModCompat] | None = None,
    online: list[str] | None = None,
    paired_save: MapVersion | None = None,
    paired_slot: str = "",
    paired_error: str = "",
) -> list[Finding]:
    """Findings for switching to ``target``, worst first.

    ``save`` is the live world; ``paired_save`` is the world a downgrade would
    put in its place. Pass ``save=None`` with ``save_error`` set when the world
    could not be read -- that is a blocker, not a shrug: a swap authorised
    without knowing what the world is, is a swap made on hope.
    """
    findings: list[Finding] = []
    mods = mods or []
    online = online or []

    if current_release == target.release:
        findings.append(Finding("same_version", BLOCK, {"version": target.release}))

    world = paired_save if paired_save is not None else save
    if paired_slot and paired_save is None:
        findings.append(
            Finding("paired_unreadable", BLOCK, {"slot": paired_slot, "error": paired_error})
        )
    elif world is None:
        findings.append(Finding("save_unreadable", BLOCK, {"error": save_error}))
    elif world > target.map_output:
        findings.append(Finding(
            "save_too_new", BLOCK,
            {"save": str(world), "version": target.release, "max": str(target.map_output)},
        ))
    elif world < target.map_input:
        findings.append(Finding(
            "save_too_old", BLOCK,
            {"save": str(world), "version": target.release, "min": str(target.map_input)},
        ))

    stale = [mod for mod in mods if mod.series and mod.series != target.version.series]
    if stale:
        findings.append(Finding(
            "mods_wrong_series", BLOCK,
            {
                "count": len(stale),
                "series": target.version.series,
                "mods": ", ".join(f"{mod.name} ({mod.series})" for mod in stale[:5]),
            },
        ))

    if paired_slot and paired_save is not None:
        findings.append(Finding(
            "world_replaced", WARN,
            {"slot": paired_slot, "save": str(paired_save)},
        ))
    elif save is not None and current_release and _is_downgrade(current_release, target.release):
        findings.append(Finding("downgrade", WARN, {"version": target.release}))

    if online:
        findings.append(Finding(
            "players_online", WARN,
            {"count": len(online), "players": ", ".join(online[:8]),
             "version": target.release},
        ))

    findings.append(Finding("clients_must_match", NOTE, {"version": target.release}))
    return sorted(findings, key=lambda f: (BLOCK, WARN, NOTE).index(f.severity))


def blockers(findings: list[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.blocking]


def _is_downgrade(current: str, target: str) -> bool:
    return _tuple(target) < _tuple(current)


def _tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in version.replace("-", ".").split("."))


def _info_json(path: Path) -> dict | None:
    try:
        with zipfile.ZipFile(path) as archive:
            for entry in archive.namelist():
                if entry.count("/") == 1 and entry.endswith("/info.json"):
                    return json.loads(archive.read(entry).decode("utf-8"))
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, KeyError):
        return None
    return None
