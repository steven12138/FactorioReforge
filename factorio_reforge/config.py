"""config.yml loading, with defaults written out on first run."""

from __future__ import annotations

import dataclasses
import shlex
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILE = "config.yml"


class ConfigError(Exception):
    pass


#: Retired-key notices found while parsing, replayed by :meth:`Config.warnings`.
_PENDING_WARNINGS: list[tuple[str, str, str]] = []


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

    #: The same, for the ring automatic backups use. Kept separate so a timer
    #: running every half hour cannot walk a manual backup off the end of the
    #: list overnight -- the backup someone took before a risky change is
    #: exactly the one a schedule would otherwise push out. Its length is the
    #: number of automatic slots; no protection, because nothing in this ring
    #: was asked for by a person.
    auto_slot_protection: list[int] = dataclasses.field(
        default_factory=lambda: [0, 0, 0, 0, 0]
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
        # --rcon-bind, not --rcon-port: the latter listens on 0.0.0.0, and the
        # RCON protocol is plaintext, so anyone who reaches the port owns the
        # server.
        "--port 34197 --rcon-bind 127.0.0.1:27015 --rcon-password CHANGE_ME"
    )
    handler: str = "factorio"
    encoding: str = "utf-8"

    #: Language for everything a person reads. Anything missing falls back to
    #: English, so a partial translation stays usable. Ships with en and zh_cn;
    #: drop a <code>.yml</code> into lang/ to add another.
    language: str = "en"

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

    #: "auto" colours only when stdout is a terminal that wants it, so piping
    #: into grep or a log collector stays clean. "always" / "never" override.
    colour: str = "auto"

    rcon: RconConfig = dataclasses.field(default_factory=RconConfig)
    saves: SavesConfig = dataclasses.field(default_factory=SavesConfig)

    #: Absolute path of the directory config.yml lives in; every relative path
    #: above is resolved against it so the program can be launched from anywhere.
    root: Path = dataclasses.field(default=Path("."), compare=False)

    #: ``(section, key, reason)`` for settings that no longer exist. Reported
    #: once a translator is available rather than at parse time.
    pending_warnings: list[tuple[str, str, str]] = dataclasses.field(
        default_factory=list, compare=False, repr=False
    )

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

        _PENDING_WARNINGS.clear()
        cfg = cls._from_dict(data)
        cfg.pending_warnings = list(_PENDING_WARNINGS)
        _PENDING_WARNINGS.clear()
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

        self._check_rcon_exposure()

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

    def set_language(self, language: str, path: Path) -> None:
        """Persist a language change by editing only that one line.

        Not :meth:`dump`: regenerating the whole file would discard any comments
        or ordering the operator put there. Rewriting a single key keeps the
        rest of their file exactly as they left it.
        """
        self.language = language
        if not path.is_file():
            return

        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.startswith("language:"):
                lines[index] = f"language: {language}\n"
                break
        else:
            # Absent because the file predates the setting; add it near the top
            # rather than at the end, where it would sit under a nested block.
            insert_at = next(
                (i + 1 for i, line in enumerate(lines) if line.startswith("encoding:")),
                len(lines),
            )
            lines.insert(insert_at, f"language: {language}\n")

        temp = path.with_suffix(".yml.tmp")
        temp.write_text("".join(lines), encoding="utf-8")
        temp.replace(path)

    def _check_rcon_exposure(self) -> None:
        """Refuse a start command that puts RCON on a public interface.

        RCON is plaintext and unauthenticated beyond one password, so a
        reachable port is a remote shell for the server. ``--rcon-port`` binds
        every interface; ``--rcon-bind`` takes an address. This is an error
        rather than a warning because it is silent, easy to get wrong, and not
        something anyone means to do.
        """
        argv = self.command_argv
        if "--rcon-bind" in argv:
            host = _host_of(argv[argv.index("--rcon-bind") + 1])
            if host not in ("127.0.0.1", "localhost", "::1"):
                raise ConfigError(
                    f"start_command binds RCON to {host!r}, which is reachable from "
                    "outside this machine. RCON is plaintext, so anyone who can "
                    "reach the port controls the server. Use "
                    "--rcon-bind 127.0.0.1:<port>, and reach it from elsewhere "
                    "through SSH or the Telegram bridge."
                )
        elif "--rcon-port" in argv:
            port = argv[argv.index("--rcon-port") + 1]
            raise ConfigError(
                "start_command uses --rcon-port, which listens on every interface. "
                "RCON is plaintext, so that exposes control of the server to the "
                f"network. Use --rcon-bind 127.0.0.1:{port} instead."
            )

    def dump(self, path: Path) -> None:
        data = {
            f.name: _plain(getattr(self, f.name))
            for f in dataclasses.fields(self)
            if f.name not in ("root", "pending_warnings")
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )


#: Keys that used to be valid. Failing on these would break every config.yml
#: written by an older version, so they are dropped with a note instead --
#: while a genuine typo still errors, which is the point of checking at all.
#: The reason is a translation key, resolved once a translator exists.
RETIRED_KEYS: dict[str, dict[str, str]] = {
    "saves": {
        "max_snapshots": "retired.max_snapshots",
        "max_snapshot_age_days": "retired.max_snapshot_age_days",
    },
}


def _host_of(address: str) -> str:
    """The host part of ``host``, ``host:port`` or ``[v6]:port``.

    A bare IPv6 literal has colons of its own, so splitting on the last one
    would turn ``::1`` into ``:``.
    """
    address = address.strip()
    if address.startswith("["):
        return address[1:].split("]", 1)[0]
    if address.count(":") > 1:
        return address
    return address.rsplit(":", 1)[0] if ":" in address else address


def _sub(klass, value: dict | None, name: str):
    if value is None:
        return klass()
    if not isinstance(value, dict):
        raise ConfigError(f"config key {name!r} must be a mapping")

    value = dict(value)
    for key, reason in RETIRED_KEYS.get(name, {}).items():
        if value.pop(key, None) is not None:
            # Collected rather than logged: config is parsed before the logger
            # and the translator exist, so these are replayed once they do.
            _PENDING_WARNINGS.append((name, key, reason))

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
