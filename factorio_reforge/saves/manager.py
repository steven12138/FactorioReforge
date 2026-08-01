"""Snapshots and rollback.

Factorio cannot swap the loaded map at runtime, so a rollback is an orchestrated
sequence -- announce, snapshot the present, stop, replace the file, start -- not
a file copy. Every step that can fail is followed by a path back to the state we
started from, because the whole point of this feature is not losing a world.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

INDEX_FILE = "index.json"
_SAFE = "-_.() "


class SaveError(Exception):
    pass


@dataclasses.dataclass
class Snapshot:
    id: int
    filename: str
    comment: str
    created_at: float
    created_by: str = "unknown"
    players_online: list[str] = dataclasses.field(default_factory=list)
    size_bytes: int = 0
    automatic: bool = False

    @property
    def created_at_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at))

    @property
    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def describe(self) -> str:
        who = f" by {self.created_by}" if self.created_by != "unknown" else ""
        note = f" - {self.comment}" if self.comment else ""
        mb = self.size_bytes / (1024 * 1024)
        return f"#{self.id} {self.created_at_text}{who} ({mb:.1f} MiB){note}"


class SaveManager:
    """Owns the snapshot directory and its index."""

    def __init__(
        self,
        current_save: Path,
        snapshot_directory: Path,
        *,
        max_snapshots: int = 30,
        max_age_days: int = 30,
        logger: Optional[logging.Logger] = None,
    ):
        self.current_save = Path(current_save)
        self.snapshot_directory = Path(snapshot_directory)
        self.max_snapshots = max_snapshots
        self.max_age_days = max_age_days
        self.logger = logger or logging.getLogger(__name__)
        self._snapshots: list[Snapshot] = []
        self._next_id = 1
        self._lock = asyncio.Lock()

    # -- index ---------------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.snapshot_directory / INDEX_FILE

    def load_index(self) -> None:
        self.snapshot_directory.mkdir(parents=True, exist_ok=True)
        if not self.index_path.is_file():
            self._snapshots, self._next_id = [], 1
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.error("Snapshot index is unreadable (%s); rebuilding from disk", exc)
            self._rebuild_index_from_disk()
            return
        self._snapshots = [Snapshot.from_dict(d) for d in data.get("snapshots", [])]
        # Drop entries whose file is gone so the list never offers a rollback
        # target that cannot be restored.
        missing = [s for s in self._snapshots if not (self.snapshot_directory / s.filename).is_file()]
        for snapshot in missing:
            self.logger.warning("Snapshot #%s file is missing, dropping it", snapshot.id)
            self._snapshots.remove(snapshot)
        self._next_id = max((s.id for s in self._snapshots), default=0) + 1
        if missing:
            self.save_index()

    def _rebuild_index_from_disk(self) -> None:
        self._snapshots = []
        for index, path in enumerate(sorted(self.snapshot_directory.glob("*.zip")), start=1):
            stat = path.stat()
            self._snapshots.append(
                Snapshot(
                    id=index, filename=path.name, comment="(recovered)",
                    created_at=stat.st_mtime, size_bytes=stat.st_size,
                )
            )
        self._next_id = len(self._snapshots) + 1
        self.save_index()

    def save_index(self) -> None:
        self.snapshot_directory.mkdir(parents=True, exist_ok=True)
        payload = {"snapshots": [s.to_dict() for s in self._snapshots]}
        temp = self.index_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.index_path)

    # -- queries -------------------------------------------------------------

    def list(self) -> list[Snapshot]:
        return sorted(self._snapshots, key=lambda s: s.created_at, reverse=True)

    def get(self, snapshot_id: int) -> Optional[Snapshot]:
        return next((s for s in self._snapshots if s.id == snapshot_id), None)

    def path_of(self, snapshot: Snapshot) -> Path:
        return self.snapshot_directory / snapshot.filename

    # -- create --------------------------------------------------------------

    async def create(
        self,
        comment: str = "",
        *,
        created_by: str = "unknown",
        players_online: Optional[list[str]] = None,
        automatic: bool = False,
        save_first: Optional[Callable[[], Any]] = None,
    ) -> Snapshot:
        """Copy the live save into the snapshot directory.

        ``save_first`` should ask the running server to flush the map to disk and
        return once it has; skipping it snapshots whatever was last written,
        which can be many minutes stale.
        """
        async with self._lock:
            if save_first is not None:
                result = save_first()
                if asyncio.iscoroutine(result):
                    await result

            if not self.current_save.is_file():
                raise SaveError(f"save file does not exist: {self.current_save}")
            _verify_zip(self.current_save)

            self.snapshot_directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            suffix = f"_{_slug(comment)}" if comment else ""
            filename = f"{stamp}{suffix}.zip"
            target = self.snapshot_directory / filename
            counter = 1
            while target.exists():
                filename = f"{stamp}{suffix}_{counter}.zip"
                target = self.snapshot_directory / filename
                counter += 1

            temp = target.with_suffix(".zip.part")
            try:
                shutil.copy2(self.current_save, temp)
                _verify_zip(temp)
                temp.replace(target)
            except Exception:
                temp.unlink(missing_ok=True)
                raise

            snapshot = Snapshot(
                id=self._next_id,
                filename=filename,
                comment=comment,
                created_at=time.time(),
                created_by=created_by,
                players_online=list(players_online or []),
                size_bytes=target.stat().st_size,
                automatic=automatic,
            )
            self._next_id += 1
            self._snapshots.append(snapshot)
            self.save_index()
            self.logger.info("Created snapshot %s", snapshot.describe())
            return snapshot

    # -- restore -------------------------------------------------------------

    async def restore_file(self, snapshot: Snapshot) -> None:
        """Overwrite the live save with a snapshot. The server must be stopped.

        Writes to a temp file next to the target and renames, so an interrupted
        copy cannot leave a truncated save where the world used to be.
        """
        source = self.path_of(snapshot)
        if not source.is_file():
            raise SaveError(f"snapshot file is missing: {source}")
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
        self.logger.info("Restored %s onto %s", snapshot.filename, self.current_save)

    # -- delete / rotate -----------------------------------------------------

    def delete(self, snapshot_id: int) -> bool:
        snapshot = self.get(snapshot_id)
        if snapshot is None:
            return False
        self.path_of(snapshot).unlink(missing_ok=True)
        self._snapshots.remove(snapshot)
        self.save_index()
        return True

    def rotate(self) -> list[Snapshot]:
        """Drop snapshots past the count or age limit. Returns what was removed.

        Only automatic snapshots are eligible: a human who typed a comment meant
        to keep that one.
        """
        removed: list[Snapshot] = []
        candidates = sorted(
            (s for s in self._snapshots if s.automatic), key=lambda s: s.created_at
        )
        if self.max_age_days > 0:
            for snapshot in list(candidates):
                if snapshot.age_days > self.max_age_days:
                    candidates.remove(snapshot)
                    removed.append(snapshot)
        if self.max_snapshots > 0:
            surplus = len(self._snapshots) - len(removed) - self.max_snapshots
            while surplus > 0 and candidates:
                removed.append(candidates.pop(0))
                surplus -= 1

        for snapshot in removed:
            self.path_of(snapshot).unlink(missing_ok=True)
            self._snapshots.remove(snapshot)
        if removed:
            self.save_index()
            self.logger.info("Rotated out %d snapshot(s)", len(removed))
        return removed


def _verify_zip(path: Path) -> None:
    """A Factorio save is a zip; a broken one must never become a rollback target."""
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise SaveError(f"{path.name} contains a corrupt entry")
            if not archive.namelist():
                raise SaveError(f"{path.name} is an empty archive")
    except zipfile.BadZipFile as exc:
        raise SaveError(f"{path.name} is not a valid save archive: {exc}") from exc


def _slug(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in _SAFE else "_" for c in text.strip())
    return cleaned.replace(" ", "-")[:40].strip("-_") or "snapshot"
