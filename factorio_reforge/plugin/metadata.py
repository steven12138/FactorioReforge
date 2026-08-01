"""Plugin identity and dependency declarations."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

ID_PATTERN = re.compile(r"[a-z0-9_]{1,64}")
_VERSION_PART = re.compile(r"(\d+)")


class MetadataError(Exception):
    pass


@dataclasses.dataclass
class Metadata:
    id: str
    version: str = "0.0.0"
    name: str = ""
    description: str = ""
    author: str = ""
    link: str = ""
    #: plugin_id -> requirement string, e.g. ``{'save_guard': '>=1.0.0'}``.
    #: ``factorio_reforge`` is accepted as a pseudo-plugin for the core version.
    dependencies: dict[str, str] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not ID_PATTERN.fullmatch(self.id):
            raise MetadataError(
                f"invalid plugin id {self.id!r}: use lowercase letters, digits and underscores"
            )
        if not self.name:
            self.name = self.id

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, fallback_id: str | None = None) -> Metadata:
        if not isinstance(data, dict):
            raise MetadataError("PLUGIN_METADATA must be a dict")
        plugin_id = data.get("id", fallback_id)
        if not plugin_id:
            raise MetadataError("PLUGIN_METADATA is missing 'id'")
        known = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["id"] = plugin_id
        deps = kwargs.get("dependencies") or {}
        if not isinstance(deps, dict):
            raise MetadataError("'dependencies' must be a dict of plugin_id -> requirement")
        kwargs["dependencies"] = {str(k): str(v) for k, v in deps.items()}
        return cls(**kwargs)

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


def parse_version(version: str) -> tuple[int, ...]:
    """Loose numeric version tuple; non-numeric suffixes are ignored."""
    parts = _VERSION_PART.findall(version)
    return tuple(int(p) for p in parts) or (0,)


def satisfies(version: str, requirement: str) -> bool:
    """Check ``version`` against a requirement like ``>=1.2.0`` or ``*``.

    Supports ``* >= > <= < == !=`` and comma-separated conjunctions -- enough for
    plugin ordering without pulling in a full version-spec library.
    """
    requirement = (requirement or "*").strip()
    if requirement in ("", "*"):
        return True

    actual = parse_version(version)
    for clause in requirement.split(","):
        clause = clause.strip()
        if not clause:
            continue
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if clause.startswith(op):
                wanted = parse_version(clause[len(op):].strip())
                left, right = _align(actual, wanted)
                if not _COMPARE[op](left, right):
                    return False
                break
        else:
            if _align(actual, parse_version(clause)) [0] != _align(actual, parse_version(clause))[1]:
                return False
    return True


def _align(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)), b + (0,) * (size - len(b))


_COMPARE = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}
