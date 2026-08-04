"""``!!FR help`` with a lot of plugins loaded.

The grouped form was a blank line, a header and a line per command for every
plugin: past sixty lines with twenty-one of them, which in a chat box means the
plugins near the end of the alphabet scroll off the top and are, in practice,
undiscoverable.
"""

from pathlib import Path

import pytest

from factorio_reforge.command.manager import CommandManager
from factorio_reforge.command.source import CommandSource
from factorio_reforge.i18n import Translator
from factorio_reforge.permission import PermissionLevel
from factorio_reforge.plugin import builtin
from factorio_reforge.plugin.registry import HelpMessage

pytestmark = pytest.mark.asyncio


class Replies(CommandSource):
    def __init__(self, server, player=None):
        super().__init__(server, None)
        self.lines: list[str] = []
        self._player = player

    @property
    def player(self):
        return self._player

    @property
    def permission_level(self):
        return PermissionLevel.OWNER

    async def reply(self, text: str) -> None:
        self.lines.append(str(text))

    def __str__(self) -> str:
        return "test"


class FakePlugin:
    def __init__(self, plugin_id):
        class Meta:
            id = plugin_id
            name = plugin_id.replace("_", " ").title()
            version = "1.0.0"
            author = "test"
            description = f"does {plugin_id} things"

        self.metadata = Meta()


class FakePlugins:
    """A registry holding one help entry per plugin, as the real one does."""

    def __init__(self, ids):
        self._plugins = {name: FakePlugin(name) for name in ids}

        class Registry:
            help_messages = [
                HelpMessage(plugin_id=name, prefix=f"!!{name[:4]} <arg>",
                            message=f"what {name} does", permission=0)
                for name in ids
            ]

        self.registry = Registry()

    def get(self, plugin_id):
        return self._plugins.get(plugin_id)


class FakeServer:
    def __init__(self, plugin_ids):
        self.plugins = FakePlugins(plugin_ids)
        # A real translator: the help index asks each plugin's catalogue for a
        # `description` before falling back to the metadata, and a stub that
        # cannot answer that would test a different code path.
        self.i18n = Translator()
        self.i18n.load_directory(
            Path(__file__).resolve().parent.parent / "factorio_reforge" / "lang"
        )

        class Cfg:
            command_prefix = "!!"

        self.config = Cfg()

    def tr(self, key, **kwargs):
        return key + ("|" + ",".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
                      if kwargs else "")


TWENTY_ONE = [
    "alerts", "auto_snapshot", "blueprints", "calculator", "crash_doctor",
    "join_motd", "leaderboard", "mail", "map_render", "mod_manager", "power",
    "production", "research", "server_admin", "server_utils", "telegram_bridge",
    "trains", "ups_watch", "vote", "warp", "world_watch",
]


@pytest.fixture
def commands():
    server = FakeServer(TWENTY_ONE)
    manager = CommandManager(prefix="!!")
    manager.register("@core", builtin.build(server))
    manager.register("@core", builtin.build_help_command(server))
    return manager, server


async def run(commands, text, player=None):
    manager, server = commands
    source = Replies(server, player)
    assert await manager.dispatch(source, text), f"{text!r} was not claimed"
    return source.lines


class TestIndex:
    async def test_a_player_gets_one_page(self, commands):
        lines = await run(commands, "!!FR help", player="alice")
        assert len(lines) < 20, "a chat box cannot show more than this"
        assert any("help.more" in line for line in lines)

    async def test_one_line_per_plugin(self, commands):
        """Not a block each: that is what made it sixty lines."""
        lines = await run(commands, "!!FR help", player="alice")
        listed = [line for line in lines if line.startswith("  alerts")]
        assert len(listed) == 1
        assert "!!aler" in listed[0]

    async def test_the_console_is_not_paginated(self, commands):
        """It has scrollback; the in-game chat box does not."""
        lines = await run(commands, "!!FR help")
        assert all("help.more" not in line for line in lines)
        for plugin_id in TWENTY_ONE:
            assert any(line.startswith(f"  {plugin_id} ") for line in lines), plugin_id

    async def test_later_pages_reach_the_end_of_the_alphabet(self, commands):
        first = await run(commands, "!!FR help", player="alice")
        assert not any("world_watch" in line for line in first)
        last = await run(commands, "!!FR help 3", player="alice")
        assert any("world_watch" in line for line in last)

    async def test_a_page_past_the_end_is_clamped(self, commands):
        lines = await run(commands, "!!FR help 99", player="alice")
        assert any("world_watch" in line for line in lines)

    async def test_core_commands_only_appear_on_the_first_page(self, commands):
        first = await run(commands, "!!FR help", player="alice")
        second = await run(commands, "!!FR help 2", player="alice")
        assert any("help.core_header" in line for line in first)
        assert all("help.core_header" not in line for line in second)

    async def test_backups_are_in_the_index(self, commands):
        """They live under their own prefix, so the core loop cannot reach them."""
        lines = await run(commands, "!!FR help")
        assert any(line.strip().startswith("!!qb") for line in lines)


class TestLookup:
    async def test_a_plugin_name_still_shows_that_plugin(self, commands):
        lines = await run(commands, "!!FR help calculator")
        assert any("calculator" in line for line in lines)
        assert any("help.plugin_version" in line for line in lines)

    async def test_a_command_name_finds_its_plugin(self, commands):
        """Browsing an index is the fallback, not the interface."""
        lines = await run(commands, "!!FR help aler")
        assert any("help.matches" in line for line in lines)
        assert any("alerts" in line for line in lines)

    async def test_the_prefix_is_optional_when_searching(self, commands):
        """Typing the prefix is the natural thing to do; it should not miss."""
        with_prefix = await run(commands, "!!FR help !!aler")
        assert any("alerts" in line for line in with_prefix)
        assert all("help.no_match" not in line for line in with_prefix)

    async def test_a_miss_says_so_and_points_somewhere(self, commands):
        lines = await run(commands, "!!FR help zzzznothing")
        assert any("help.no_match" in line for line in lines)

    async def test_a_number_is_a_page_not_a_plugin(self, commands):
        lines = await run(commands, "!!FR help 2", player="alice")
        assert any("help.header_counts" in line for line in lines)
        assert all("help.no_match" not in line for line in lines)


class TestShortcut:
    """``!!help``, because help is what you type when you know nothing else."""

    async def test_it_is_the_same_index(self, commands):
        assert await run(commands, "!!help") == await run(commands, "!!FR help")

    async def test_it_pages(self, commands):
        short = await run(commands, "!!help 2", player="alice")
        long = await run(commands, "!!FR help 2", player="alice")
        assert short == long
        assert any("help.header_counts" in line for line in short)

    async def test_it_looks_plugins_up(self, commands):
        lines = await run(commands, "!!help calculator")
        assert any("help.plugin_version" in line for line in lines)

    async def test_it_searches(self, commands):
        lines = await run(commands, "!!help aler")
        assert any("help.matches" in line for line in lines)

    async def test_the_hints_name_the_short_form(self, commands):
        """Telling someone to type the long one after they found the short one."""
        lines = await run(commands, "!!help", player="alice")
        hint = next(line for line in lines if "help.more" in line)
        assert "prefix=!!help" in hint

    async def test_the_core_row_still_names_the_framework_tree(self, commands):
        """`!!FR` is where status, plugin and server live; that is not `!!help`."""
        lines = await run(commands, "!!help")
        assert any(line.strip().startswith("!!FR") for line in lines)
