"""config.yml loading, with defaults written out on first run."""

from __future__ import annotations

import dataclasses
import logging
import shlex
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILE = "config.yml"


class ConfigError(Exception):
    pass


@dataclasses.dataclass
class RconConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 27015
    password: str = ""
    connect_timeout: float = 5.0
    retry_interval: float = 3.0


@dataclasses.dataclass
class SavesConfig:
    #: Directory Factorio itself loads from, i.e. ``<server>/saves``.
    save_directory: str = "server/factorio/saves"
    #: The one save the server is launched with. Restoring overwrites this file,
    #: which is why the launch command must name it explicitly rather than using
    #: --start-server-load-latest.
    current_save: str = "server/factorio/saves/reforge.zip"
    snapshot_directory: str = "snapshots"

    #: Delete protection per slot, in seconds, following QuickBackupM. A backup
    #: always goes to slot 1 and pushes the rest down; the slot sacrificed to
    #: make room is the first empty one, or the highest-numbered slot past its
    #: protection. The last two entries keep a few hours and a few days of
    #: history safe from a burst of backups. The length of this list is the
    #: number of slots.
    slot_protection: list[int] = dataclasses.field(
        default_factory=lambda: [0, 0, 0, 3 * 60 * 60, 3 * 24 * 60 * 60]
    )

    #: Seconds to wait for the server to confirm it finished writing a save.
    save_timeout: float = 120.0
    #: Seconds of in-game countdown before a restore actually happens.
    restore_countdown: float = 10.0


@dataclasses.dataclass
class Config:
    working_directory: str = "server/factorio"
    start_command: str = (
        "./bin/x64/factorio --start-server ./saves/reforge.zip "
        "--server-settings ./server-settings.json "
        "--server-adminlist ./server-adminlist.json "
        "--server-banlist ./server-banlist.json "
        "--port 34197 --rcon-port 27015 --rcon-password CHANGE_ME"
    )
    handler: str = "factorio"
    encoding: str = "utf-8"

    plugin_directories: list[str] = dataclasses.field(default_factory=lambda: ["plugins"])
    command_prefix: str = "!!"

    #: Who gets what by default; see factorio_reforge.permission.
    default_permission_level: str = "user"

    quit_timeout: float = 60.0
    sigint_timeout: float = 30.0
    sigterm_timeout: float = 15.0

    #: Restart the server automatically if it dies without us asking it to.
    auto_restart_on_crash: bool = False
    crash_restart_delay: float = 10.0

    log_level: str = "INFO"
    log_directory: str = "logs"

    rcon: RconConfig = dataclasses.field(default_factory=RconConfig)
    saves: SavesConfig = dataclasses.field(default_factory=SavesConfig)

    #: Absolute path of the directory config.yml lives in; every relative path
    #: above is resolved against it so the program can be launched from anywhere.
    root: Path = dataclasses.field(default=Path("."), compare=False)

    # -- derived paths -------------------------------------------------------

    def resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.root / path).resolve()

    @property
    def working_dir_path(self) -> Path:
        return self.resolve(self.working_directory)

    @property
    def command_argv(self) -> list[str]:
        return shlex.split(self.start_command)

    @property
    def plugin_dir_paths(self) -> list[Path]:
        return [self.resolve(d) for d in self.plugin_directories]

    @property
    def snapshot_dir_path(self) -> Path:
        return self.resolve(self.saves.snapshot_directory)

    @property
    def current_save_path(self) -> Path:
        return self.resolve(self.saves.current_save)

    @property
    def save_dir_path(self) -> Path:
        return self.resolve(self.saves.save_directory)

    @property
    def log_dir_path(self) -> Path:
        return self.resolve(self.log_directory)

    # -- io ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Config:
        if not path.is_file():
            raise ConfigError(
                f"{path} not found. Run `python -m factorio_reforge init` to create it."
            )
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path} must contain a mapping at the top level")

        cfg = cls._from_dict(data)
        cfg.root = path.parent.resolve()
        cfg.validate()
        return cfg

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Config:
        known = {f.name for f in dataclasses.fields(cls)} - {"rcon", "saves", "root"}
        unknown = set(data) - known - {"rcon", "saves"}
        if unknown:
            raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")

        kwargs: dict[str, Any] = {k: v for k, v in data.items() if k in known}
        kwargs["rcon"] = _sub(RconConfig, data.get("rcon"), "rcon")
        kwargs["saves"] = _sub(SavesConfig, data.get("saves"), "saves")
        return cls(**kwargs)

    def validate(self) -> None:
        if not self.command_argv:
            raise ConfigError("start_command is empty")
        if not self.working_dir_path.is_dir():
            raise ConfigError(f"working_directory does not exist: {self.working_dir_path}")
        if self.rcon.enabled and not self.rcon.password:
            raise ConfigError("rcon.enabled is true but rcon.password is empty")
        if self.command_prefix.strip() == "":
            raise ConfigError("command_prefix must not be blank")

        # Rollback replaces current_save on disk, so a launch command that does
        # not name that exact file would silently keep loading the old map.
        argv = self.command_argv
        if "--start-server-load-latest" in argv:
            raise ConfigError(
                "start_command uses --start-server-load-latest, which is incompatible "
                "with rollback: after restoring a snapshot Factorio would pick the "
                "newest autosave instead. Use --start-server <path> naming "
                f"{self.saves.current_save} instead."
            )
        if "--start-server" in argv:
            named = argv[argv.index("--start-server") + 1]
            if self.working_dir_path.joinpath(named).resolve() != self.current_save_path:
                raise ConfigError(
                    f"start_command loads {named!r} but saves.current_save points at "
                    f"{self.saves.current_save!r}; rollback would write to a file the "
                    "server never reads. Make them the same file."
                )

    def dump(self, path: Path) -> None:
        data = {
            f.name: _plain(getattr(self, f.name))
            for f in dataclasses.fields(self)
            if f.name != "root"
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )


#: Keys that used to be valid. Failing on these would break every config.yml
#: written by an older version, so they are dropped with a note instead --
#: while a genuine typo still errors, which is the point of checking at all.
RETIRED_KEYS: dict[str, dict[str, str]] = {
    "saves": {
        "max_snapshots": "replaced by saves.slot_protection (its length is the slot count)",
        "max_snapshot_age_days": "replaced by per-slot saves.slot_protection",
    },
}


def _sub(klass, value: dict | None, name: str):
    if value is None:
        return klass()
    if not isinstance(value, dict):
        raise ConfigError(f"config key {name!r} must be a mapping")

    value = dict(value)
    for key, reason in RETIRED_KEYS.get(name, {}).items():
        if value.pop(key, None) is not None:
            logging.getLogger("reforge").warning(
                "config.yml: %s.%s is no longer used -- %s", name, key, reason
            )

    known = {f.name for f in dataclasses.fields(klass)}
    unknown = set(value) - known
    if unknown:
        raise ConfigError(f"unknown keys under {name}: {', '.join(sorted(unknown))}")
    return klass(**value)


def _plain(value):
    if dataclasses.is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Path):
        return str(value)
    return value
