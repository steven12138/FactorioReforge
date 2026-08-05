"""Plugin discovery, dependency ordering, hot reload and event dispatch."""

import textwrap

import pytest

from factorio_reforge.i18n import Translator
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
        # The manager loads and unloads each plugin's translations and logs
        # through the translator, so a stand-in core needs a real one.
        self.i18n = Translator()

    def tr(self, key, /, *args, **kwargs):
        return self.i18n.translate(key, *args, **kwargs)


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

    async def test_commands_from_the_old_version_do_not_survive_a_reload(
        self, tmp_path, plugin_dir, caplog
    ):
        """The listener test above had no command equivalent, and the gap hid a
        real bug for the whole life of the project.

        Nothing ever called ``CommandManager.unregister_plugin``. The plugin's
        own registry was cleared on unload, but the command manager keeps a
        separate index by root literal -- so every reload left the previous
        tree registered *and* first in the list. Dispatch tried it first, and it
        ran the old module, whose ``on_unload`` had just cleared its state; on
        the live server that surfaced as ``KeyError: 'book'`` from a plugin
        whose new version was perfectly fine.

        It stayed invisible because the stand-in used elsewhere in this file
        implements ``unregister_plugin``. Satisfying a method nobody calls
        proves nothing, so this one drives the real CommandManager.
        """
        import logging

        from factorio_reforge.command.manager import CommandManager
        from factorio_reforge.command.source import ConsoleCommandSource
        from factorio_reforge.plugin.manager import PluginManager

        core = FakeCore(tmp_path, logging.getLogger("test"))
        core.commands = CommandManager(prefix="!!", logger=logging.getLogger("test"))
        manager = PluginManager(core, [plugin_dir], logging.getLogger("test"))
        core.plugins = manager
        manager.server = core

        path = write_plugin(plugin_dir, "commander", """
            from factorio_reforge.command.builder import Literal
            RAN = []
            def on_load(server, prev):
                server.register_command(Literal('!!ping').runs(_run))
            def on_unload(server):
                RAN.clear()
            async def _run(source):
                RAN.append('v1')
        """)
        await manager.load_all()
        assert core.commands.roots()["!!ping"] == ["commander"]

        path.write_text(
            "PLUGIN_METADATA = {'id': 'commander', 'version': '2.0.0'}\n"
            "from factorio_reforge.command.builder import Literal\n"
            "RAN = []\n"
            "def on_load(server, prev):\n"
            "    server.register_command(Literal('!!ping').runs(_run))\n"
            "def on_unload(server):\n"
            "    RAN.clear()\n"
            "async def _run(source):\n"
            "    RAN.append('v2')\n"
        )
        await manager.reload("commander")

        # One entry, not two: the old tree must be gone, not merely shadowed.
        assert core.commands.roots()["!!ping"] == ["commander"]
        assert not any("already owned by" in r.getMessage() for r in caplog.records)

        await core.commands.dispatch(ConsoleCommandSource(core), "!!ping")
        assert manager.get("commander").module.RAN == ["v2"]

    async def test_unloading_a_plugin_leaves_no_command_behind(self, tmp_path, plugin_dir):
        """Otherwise !!ping still dispatches into a module that is gone."""
        import logging

        from factorio_reforge.command.manager import CommandManager
        from factorio_reforge.plugin.manager import PluginManager

        core = FakeCore(tmp_path, logging.getLogger("test"))
        core.commands = CommandManager(prefix="!!", logger=logging.getLogger("test"))
        manager = PluginManager(core, [plugin_dir], logging.getLogger("test"))
        core.plugins = manager
        manager.server = core

        write_plugin(plugin_dir, "commander2", """
            from factorio_reforge.command.builder import Literal
            def on_load(server, prev):
                server.register_command(Literal('!!pong').runs(_run))
            async def _run(source):
                pass
        """)
        await manager.load_all()
        await manager.unload("commander2")
        assert "!!pong" not in core.commands.roots()

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

        write_plugin(plugin_dir, "stable", "")
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


class TestTranslationDirectories:
    """Where a plugin's language files are looked for."""

    def test_a_multi_file_plugin_keeps_them_inside_its_package(self, tmp_path):
        from factorio_reforge.plugin.manager import plugin_lang_dir

        pkg = tmp_path / "telegram_bridge"
        pkg.mkdir()
        assert plugin_lang_dir(pkg, "telegram_bridge") == pkg / "lang"

    def test_a_solo_plugin_has_nowhere_to_keep_translations(self, tmp_path):
        """Sharing one directory would have solo plugins clobber each other."""
        from factorio_reforge.plugin.manager import plugin_lang_dir

        assert not plugin_lang_dir(tmp_path / "warp.py", "warp").is_dir()

    async def test_a_package_loads_its_own_catalogue(self, manager, plugin_dir):
        import yaml

        package = plugin_dir / "greeter"
        (package / "lang").mkdir(parents=True)
        (package / "__init__.py").write_text(
            "PLUGIN_METADATA = {'id': 'greeter', 'version': '1.0.0'}\n"
        )
        (package / "lang" / "en.yml").write_text(yaml.safe_dump({"hello": "hi there"}))
        await manager.load_all()
        assert manager.server.i18n.translate("greeter.hello") == "hi there"

    async def test_unloading_drops_only_that_plugin_keys(self, manager, plugin_dir):
        import yaml

        for name in ("alpha", "beta"):
            package = plugin_dir / name
            (package / "lang").mkdir(parents=True)
            (package / "__init__.py").write_text(
                f"PLUGIN_METADATA = {{'id': {name!r}, 'version': '1.0.0'}}\n"
            )
            (package / "lang" / "en.yml").write_text(yaml.safe_dump({"word": name}))
        await manager.load_all()
        await manager.unload("alpha")
        assert manager.server.i18n.translate("alpha.word") == "alpha.word"
        assert manager.server.i18n.translate("beta.word") == "beta"
