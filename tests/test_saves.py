"""Slot-based backups, following QuickBackupM.

The shuffle in ``_clean_up_slot_1`` is the part worth testing hardest: it is
what decides which world gets destroyed to make room for a new one.
"""

import json
import pathlib
import time
import zipfile

import pytest

from factorio_reforge.saves.manager import (
    OVERWRITE_SLOT,
    NoSlotAvailable,
    SaveError,
    SaveManager,
    SlotConfig,
)

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
    mgr = SaveManager(
        save, tmp_path / "snapshots",
        slots=[SlotConfig(0), SlotConfig(0), SlotConfig(0)],
    )
    mgr.load_index()
    return mgr


def age(manager, slot: int, seconds: float) -> None:
    """Backdate a slot, so protection windows can be tested without waiting."""
    path = manager.info_path(slot)
    data = json.loads(path.read_text())
    data["created_at"] = time.time() - seconds
    path.write_text(json.dumps(data))


class TestCreate:
    async def test_a_backup_lands_in_slot_1(self, manager):
        slot = await manager.create("first", created_by="alice")
        assert slot.id == 1
        assert manager.save_path(1).is_file()
        assert read_save(manager.save_path(1)) == "world"

    async def test_slots_shift_down_so_slot_1_is_always_newest(self, manager):
        for marker in ("one", "two", "three"):
            write_save(manager.current_save, marker)
            await manager.create(marker)
        assert read_save(manager.save_path(1)) == "three"
        assert read_save(manager.save_path(2)) == "two"
        assert read_save(manager.save_path(3)) == "one"

    async def test_the_oldest_slot_falls_off_the_end(self, manager):
        for marker in ("one", "two", "three", "four"):
            write_save(manager.current_save, marker)
            await manager.create(marker)
        assert [s.comment for s in manager.list()] == ["four", "three", "two"]
        assert read_save(manager.save_path(3)) == "two", "'one' should be gone"

    async def test_the_server_writes_the_backup_when_it_can(self, manager):
        """Factorio's /server-save <name> writes its own file; no copy needed."""
        calls = []

        async def write_save_here(target):
            calls.append(target)
            write_save(target, "written-by-server")

        slot = await manager.create("x", write_save=write_save_here)
        assert calls == [manager.save_path(1)]
        assert read_save(manager.save_path(1)) == "written-by-server"
        assert slot.size_bytes > 0

    async def test_the_live_save_is_not_touched_when_the_server_writes_it(self, manager):
        async def write_save_here(target):
            write_save(target, "backup")

        before = manager.current_save.read_bytes()
        await manager.create("x", write_save=write_save_here)
        assert manager.current_save.read_bytes() == before

    async def test_a_failed_server_write_falls_back_to_copying(self, manager, caplog):
        async def explode(target):
            raise RuntimeError("rcon down")

        with caplog.at_level("WARNING"):
            await manager.create("x", write_save=explode)
        assert read_save(manager.save_path(1)) == "world"
        assert "falling back" in caplog.text

    async def test_a_server_that_claims_success_but_writes_nothing_falls_back(self, manager):
        async def pretend(target):
            return None

        await manager.create("x", write_save=pretend)
        assert read_save(manager.save_path(1)) == "world"

    async def test_a_corrupt_result_does_not_become_a_slot(self, manager):
        async def write_rubbish(target):
            target.write_bytes(b"not a zip")

        with pytest.raises(SaveError):
            await manager.create("x", write_save=write_rubbish)
        assert manager.get(1) is None, "a broken backup must not be listed as restorable"

    async def test_missing_save_and_no_server_is_an_error(self, manager):
        manager.current_save.unlink()
        with pytest.raises(SaveError, match="does not exist"):
            await manager.create()


class TestDeleteProtection:
    @pytest.fixture
    def protected(self, tmp_path):
        save = write_save(tmp_path / "saves" / "reforge.zip")
        mgr = SaveManager(
            save, tmp_path / "snapshots",
            slots=[SlotConfig(0), SlotConfig(0), SlotConfig(3600)],
        )
        mgr.load_index()
        return mgr

    async def test_an_unprotected_slot_is_sacrificed_instead_of_a_protected_one(
        self, protected
    ):
        """The protected slot stays put; the newest unprotected one is dropped.

        This is the behaviour worth having: an hour-old world that someone
        marked worth keeping outlives a burst of fresh backups.
        """
        for marker in ("one", "two", "three"):
            write_save(protected.current_save, marker)
            await protected.create(marker)
        # slot 1="three" slot 2="two" slot 3="one", and slot 3 is protected.
        write_save(protected.current_save, "four")
        await protected.create("four")

        assert protected.get(3).comment == "one", "the protected world survived"
        assert protected.get(1).comment == "four"
        assert protected.get(2).comment == "three", "'two' was the one dropped"

    async def test_a_backup_is_refused_when_every_slot_is_protected(self, tmp_path):
        """Refusing beats destroying something someone asked to keep."""
        save = write_save(tmp_path / "saves" / "reforge.zip")
        mgr = SaveManager(
            save, tmp_path / "snapshots", slots=[SlotConfig(3600), SlotConfig(3600)]
        )
        mgr.load_index()
        await mgr.create("one")
        await mgr.create("two")
        with pytest.raises(NoSlotAvailable, match="protection"):
            await mgr.create("three")
        assert [s.comment for s in mgr.list()] == ["two", "one"]

    async def test_an_expired_protection_frees_the_slot_again(self, protected):
        for marker in ("one", "two", "three"):
            write_save(protected.current_save, marker)
            await protected.create(marker)
        age(protected, 3, 7200)
        write_save(protected.current_save, "four")
        await protected.create("four")
        assert [s.comment for s in protected.list()] == ["four", "three", "two"]

    async def test_an_empty_slot_is_preferred_over_dropping_anything(self, protected):
        await protected.create("only")
        assert protected.get(1).comment == "only"
        assert protected.get(2) is None
        await protected.create("second")
        assert [s.comment for s in protected.list()] == ["second", "only"]

    def test_is_protected_reports_the_window(self, protected, tmp_path):
        assert protected.protection_of(3) == 3600
        assert protected.protection_of(1) == 0


class TestRestore:
    async def test_restore_puts_the_old_world_back(self, manager):
        await manager.create("v1")
        write_save(manager.current_save, "v2")
        await manager.restore(1)
        assert read_save(manager.current_save) == "world"

    async def test_restoring_a_corrupt_slot_leaves_the_world_untouched(self, manager):
        await manager.create("v1")
        manager.save_path(1).write_bytes(b"corrupted")
        write_save(manager.current_save, "current")
        with pytest.raises(SaveError):
            await manager.restore(1)
        assert read_save(manager.current_save) == "current"

    async def test_restoring_an_empty_slot_is_reported(self, manager):
        with pytest.raises(SaveError, match="no save file"):
            await manager.restore(2)


class TestOverwriteSlot:
    """QBM's "backup current world to avoid idiot" -- the undo for a restore."""

    def test_the_current_world_is_preserved_before_being_replaced(self, manager):
        write_save(manager.current_save, "about-to-be-lost")
        info = manager.back_up_current_world("alice")
        assert info is not None
        assert read_save(manager.save_path(OVERWRITE_SLOT)) == "about-to-be-lost"
        assert "alice" in info.comment

    def test_it_is_overwritten_each_time_rather_than_accumulating(self, manager):
        write_save(manager.current_save, "first")
        manager.back_up_current_world("alice")
        write_save(manager.current_save, "second")
        manager.back_up_current_world("bob")
        assert read_save(manager.save_path(OVERWRITE_SLOT)) == "second"

    async def test_it_can_be_restored_like_any_slot(self, manager):
        write_save(manager.current_save, "the-good-world")
        manager.back_up_current_world("alice")
        write_save(manager.current_save, "the-mistake")
        await manager.restore(OVERWRITE_SLOT)
        assert read_save(manager.current_save) == "the-good-world"

    def test_with_no_current_save_it_reports_rather_than_pretending(self, manager):
        manager.current_save.unlink()
        assert manager.back_up_current_world("alice") is None

    def test_the_overwrite_slot_is_not_one_of_the_numbered_slots(self, manager):
        write_save(manager.current_save, "x")
        manager.back_up_current_world("alice")
        assert manager.list() == [], "it must not occupy a backup slot"


class TestEditing:
    async def test_rename_changes_the_comment(self, manager):
        await manager.create("typo")
        assert manager.rename(1, "fixed").comment == "fixed"
        assert manager.get(1).comment == "fixed"

    async def test_delete_empties_the_slot_without_shifting_others(self, manager):
        for marker in ("one", "two"):
            write_save(manager.current_save, marker)
            await manager.create(marker)
        manager.delete(1)
        assert manager.get(1) is None
        assert manager.get(2).comment == "one", "deleting must not renumber the rest"

    async def test_a_deleted_slot_is_reused_first(self, manager):
        for marker in ("one", "two"):
            write_save(manager.current_save, marker)
            await manager.create(marker)
        manager.delete(1)
        await manager.create("three")
        assert manager.get(1).comment == "three"
        assert manager.get(2).comment == "one"

    @pytest.mark.parametrize("slot", [0, 4, -1])
    async def test_an_out_of_range_slot_is_rejected(self, manager, slot):
        with pytest.raises(SaveError, match="between 1 and 3"):
            manager.validate(slot)

    async def test_an_empty_slot_is_reported_as_empty(self, manager):
        with pytest.raises(SaveError, match="empty"):
            manager.validate(2)


class TestListing:
    async def test_all_slots_includes_the_empty_ones(self, manager):
        await manager.create("only")
        rows = manager.all_slots()
        assert [index for index, _ in rows] == [1, 2, 3]
        assert rows[0][1].comment == "only"
        assert rows[1][1] is None

    async def test_a_slot_with_a_broken_info_file_reads_as_empty(self, manager):
        await manager.create("x")
        manager.info_path(1).write_text("{{{ not json")
        assert manager.get(1) is None

    async def test_a_slot_whose_save_vanished_reads_as_empty(self, manager):
        """Otherwise it would be offered as a restore target that cannot work."""
        await manager.create("x")
        manager.save_path(1).unlink()
        assert manager.get(1) is None

    async def test_total_size_counts_every_slot(self, manager):
        await manager.create("one")
        write_save(manager.current_save, "two")
        await manager.create("two")
        assert manager.total_size() > 0


class TestConfigUpgrade:
    """A config.yml written by an older version must still load."""

    def test_retired_keys_are_collected_instead_of_failing(self, tmp_path):
        """Collected rather than logged: the translator does not exist yet."""
        from factorio_reforge.config import _PENDING_WARNINGS, SavesConfig, _sub

        _PENDING_WARNINGS.clear()
        saves = _sub(
            SavesConfig,
            {"max_snapshots": 30, "max_snapshot_age_days": 30, "save_timeout": 60.0},
            "saves",
        )
        assert saves.save_timeout == 60.0
        keys = {key for _, key, _ in _PENDING_WARNINGS}
        assert keys == {"max_snapshots", "max_snapshot_age_days"}
        # The reason is a translation key now, resolved once a translator exists.
        assert all(reason.startswith("retired.") for _, _, reason in _PENDING_WARNINGS)
        _PENDING_WARNINGS.clear()

    def test_a_real_typo_still_errors(self):
        from factorio_reforge.config import ConfigError, SavesConfig, _sub

        with pytest.raises(ConfigError, match="save_timout"):
            _sub(SavesConfig, {"save_timout": 60.0}, "saves")


class TestSerialisation:
    """A slot carries a translator; serialising must not follow it."""

    async def test_to_dict_excludes_the_injected_translator(self, manager):
        slot = await manager.create("x")
        slot._tr = lambda *a, **k: "translated"
        assert "_tr" not in slot.to_dict()

    async def test_writing_info_works_with_a_translator_attached(self, manager):
        """asdict would deep-copy the translator's closure and die on it."""
        manager.tr = lambda key, **kwargs: f"tr:{key}"
        await manager.create("with a translator")
        assert manager.info_path(1).is_file()
        assert manager.get(1).comment == "with a translator"

    async def test_describe_uses_the_translator_when_present(self, manager):
        manager.tr = lambda key, **kwargs: f"[{key}] slot={kwargs.get('slot')}"
        await manager.create("x")
        assert manager.get(1).describe() == "[save.slot_describe] slot=1"

    async def test_describe_without_a_translator_still_reads_well(self, manager):
        slot = await manager.create("plain")
        slot._tr = None
        assert "slot 1:" in slot.describe() and "plain" in slot.describe()


class TestRconExposureCheck:
    """A start command that exposes RCON is refused, not warned about.

    RCON is plaintext and the failure is silent, so it has to be caught before
    the server ever listens.
    """

    def _config(self, tmp_path, rcon_args):
        from factorio_reforge.config import Config

        working = tmp_path / "server"
        working.mkdir()
        config = Config()
        config.root = tmp_path
        config.working_directory = str(working)
        config.rcon.password = "x"
        (working / "s.zip").write_text("")
        config.saves.current_save = str(working / "s.zip")
        config.start_command = f"./factorio --start-server s.zip {rcon_args}"
        return config

    def test_rcon_port_is_refused(self, tmp_path):
        from factorio_reforge.config import ConfigError

        config = self._config(tmp_path, "--rcon-port 27015")
        with pytest.raises(ConfigError, match="every interface"):
            config.validate()

    def test_a_wildcard_bind_is_refused(self, tmp_path):
        from factorio_reforge.config import ConfigError

        config = self._config(tmp_path, "--rcon-bind 0.0.0.0:27015")
        with pytest.raises(ConfigError, match="reachable from"):
            config.validate()

    @pytest.mark.parametrize("address", ["127.0.0.1:27015", "localhost:27015", "::1"])
    def test_a_local_bind_is_accepted(self, tmp_path, address):
        config = self._config(tmp_path, f"--rcon-bind {address}")
        config.validate()

    def test_the_default_start_command_binds_locally(self):
        from factorio_reforge.config import Config

        assert "--rcon-bind 127.0.0.1:" in Config().start_command
        assert "--rcon-port" not in Config().start_command


class TestServerSettingsWrites:
    """server_admin edits a file Factorio refuses to start without."""

    def _plugin(self):
        import importlib.util
        import sys

        name = "_test_plugin_server_admin"
        if name in sys.modules:
            return sys.modules[name]
        path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "plugins" / "server_admin" / "__init__.py"
        )
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_a_write_is_atomic(self, tmp_path):
        """A truncated server-settings.json stops the server from starting."""
        plugin = self._plugin()
        target = tmp_path / "server-settings.json"
        target.write_text(json.dumps({"name": "before"}))

        class FakeConfig:
            working_dir_path = tmp_path
            command_argv = ["./factorio", "--server-settings", str(target)]

        class FakeCore:
            config = FakeConfig()

        class FakeServer:
            _server = FakeCore()

        plugin.write_settings(FakeServer(), {"name": "after"})
        assert json.loads(target.read_text())["name"] == "after"
        assert not list(tmp_path.glob("*.tmp")), "the temp file must be renamed away"

    def test_the_settings_path_follows_the_start_command(self, tmp_path):
        """A non-standard --server-settings must be honoured, not assumed away."""
        plugin = self._plugin()
        elsewhere = tmp_path / "custom.json"

        class FakeConfig:
            working_dir_path = tmp_path
            command_argv = ["./factorio", "--server-settings", "custom.json"]

        class FakeCore:
            config = FakeConfig()

        class FakeServer:
            _server = FakeCore()

        assert plugin.settings_path(FakeServer()) == elsewhere
