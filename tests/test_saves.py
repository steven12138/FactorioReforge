"""SaveManager tests, including the paths that protect against losing a world."""

import time
import zipfile

import pytest

from factorio_reforge.saves.manager import SaveError, SaveManager

pytestmark = pytest.mark.asyncio


def write_save(path, marker: str = "world"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("level.dat", marker)
    return path


def read_save(path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("level.dat").decode()


@pytest.fixture
def manager(tmp_path):
    save = write_save(tmp_path / "saves" / "reforge.zip")
    mgr = SaveManager(save, tmp_path / "snapshots", max_snapshots=5, max_age_days=0)
    mgr.load_index()
    return mgr


class TestSnapshot:
    async def test_create_copies_the_live_save(self, manager):
        snapshot = await manager.create("first", created_by="alice")
        assert snapshot.id == 1 and snapshot.comment == "first"
        assert manager.path_of(snapshot).is_file()
        assert read_save(manager.path_of(snapshot)) == "world"

    async def test_save_first_hook_runs_before_the_copy(self, manager):
        calls = []

        async def flush():
            calls.append("flushed")
            write_save(manager.current_save, "flushed-world")

        snapshot = await manager.create("x", save_first=flush)
        assert calls == ["flushed"]
        assert read_save(manager.path_of(snapshot)) == "flushed-world"

    async def test_missing_save_is_reported(self, manager):
        manager.current_save.unlink()
        with pytest.raises(SaveError, match="does not exist"):
            await manager.create()

    async def test_corrupt_save_is_refused_rather_than_snapshotted(self, manager):
        manager.current_save.write_bytes(b"not a zip at all")
        with pytest.raises(SaveError):
            await manager.create()

    async def test_index_survives_a_reload(self, manager, tmp_path):
        await manager.create("keep me", created_by="bob")
        reopened = SaveManager(manager.current_save, manager.snapshot_directory)
        reopened.load_index()
        assert [s.comment for s in reopened.list()] == ["keep me"]
        assert reopened.list()[0].created_by == "bob"

    async def test_ids_keep_climbing_after_a_reload(self, manager):
        await manager.create("a")
        reopened = SaveManager(manager.current_save, manager.snapshot_directory)
        reopened.load_index()
        second = await reopened.create("b")
        assert second.id == 2

    async def test_entries_whose_file_vanished_are_dropped(self, manager):
        snapshot = await manager.create("gone")
        manager.path_of(snapshot).unlink()
        reopened = SaveManager(manager.current_save, manager.snapshot_directory)
        reopened.load_index()
        assert reopened.list() == [], "a snapshot with no file must not be offered for rollback"

    async def test_a_broken_index_is_rebuilt_from_the_files_on_disk(self, manager):
        await manager.create("real")
        manager.index_path.write_text("{{{ not json")
        reopened = SaveManager(manager.current_save, manager.snapshot_directory)
        reopened.load_index()
        assert len(reopened.list()) == 1


class TestRestore:
    async def test_restore_puts_the_old_world_back(self, manager):
        snapshot = await manager.create("v1")
        write_save(manager.current_save, "v2")
        assert read_save(manager.current_save) == "v2"
        await manager.restore_file(snapshot)
        assert read_save(manager.current_save) == "world"

    async def test_restoring_a_corrupt_snapshot_leaves_the_world_untouched(self, manager):
        snapshot = await manager.create("v1")
        manager.path_of(snapshot).write_bytes(b"corrupted")
        write_save(manager.current_save, "current")
        with pytest.raises(SaveError):
            await manager.restore_file(snapshot)
        assert read_save(manager.current_save) == "current"

    async def test_restoring_a_missing_snapshot_is_reported(self, manager):
        snapshot = await manager.create("v1")
        manager.path_of(snapshot).unlink()
        with pytest.raises(SaveError, match="missing"):
            await manager.restore_file(snapshot)


class TestRotation:
    async def test_manual_snapshots_are_never_rotated_away(self, tmp_path):
        save = write_save(tmp_path / "saves" / "reforge.zip")
        mgr = SaveManager(save, tmp_path / "snapshots", max_snapshots=2, max_age_days=0)
        mgr.load_index()
        for i in range(3):
            await mgr.create(f"manual {i}")
        mgr.rotate()
        assert len(mgr.list()) == 3, "a snapshot someone asked for is not disposable"

    async def test_automatic_snapshots_are_trimmed_to_the_limit(self, tmp_path):
        save = write_save(tmp_path / "saves" / "reforge.zip")
        mgr = SaveManager(save, tmp_path / "snapshots", max_snapshots=2, max_age_days=0)
        mgr.load_index()
        for i in range(4):
            await mgr.create(f"auto {i}", automatic=True)
        removed = mgr.rotate()
        assert len(removed) == 2
        assert len(mgr.list()) == 2
        assert [s.comment for s in mgr.list()] == ["auto 3", "auto 2"], "oldest go first"
        for snapshot in removed:
            assert not mgr.path_of(snapshot).exists(), "rotation must delete the file too"

    async def test_age_limit_removes_old_automatic_snapshots(self, tmp_path):
        save = write_save(tmp_path / "saves" / "reforge.zip")
        mgr = SaveManager(save, tmp_path / "snapshots", max_snapshots=0, max_age_days=7)
        mgr.load_index()
        old = await mgr.create("old", automatic=True)
        old.created_at = time.time() - 10 * 86400
        await mgr.create("fresh", automatic=True)
        removed = mgr.rotate()
        assert [s.comment for s in removed] == ["old"]


class TestDelete:
    async def test_delete_removes_the_entry_and_the_file(self, manager):
        snapshot = await manager.create("x")
        path = manager.path_of(snapshot)
        assert manager.delete(snapshot.id) is True
        assert not path.exists()
        assert manager.get(snapshot.id) is None

    async def test_deleting_an_unknown_id_is_false_not_an_error(self, manager):
        assert manager.delete(999) is False
