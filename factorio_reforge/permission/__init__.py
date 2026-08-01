"""Five-level permission model, persisted to permission.yml.

Mirrors MCDReforged's scheme so the mental model carries over: levels are
ordered, a command declares a minimum, and the console is always at the top
because whoever holds the terminal already controls the process.
"""

from __future__ import annotations

import enum
import threading
from pathlib import Path

import yaml


class PermissionLevel(enum.IntEnum):
    GUEST = 0
    USER = 1
    HELPER = 2
    ADMIN = 3
    OWNER = 4

    @classmethod
    def parse(cls, value: str | int | PermissionLevel) -> PermissionLevel:
        if isinstance(value, PermissionLevel):
            return value
        if isinstance(value, int):
            if value not in cls._value2member_map_:
                raise ValueError(f"no such permission level: {value}")
            return cls(value)
        name = str(value).strip().upper()
        if name.isdigit():
            return cls.parse(int(name))
        if name not in cls.__members__:
            raise ValueError(f"no such permission level: {value!r}")
        return cls[name]

    @property
    def label(self) -> str:
        return self.name.lower()


CONSOLE_LEVEL = PermissionLevel.OWNER


class PermissionManager:
    """Player-name to level mapping, backed by a YAML file.

    Names are matched case-insensitively: Factorio usernames are unique
    case-insensitively, and operators type them by hand.
    """

    def __init__(self, path: Path, default_level: str | PermissionLevel = PermissionLevel.USER):
        self.path = path
        self.default_level = PermissionLevel.parse(default_level)
        self._levels: dict[str, PermissionLevel] = {}
        self._lock = threading.Lock()

    def load(self) -> None:
        if not self.path.is_file():
            self.save()
            return
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        levels: dict[str, PermissionLevel] = {}
        for level_name, players in (data.get("levels") or {}).items():
            level = PermissionLevel.parse(level_name)
            for player in players or []:
                levels[str(player).lower()] = level
        with self._lock:
            self._levels = levels
            if (default := data.get("default_level")) is not None:
                self.default_level = PermissionLevel.parse(default)

    def save(self) -> None:
        with self._lock:
            buckets: dict[str, list[str]] = {level.label: [] for level in PermissionLevel}
            for player, level in sorted(self._levels.items()):
                buckets[level.label].append(player)
            payload = {"default_level": self.default_level.label, "levels": buckets}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

    def get(self, player: str | None) -> PermissionLevel:
        if player is None:
            return CONSOLE_LEVEL
        with self._lock:
            return self._levels.get(player.lower(), self.default_level)

    def set(self, player: str, level: str | int | PermissionLevel) -> PermissionLevel:
        parsed = PermissionLevel.parse(level)
        with self._lock:
            self._levels[player.lower()] = parsed
        self.save()
        return parsed

    def remove(self, player: str) -> bool:
        with self._lock:
            existed = self._levels.pop(player.lower(), None) is not None
        if existed:
            self.save()
        return existed

    def all(self) -> dict[str, PermissionLevel]:
        with self._lock:
            return dict(self._levels)
