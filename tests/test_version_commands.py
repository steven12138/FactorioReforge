"""What the version plugin does when a swap goes wrong.

The happy path is a symlink flip. The path worth testing is the other one: the
new build does not come up, and the server has to end up back where it started
-- old binary, old world -- without anyone typing anything. Nothing else in the
project fails in a way that leaves a world unopenable, so this is exercised
rather than reasoned about.
"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

from factorio_reforge.saves.manager import PREUPGRADE_SLOT, SaveManager, SlotConfig
from factorio_reforge.versions.layout import Installation

pytestmark = pytest.mark.asyncio

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


def load_plugin(name: str = "version_manager"):
    module_name = f"_test_plugin_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, PLUGINS / name / "__init__.py",
        submodule_search_locations=[str(PLUGINS / name)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class Replies:
    def __init__(self):
        self.lines: list[str] = []

    async def reply(self, text: str) -> None:
        self.lines.append(str(text))

    player = None

    def __str__(self) -> str:
        return "console"

    @property
    def text(self) -> str:
        return " | ".join(self.lines)


class FakeServer:
    """Enough of ServerInterface for a switch, with a start that can be made to fail."""

    def __init__(self, saves, working_dir, current_save):
        self.saves = saves
        self.running = True
        self.start_succeeds = True
        self.starts = 0
        self.said: list[str] = []
        self.events: list[tuple] = []

        server = self

        class Cfg:
            command_prefix = "!!"
            working_dir_path = working_dir
            current_save_path = current_save

        self.config = Cfg()

        class Logger:
            def info(self, *a, **k):
                pass

            warning = error = info

        self.logger = Logger()
        self._server = server

    def tr(self, key, **kwargs):
        return key

    def is_server_running(self):
        return self.running

    def is_rcon_running(self):
        return False

    async def stop(self):
        self.running = False
        return True

    async def start(self):
        self.starts += 1
        self.running = self.start_succeeds
        return self.start_succeeds

    async def say(self, text):
        self.said.append(text)

    async def dispatch_event(self, event, *args):
        self.events.append((event, args))


def write_save(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("world/level-init.dat", b"\x02\x00\x00\x00\x4d\x00\x00\x00")
        archive.writestr("world/marker", marker)
    return path


def marker_of(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("world/marker").decode()


def make_tree(installation: Installation, version: str) -> None:
    tree = installation.version_dir(version)
    (tree / "bin" / "x64").mkdir(parents=True)
    (tree / "bin" / "x64" / "factorio").write_text(version)


@pytest.fixture
def setup(tmp_path):
    plugin = load_plugin()

    live = tmp_path / "server" / "factorio"
    (live / "bin" / "x64").mkdir(parents=True)
    (live / "bin" / "x64" / "factorio").write_text("2.0.77")
    (live / "saves").mkdir()
    current = write_save(live / "saves" / "reforge.zip", "before")

    installation = Installation(live)
    installation.adopt("2.0.77")
    make_tree(installation, "2.0.78")

    saves = SaveManager(
        current, tmp_path / "snapshots",
        slots=[SlotConfig(0), SlotConfig(0)],
        auto_slots=[SlotConfig(0)],
    )
    saves.load_index()
    server = FakeServer(saves, live, current)

    plugin._state.clear()
    plugin._state.update(
        server=server,
        config={"countdown_seconds": 0, "confirm_window_seconds": 120},
        installation=installation,
        staged=None,
        busy=False,
    )
    return plugin, server, installation, current


class TestSwitch:
    async def test_a_successful_switch_flips_the_link(self, setup):
        plugin, server, installation, _ = setup
        await plugin._switch(Replies(), "2.0.78", "")
        assert installation.active_version == "2.0.78"
        assert server.running

    async def test_the_world_is_kept_before_anything_moves(self, setup):
        """The pre-upgrade slot is the only world the old binary can still open."""
        plugin, server, installation, _ = setup
        await plugin._switch(Replies(), "2.0.78", "")
        assert marker_of(server.saves.save_path(PREUPGRADE_SLOT)) == "before"

    async def test_the_kept_world_is_not_the_overwrite_slot(self, setup):
        """A restore would spend that one, and it has to outlive a restore."""
        plugin, server, installation, _ = setup
        await plugin._switch(Replies(), "2.0.78", "")
        assert server.saves.get_overwrite() is None
        assert server.saves.get_preupgrade() is not None

    async def test_a_switch_that_will_not_start_puts_the_old_version_back(self, setup):
        plugin, server, installation, _ = setup
        server.start_succeeds = False

        source = Replies()
        await plugin._switch(source, "2.0.78", "")

        assert installation.active_version == "2.0.77"
        assert "switch.did_not_come_up" in source.text

    async def test_a_failed_switch_puts_the_world_back_too(self, setup):
        """Restoring the link is not enough if the new build wrote a save."""
        plugin, server, installation, current = setup
        server.start_succeeds = False
        await plugin._switch(Replies(), "2.0.78", "")
        assert marker_of(current) == "before"

    async def test_a_downgrade_restores_the_paired_world(self, setup):
        plugin, server, installation, current = setup
        write_save(server.saves.slot_path(1) / "save.zip", "older")
        server.saves._write_info(1, _slot_info(server.saves))

        await plugin._switch(Replies(), "2.0.78", "1")
        assert marker_of(current) == "older"
        # And the world it replaced is still reachable.
        assert marker_of(server.saves.save_path(PREUPGRADE_SLOT)) == "before"

    async def test_a_stopped_server_is_left_stopped(self, setup):
        """Switching is not a reason to start a server somebody had stopped."""
        plugin, server, installation, _ = setup
        server.running = False

        source = Replies()
        await plugin._switch(source, "2.0.78", "")

        assert installation.active_version == "2.0.78"
        assert server.starts == 0
        assert "switch.start_when_ready" in source.text

    async def test_a_successful_switch_tells_the_other_plugins(self, setup):
        """mod_manager picks releases by version and caches it at load."""
        plugin, server, installation, _ = setup
        await plugin._switch(Replies(), "2.0.78", "")
        assert ("version.changed", ("2.0.78",)) in server.events


def _slot_info(saves: SaveManager):
    from factorio_reforge.saves.manager import Slot

    info = Slot(id=1, comment="older world", created_at=0.0, size_bytes=1)
    info._tr = saves.tr
    return info
