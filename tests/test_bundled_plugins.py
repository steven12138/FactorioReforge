"""Every bundled plugin survives being loaded, through the real manager.

This exists because `version_manager` shipped reaching for `server.config`,
which lives on the core and not on the interface plugins are handed. It passed
535 tests: every one of them called the plugin's functions directly, so nothing
ever ran ``on_load`` against a real ``PluginServerInterface``. The first thing
that did was the server.

The point is not to exercise what each plugin does -- the other files do that.
It is to catch the one failure this shape of test catches and nothing else
does: a plugin using an API that is not there.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from factorio_reforge.i18n import Translator
from factorio_reforge.plugin.manager import LoadedPlugin, PluginManager

pytestmark = pytest.mark.asyncio

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"

#: Loading these opens something the test machine may not have free -- a
#: listening socket, a Telegram session. Excluded by what they do, not by
#: having failed: the exclusion is the reason, so it does not quietly grow.
NEEDS_THE_WORLD = {"web_panel"}


def bundled() -> list[str]:
    return sorted(
        entry.name
        for entry in PLUGINS.iterdir()
        if entry.is_dir()
        and (entry / "__init__.py").is_file()
        and entry.name not in NEEDS_THE_WORLD
    )


class FakeCore:
    """A core with the attributes config.yml actually provides."""

    def __init__(self, root: Path):
        self.logger = logging.getLogger("test")
        self.plugins = None
        self.commands = _Commands()
        self.config = _Config(root)
        self.i18n = Translator()
        self.saves = None
        self.rcon = None
        self.process = _Process()

        self.requested_lua_events: list[str] = []

    def tr(self, key, /, *args, **kwargs):
        return self.i18n.translate(key, *args, **kwargs)

    def request_lua_event(self, name):
        from factorio_reforge.core import luahooks

        # Same refusal the real core gives, so a plugin asking for an event
        # nobody wrote a payload for fails here rather than on a server.
        if name not in luahooks.BRIDGED:
            raise luahooks.UnknownEvent(name)
        self.requested_lua_events.append(name)


class _Commands:
    def __init__(self):
        self.registered = []

    def register(self, plugin_id, node):
        self.registered.append((plugin_id, node))

    def unregister_plugin(self, plugin_id):
        self.registered = [e for e in self.registered if e[0] != plugin_id]


class _Process:
    is_running = False
    is_startup_done = False
    pid = None
    uptime = None


class _Config:
    command_prefix = "!!"

    def __init__(self, root: Path):
        self.root = root
        self.working_directory = "server/factorio"
        self.start_command = "./bin/x64/factorio --start-server ./saves/reforge.zip"
        self.language = "en"

    def resolve(self, value):
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    @property
    def command_argv(self):
        return self.start_command.split()

    @property
    def working_dir_path(self):
        return self.resolve(self.working_directory)

    @property
    def current_save_path(self):
        return self.working_dir_path / "saves" / "reforge.zip"

    @property
    def save_dir_path(self):
        return self.working_dir_path / "saves"


@pytest.fixture
def core(tmp_path):
    (tmp_path / "server" / "factorio" / "saves").mkdir(parents=True)
    (tmp_path / "server" / "factorio" / "mods").mkdir(parents=True)
    return FakeCore(tmp_path)


@pytest.mark.parametrize("plugin_id", bundled())
async def test_a_bundled_plugin_loads(plugin_id, core, tmp_path):
    """on_load runs against the interface a plugin is really given."""
    manager = PluginManager(core, [PLUGINS], logging.getLogger("test"))
    core.plugins = manager
    manager.server = core

    path = PLUGINS / plugin_id
    metadata, module = manager._import(path)  # noqa: SLF001
    try:
        loaded = await manager._activate(  # noqa: SLF001
            LoadedPlugin(metadata, module, path)
        )
        assert loaded, f"{plugin_id} failed to load"
    finally:
        # Plugins start pollers on load; leaving them running would leak into
        # the next parameter and report as a failure somewhere unrelated.
        for task in asyncio.all_tasks() - {asyncio.current_task()}:
            task.cancel()


def test_the_exclusion_list_only_names_plugins_that_exist():
    """A renamed plugin must not silently drop out of this check."""
    missing = {name for name in NEEDS_THE_WORLD if not (PLUGINS / name).is_dir()}
    assert not missing, f"no such plugin(s): {sorted(missing)}"


async def test_leaving_is_recorded_through_the_real_event_dispatch(core, tmp_path):
    """``!!seen`` is only as good as the listener being wired up.

    The listeners are found by name -- ``on_player_left`` -- rather than
    registered explicitly, so a rename would silently stop the record without
    failing anything. This is the test that would notice.
    """
    import json

    import factorio_reforge.plugin.events as ev

    manager = PluginManager(core, [PLUGINS], logging.getLogger("test"))
    core.plugins = manager
    manager.server = core

    path = PLUGINS / "server_utils"
    metadata, module = manager._import(path)  # noqa: SLF001
    await manager._activate(LoadedPlugin(metadata, module, path))  # noqa: SLF001
    manager._rebuild()  # noqa: SLF001

    await manager.dispatch(ev.PLAYER_LEFT, "Alice")

    record = tmp_path / "config" / "server_utils" / "last_seen.json"
    assert record.is_file(), "nothing was written when a player left"
    assert json.loads(record.read_text())["Alice"] > 1e9, "not a wall-clock timestamp"
