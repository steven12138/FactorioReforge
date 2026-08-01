"""Plugin discovery, dependency ordering, hot reload and event dispatch."""

import textwrap

import pytest

from factorio_reforge.plugin.manager import PluginManager
from factorio_reforge.plugin.metadata import Metadata, MetadataError, satisfies

pytestmark = pytest.mark.asyncio


class FakeCore:
    """Enough of ReforgeServer for PluginManager and PluginServerInterface."""

    def __init__(self, tmp_path, logger):
        self.logger = logger
        self.plugins = None
        self.commands = _FakeCommands()
        self.config = _FakeConfig(tmp_path)


class _FakeCommands:
    def __init__(self):
        self.registered = []

    def register(self, plugin_id, node):
        self.registered.append((plugin_id, node))

    def unregister_plugin(self, plugin_id):
        self.registered = [e for e in self.registered if e[0] != plugin_id]


class _FakeConfig:
    def __init__(self, root):
        self.root = root

    def resolve(self, value):
        return self.root / value


@pytest.fixture
def plugin_dir(tmp_path):
    directory = tmp_path / "plugins"
    directory.mkdir()
    return directory


@pytest.fixture
def manager(tmp_path, plugin_dir, caplog):
    import logging

    core = FakeCore(tmp_path, logging.getLogger("test"))
    mgr = PluginManager(core, [plugin_dir], logging.getLogger("test"))
    core.plugins = mgr
    # PluginServerInterface expects the core object where ServerInterface keeps it.
    mgr.server = core
    return mgr


def write_plugin(directory, name, body, metadata=None):
    metadata = metadata or {"id": name, "version": "1.0.0"}
    path = directory / f"{name}.py"
    path.write_text(
        f"PLUGIN_METADATA = {metadata!r}\n" + textwrap.dedent(body), encoding="utf-8"
    )
    return path


class TestDiscovery:
    async def test_solo_and_multi_file_plugins_are_both_found(self, manager, plugin_dir):
        write_plugin(plugin_dir, "solo", "")
        multi = plugin_dir / "multi"
        multi.mkdir()
        (multi / "__init__.py").write_text(
            "PLUGIN_METADATA = {'id': 'multi', 'version': '1.0.0'}\n"
        )
        assert {p.name for p in manager.discover()} == {"solo.py", "multi"}

    async def test_disabled_and_underscored_entries_are_skipped(self, manager, plugin_dir):
        write_plugin(plugin_dir, "live", "")
        (plugin_dir / "off.py.disabled").write_text("PLUGIN_METADATA = {'id': 'off'}")
        (plugin_dir / "_private.py").write_text("PLUGIN_METADATA = {'id': 'x'}")
        assert [p.name for p in manager.discover()] == ["live.py"]

    async def test_a_missing_plugin_directory_is_not_an_error(self, manager, tmp_path):
        manager.directories = [tmp_path / "does-not-exist"]
        assert manager.discover() == []


class TestLoading:
    async def test_on_load_runs_and_the_plugin_is_listed(self, manager, plugin_dir):
        write_plugin(plugin_dir, "hello", """
            LOADED = []
            def on_load(server, prev):
                LOADED.append(True)
        """)
        loaded, failed = await manager.load_all()
        assert loaded == ["hello"] and failed == []
        assert manager.get("hello").module.LOADED == [True]

    async def test_a_plugin_without_metadata_is_rejected_not_crashed_on(
        self, manager, plugin_dir
    ):
        (plugin_dir / "bad.py").write_text("x = 1\n")
        loaded, failed = await manager.load_all()
        assert loaded == [] and failed == ["bad.py"]

    async def test_a_syntax_error_fails_only_that_plugin(self, manager, plugin_dir):
        (plugin_dir / "broken.py").write_text("PLUGIN_METADATA = {'id': 'broken'}\ndef (:\n")
        write_plugin(plugin_dir, "fine", "")
        loaded, failed = await manager.load_all()
        assert loaded == ["fine"]
        assert "broken.py" in failed

    async def test_a_raising_on_load_does_not_leave_a_half_loaded_plugin(
        self, manager, plugin_dir
    ):
        write_plugin(plugin_dir, "explodes", """
            def on_load(server, prev):
                raise RuntimeError('nope')
        """)
        loaded, failed = await manager.load_all()
        assert loaded == [] and "explodes" in failed
        assert manager.get("explodes") is None

    async def test_async_on_load_is_awaited(self, manager, plugin_dir):
        write_plugin(plugin_dir, "asyncplug", """
            STATE = {}
            async def on_load(server, prev):
                STATE['ready'] = True
        """)
        await manager.load_all()
        assert manager.get("asyncplug").module.STATE == {"ready": True}


class TestDependencies:
    async def test_dependencies_load_before_dependents(self, manager, plugin_dir):
        write_plugin(plugin_dir, "base", "", {"id": "base", "version": "1.0.0"})
        write_plugin(
            plugin_dir, "child", "",
            {"id": "child", "version": "1.0.0", "dependencies": {"base": ">=1.0.0"}},
        )
        loaded, _ = await manager.load_all()
        assert loaded.index("base") < loaded.index("child")

    async def test_a_plugin_with_a_missing_dependency_is_not_loaded(self, manager, plugin_dir):
        write_plugin(
            plugin_dir, "orphan", "",
            {"id": "orphan", "version": "1.0.0", "dependencies": {"nowhere": "*"}},
        )
        loaded, failed = await manager.load_all()
        assert loaded == [] and "orphan" in failed

    async def test_a_version_mismatch_rejects_the_dependent(self, manager, plugin_dir):
        write_plugin(plugin_dir, "base", "", {"id": "base", "version": "1.0.0"})
        write_plugin(
            plugin_dir, "picky", "",
            {"id": "picky", "version": "1.0.0", "dependencies": {"base": ">=2.0.0"}},
        )
        loaded, failed = await manager.load_all()
        assert loaded == ["base"] and "picky" in failed

    async def test_a_dependency_cycle_is_reported_rather_than_hanging(self, manager, plugin_dir):
        write_plugin(plugin_dir, "a", "", {"id": "a", "version": "1.0", "dependencies": {"b": "*"}})
        write_plugin(plugin_dir, "b", "", {"id": "b", "version": "1.0", "dependencies": {"a": "*"}})
        loaded, failed = await manager.load_all()
        assert loaded == []
        assert set(failed) == {"a", "b"}


class TestReload:
    async def test_reload_picks_up_the_edited_file(self, manager, plugin_dir):
        path = write_plugin(plugin_dir, "mutable", "VALUE = 1\n")
        await manager.load_all()
        assert manager.get("mutable").module.VALUE == 1

        path.write_text("PLUGIN_METADATA = {'id': 'mutable', 'version': '2.0.0'}\nVALUE = 2\n")
        assert await manager.reload("mutable") is True
        assert manager.get("mutable").module.VALUE == 2
        assert manager.get("mutable").metadata.version == "2.0.0"

    async def test_on_unload_runs_before_the_new_version_loads(self, manager, plugin_dir):
        marker = plugin_dir.parent / "unloaded.txt"
        write_plugin(plugin_dir, "cleanup", f"""
            def on_unload(server):
                open({str(marker)!r}, 'w').write('bye')
        """)
        await manager.load_all()
        await manager.unload("cleanup")
        assert marker.read_text() == "bye"
        assert manager.get("cleanup") is None

    async def test_listeners_from_the_old_version_do_not_survive_a_reload(
        self, manager, plugin_dir
    ):
        path = write_plugin(plugin_dir, "listener", """
            SEEN = []
            def on_player_joined(server, player, info=None):
                SEEN.append(('v1', player))
        """)
        await manager.load_all()
        await manager.dispatch("reforge.player_joined", "alice", None)
        assert manager.get("listener").module.SEEN == [("v1", "alice")]

        path.write_text(
            "PLUGIN_METADATA = {'id': 'listener', 'version': '2.0.0'}\n"
            "SEEN = []\n"
            "def on_player_joined(server, player, info=None):\n"
            "    SEEN.append(('v2', player))\n"
        )
        await manager.reload("listener")
        await manager.dispatch("reforge.player_joined", "bob", None)
        # Exactly one entry: the v1 callback must be gone, not merely shadowed.
        assert manager.get("listener").module.SEEN == [("v2", "bob")]

    async def test_unloading_a_plugin_others_depend_on_is_refused(self, manager, plugin_dir):
        from factorio_reforge.plugin.manager import PluginError

        write_plugin(plugin_dir, "base", "", {"id": "base", "version": "1.0.0"})
        write_plugin(
            plugin_dir, "child", "",
            {"id": "child", "version": "1.0.0", "dependencies": {"base": "*"}},
        )
        await manager.load_all()
        with pytest.raises(PluginError, match="required by"):
            await manager.unload("base")

    async def test_reload_changed_only_touches_edited_plugins(self, manager, plugin_dir):
        import os
        import time

        stable = write_plugin(plugin_dir, "stable", "")
        edited = write_plugin(plugin_dir, "edited", "")
        await manager.load_all()
        time.sleep(0.01)
        edited.write_text("PLUGIN_METADATA = {'id': 'edited', 'version': '1.0.1'}\n")
        os.utime(edited, None)
        assert await manager.reload_changed() == ["edited"]


class TestDispatch:
    async def test_listeners_run_in_priority_order(self, manager, plugin_dir):
        write_plugin(plugin_dir, "ordered", """
            from factorio_reforge.plugin.events import event_listener
            ORDER = []

            @event_listener('reforge.player_joined', priority=200)
            def late(server, player, info=None):
                ORDER.append('late')

            @event_listener('reforge.player_joined', priority=10)
            def early(server, player, info=None):
                ORDER.append('early')
        """)
        await manager.load_all()
        await manager.dispatch("reforge.player_joined", "alice", None)
        assert manager.get("ordered").module.ORDER == ["early", "late"]

    async def test_one_raising_listener_does_not_stop_the_others(self, manager, plugin_dir):
        write_plugin(plugin_dir, "angry", """
            def on_player_joined(server, player, info=None):
                raise RuntimeError('boom')
        """)
        write_plugin(plugin_dir, "calm", """
            SEEN = []
            def on_player_joined(server, player, info=None):
                SEEN.append(player)
        """)
        await manager.load_all()
        await manager.dispatch("reforge.player_joined", "alice", None)
        assert manager.get("calm").module.SEEN == ["alice"]

    async def test_a_listener_may_take_fewer_arguments_than_the_event_carries(
        self, manager, plugin_dir
    ):
        write_plugin(plugin_dir, "lazy", """
            SEEN = []
            def on_player_joined(server, player):
                SEEN.append(player)
        """)
        await manager.load_all()
        await manager.dispatch("reforge.player_joined", "alice", "extra-info")
        assert manager.get("lazy").module.SEEN == ["alice"]


class TestMetadata:
    def test_an_invalid_id_is_rejected(self):
        with pytest.raises(MetadataError):
            Metadata(id="Not Valid!")

    def test_name_defaults_to_the_id(self):
        assert Metadata(id="thing").name == "thing"

    @pytest.mark.parametrize(
        "version, requirement, expected",
        [
            ("1.2.3", "*", True),
            ("1.2.3", ">=1.0.0", True),
            ("1.2.3", ">=2.0.0", False),
            ("2.0.0", "<3.0.0", True),
            ("1.0.0", "==1.0.0", True),
            ("1.0.0", "!=1.0.0", False),
            ("1.2.0", ">=1.0.0,<2.0.0", True),
            ("2.5.0", ">=1.0.0,<2.0.0", False),
            ("1.2", ">=1.2.0", True),
        ],
    )
    def test_version_requirements(self, version, requirement, expected):
        assert satisfies(version, requirement) is expected
