"""Slot-based backups, following QuickBackupM's proven design.

QBM (https://github.com/TISUnion/QuickBackupM) has been in production on
Minecraft servers for years, so its logic is copied rather than reinvented:

* A new backup always lands in **slot 1**; the others shift down one.
* The slot that gets sacrificed to make room is the first empty one, or failing
  that the highest-numbered slot past its ``delete_protection``. If every slot
  is still protected, the backup is refused rather than destroying something
  someone asked to keep.
* Restoring stages a slot, then waits for an explicit confirm, with an
  abortable countdown.
* Before overwriting the world, the current one is copied to a fixed
  ``overwrite`` slot -- QBM's comment on that line reads "backup current world
  to avoid idiot", and it is the single most valuable behaviour here.

Two things differ, both because Factorio offers something Minecraft does not:

* Minecraft needs ``save-off`` / ``save-all flush`` and then a directory copy,
  because the world is a live directory. Factorio's ``/server-save <name>``
  writes a **separate, complete** save file and leaves the live one untouched,
  so the backup is written straight into its slot with no copy and no need to
  suspend autosaving.
* A world is one ``.zip``, not a directory tree, so a slot holds ``save.zip``
  plus ``info.json`` instead of a copied world folder.

Layout::

    snapshots/
        slot1/  save.zip  info.json
        slot2/  ...
        ...
        overwrite/  save.zip  info.json
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import shutil
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

SAVE_NAME = "save.zip"
INFO_NAME = "info.json"
OVERWRITE_SLOT = "overwrite"
_SAFE = "-_.() "


class SaveError(Exception):
    pass


class NoSlotAvailable(SaveError):
    """Every slot is still within its delete protection window."""


@dataclasses.dataclass
class SlotConfig:
    #: Seconds during which this slot may not be sacrificed to make room.
    delete_protection: int = 0


@dataclasses.dataclass
class Slot:
    """One backup. ``id`` is the slot number, so it changes as slots shift."""

    id: int
    comment: str = ""
    created_at: float = 0.0
    created_by: str = "unknown"
    players_online: list[str] = dataclasses.field(default_factory=list)
    size_bytes: int = 0
    automatic: bool = False

    @property
    def created_at_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at))

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise the data fields only.

        Not ``dataclasses.asdict``: that deep-copies every field, and ``_tr``
        holds a bound method of the running server, so copying it drags in the
        whole object graph and dies on an asyncio Future. Popping the key
        afterwards is too late -- the copy has already happened.
        """
        return {
            field.name: getattr(self, field.name)
            for field in dataclasses.fields(self)
            if not field.name.startswith("_")
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], slot_id: int) -> Slot:
        known = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["id"] = slot_id
        return cls(**kwargs)

    #: Set by SaveManager so a slot can describe itself in the operator's
    #: language. A dataclass carried through plugin code is the wrong place to
    #: reach for global state, so it is injected instead.
    _tr: Callable[..., str] | None = dataclasses.field(
        default=None, repr=False, compare=False
    )

    def describe(self) -> str:
        if self._tr is not None:
            return self._tr(
                "save.slot_describe",
                slot=self.id,
                time=self.created_at_text,
                by=self.created_by,
                size=f"{self.size_bytes / (1024 * 1024):.1f}",
                comment=self.comment or self._tr("common.empty"),
            )
        who = f" by {self.created_by}" if self.created_by != "unknown" else ""
        note = f" - {self.comment}" if self.comment else ""
        size = f" ({self.size_bytes / (1024 * 1024):.1f} MiB)" if self.size_bytes else ""
        return f"slot {self.id}: {self.created_at_text}{who}{size}{note}"


class SaveManager:
    def __init__(
        self,
        current_save: Path,
        snapshot_directory: Path,
        *,
        slots: list[SlotConfig] | None = None,
        logger: logging.Logger | None = None,
        tr: Callable[..., str] | None = None,
    ):
        self.tr = tr
        self.current_save = Path(current_save)
        self.snapshot_directory = Path(snapshot_directory)
        #: QBM's defaults: the two oldest slots are protected so a burst of
        #: backups cannot wipe out yesterday's known-good world.
        self.slots = slots or [
            SlotConfig(0),
            SlotConfig(0),
            SlotConfig(0),
            SlotConfig(3 * 60 * 60),
            SlotConfig(3 * 24 * 60 * 60),
        ]
        self.logger = logger or logging.getLogger(__name__)
        # One backup or restore at a time; QBM calls this single_op.
        self._lock = asyncio.Lock()

    def _log(self, key: str, **kwargs) -> str:
        """Translate a log line, falling back to the key when standalone."""
        return self.tr(key, **kwargs) if self.tr else key

    # -- paths ---------------------------------------------------------------

    @property
    def slot_count(self) -> int:
        return len(self.slots)

    def slot_path(self, slot: int | str) -> Path:
        name = slot if isinstance(slot, str) else f"slot{slot}"
        return self.snapshot_directory / name

    def save_path(self, slot: int | str) -> Path:
        return self.slot_path(slot) / SAVE_NAME

    def info_path(self, slot: int | str) -> Path:
        return self.slot_path(slot) / INFO_NAME

    def ensure_directories(self) -> None:
        self.snapshot_directory.mkdir(parents=True, exist_ok=True)
        for index in range(1, self.slot_count + 1):
            self.slot_path(index).mkdir(exist_ok=True)

    # -- reading -------------------------------------------------------------

    def load_index(self) -> None:
        """Kept for API compatibility; slots are read from disk on demand."""
        self.ensure_directories()

    def get(self, slot: int | str) -> Slot | None:
        """Read one slot, or None if it is empty or unreadable."""
        info_file = self.info_path(slot)
        if not info_file.is_file() or not self.save_path(slot).is_file():
            return None
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.logger.warning("%s is unreadable; treating the slot as empty", info_file)
            return None
        slot_id = slot if isinstance(slot, int) else 0
        info = Slot.from_dict(data, slot_id)
        info._tr = self.tr
        return info

    def list(self) -> list[Slot]:
        """Occupied slots, in slot order -- slot 1 is always the newest."""
        return [s for s in (self.get(i) for i in range(1, self.slot_count + 1)) if s]

    def all_slots(self) -> list[tuple[int, Slot | None]]:
        """Every slot including the empty ones, for listing."""
        return [(i, self.get(i)) for i in range(1, self.slot_count + 1)]

    def protection_of(self, slot: int) -> int:
        if 1 <= slot <= self.slot_count:
            return self.slots[slot - 1].delete_protection
        return 0

    def is_protected(self, slot: int) -> bool:
        info = self.get(slot)
        if info is None:
            return False
        return info.age_seconds <= self.protection_of(slot)

    def validate(self, slot: int) -> Slot:
        """Resolve a user-supplied slot number, with a message they can act on."""
        if not 1 <= slot <= self.slot_count:
            raise SaveError(f"slot must be between 1 and {self.slot_count}")
        info = self.get(slot)
        if info is None:
            raise SaveError(f"slot {slot} is empty")
        return info

    # -- the QBM slot shuffle ------------------------------------------------

    def _clean_up_slot_1(self) -> None:
        """Free slot 1, shifting the others down. QBM's ``clean_up_slot_1``.

        The slot that gets dropped is the first empty one, or the
        highest-numbered slot whose protection has expired. Nothing protected is
        ever destroyed -- if there is no candidate the backup is refused, which
        is the whole point of setting a protection window.
        """
        self.ensure_directories()

        empty_index: int | None = None
        last_available: int | None = None
        for index in range(1, self.slot_count + 1):
            info = self.get(index)
            if info is None:
                if empty_index is None:
                    empty_index = index
            elif info.age_seconds > self.protection_of(index):
                last_available = index

        target = empty_index if empty_index is not None else last_available
        if target is None:
            raise NoSlotAvailable(
                f"all {self.slot_count} slots are within their delete protection window; "
                "delete one with !!save del <slot>, or lower the protection in config.yml"
            )

        victim = self.get(target)
        if victim is not None:
            self.logger.info(self._log("log.backup_dropped", slot=victim.describe()))
        shutil.rmtree(self.slot_path(target), ignore_errors=True)

        # Shift target-1 .. 1 down by one, so slot 1 ends up free.
        for index in reversed(range(1, target)):
            self.slot_path(index).rename(self.slot_path(index + 1))
        self.slot_path(1).mkdir(parents=True, exist_ok=True)

    # -- creating ------------------------------------------------------------

    async def create(
        self,
        comment: str = "",
        *,
        created_by: str = "unknown",
        players_online: list[str] | None = None,
        automatic: bool = False,
        write_save: Callable[[Path], Any] | None = None,
    ) -> Slot:
        """Make a backup in slot 1.

        ``write_save(target)`` should ask the running server to write a complete
        save at ``target`` and return once it has. When it is absent -- the
        server is stopped -- the current save file is copied instead, which is
        the best answer available with nothing to ask.
        """
        async with self._lock:
            self._clean_up_slot_1()
            slot_dir = self.slot_path(1)
            slot_dir.mkdir(parents=True, exist_ok=True)
            target = self.save_path(1)

            wrote = False
            if write_save is not None:
                try:
                    result = write_save(target)
                    if asyncio.iscoroutine(result):
                        await result
                    wrote = target.is_file()
                    if not wrote:
                        self.logger.warning(
                            "The server reported a save but %s is not there; "
                            "falling back to copying the current save file", target
                        )
                except Exception as exc:
                    self.logger.warning(
                        "The server could not write the backup (%s); "
                        "falling back to copying the current save file", exc
                    )

            if not wrote:
                if not self.current_save.is_file():
                    raise SaveError(f"save file does not exist: {self.current_save}")
                _verify_zip(self.current_save)
                shutil.copy2(self.current_save, target)

            try:
                _verify_zip(target)
            except SaveError:
                shutil.rmtree(slot_dir, ignore_errors=True)
                slot_dir.mkdir(parents=True, exist_ok=True)
                raise

            info = Slot(
                id=1,
                comment=comment,
                created_at=time.time(),
                created_by=created_by,
                players_online=list(players_online or []),
                size_bytes=target.stat().st_size,
                automatic=automatic,
            )
            info._tr = self.tr
            self._write_info(1, info)
            self.logger.info(self._log("log.backup_created", slot=info.describe()))
            return info

    def _write_info(self, slot: int | str, info: Slot) -> None:
        path = self.info_path(slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(info.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temp.replace(path)

    # -- the pre-restore backup ----------------------------------------------

    def back_up_current_world(self, confirmed_by: str) -> Slot | None:
        """Copy the live save into the fixed ``overwrite`` slot.

        QBM does this right before replacing the world, and it is what makes
        restoring the wrong slot survivable. Called with the server stopped, so
        the file is not moving underneath us.
        """
        if not self.current_save.is_file():
            self.logger.error("There is no current save to preserve at %s", self.current_save)
            return None

        path = self.slot_path(OVERWRITE_SLOT)
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        target = path / SAVE_NAME
        shutil.copy2(self.current_save, target)

        info = Slot(
            id=0,
            comment=f"the world as it was, before {confirmed_by} confirmed a restore",
            created_at=time.time(),
            created_by=confirmed_by,
            size_bytes=target.stat().st_size,
            automatic=True,
        )
        info._tr = self.tr
        self._write_info(OVERWRITE_SLOT, info)
        self.logger.info(self._log("log.world_preserved"))
        return info

    def get_overwrite(self) -> Slot | None:
        return self.get(OVERWRITE_SLOT)

    # -- restoring -----------------------------------------------------------

    async def restore(self, slot: int | str) -> None:
        """Put a slot's save in place. The server must be stopped.

        Writes to a temp file next to the target and renames, so an interrupted
        copy cannot leave a truncated save where the world used to be.
        """
        source = self.save_path(slot)
        if not source.is_file():
            raise SaveError(f"slot {slot} has no save file")
        _verify_zip(source)

        self.current_save.parent.mkdir(parents=True, exist_ok=True)
        temp = self.current_save.with_suffix(".zip.restoring")
        try:
            shutil.copy2(source, temp)
            _verify_zip(temp)
            temp.replace(self.current_save)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        self.logger.info(self._log("log.backup_restored", slot=slot, path=self.current_save))

    # -- editing -------------------------------------------------------------

    def rename(self, slot: int, comment: str) -> Slot:
        info = self.validate(slot)
        info.comment = comment
        self._write_info(slot, info)
        return info

    def delete(self, slot: int) -> Slot:
        info = self.validate(slot)
        shutil.rmtree(self.slot_path(slot), ignore_errors=True)
        self.slot_path(slot).mkdir(parents=True, exist_ok=True)
        self.logger.info("Deleted slot %s", slot)
        return info

    def total_size(self) -> int:
        return sum(
            path.stat().st_size
            for path in self.snapshot_directory.rglob(SAVE_NAME)
            if path.is_file()
        )


def _verify_zip(path: Path) -> None:
    """A Factorio save is a zip; a broken one must never become a restore target."""
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise SaveError(f"{path.name} contains a corrupt entry")
            if not archive.namelist():
                raise SaveError(f"{path.name} is an empty archive")
    except zipfile.BadZipFile as exc:
        raise SaveError(f"{path.name} is not a valid save archive: {exc}") from exc
    except FileNotFoundError as exc:
        raise SaveError(f"{path} does not exist") from exc


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"


def _slug(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in _SAFE else "_" for c in text.strip())
    return cleaned.replace(" ", "-")[:40].strip("-_") or "snapshot"
