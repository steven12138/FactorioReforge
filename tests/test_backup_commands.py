"""The ``!!qb`` tree, its ``!!save`` alias, and the two rings it addresses.

Backups are the one feature where a silent break costs a world, so the command
surface is exercised through the dispatcher rather than by calling handlers.
"""

import zipfile

import pytest

from factorio_reforge.command.manager import CommandManager
from factorio_reforge.command.source import CommandSource
from factorio_reforge.permission import PermissionLevel
from factorio_reforge.plugin import builtin
from factorio_reforge.saves.manager import SaveManager, SlotConfig

pytestmark = pytest.mark.asyncio


class Replies(CommandSource):
    """A source that records what was said back to it."""

    def __init__(self, server):
        super().__init__(server, None)
        self.lines: list[str] = []

    @property
    def permission_level(self):
        return PermissionLevel.OWNER

    async def reply(self, text: str) -> None:
        self.lines.append(str(text))

    def __str__(self) -> str:
        return "test"

    @property
    def text(self) -> str:
        return " ".join(self.lines)


class FakeServer:
    """Just enough of ReforgeServer for the backup tree."""

    def __init__(self, saves):
        self.saves = saves
        self.restored: list = []

        class Cfg:
            command_prefix = "!!"

            class saves:
                restore_countdown = 0.0

        self.config = Cfg()

    def tr(self, key, **kwargs):
        return key + (f" {kwargs}" if kwargs else "")

    async def create_snapshot(self, comment="", *, created_by="unknown", automatic=False):
        return await self.saves.create(comment, created_by=created_by, automatic=automatic)

    async def rollback(self, slot, *, countdown=0.0, requested_by="unknown"):
        info = self.saves.validate(slot)
        self.restored.append(str(slot))
        return info

    def abort_rollback(self):
        return False


def write_save(path, marker="world"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("level.dat", marker)
    return path


@pytest.fixture
def commands(tmp_path):
    saves = SaveManager(
        write_save(tmp_path / "saves" / "world.zip"), tmp_path / "snapshots",
        slots=[SlotConfig(0), SlotConfig(0)],
        auto_slots=[SlotConfig(0), SlotConfig(0)],
    )
    saves.load_index()
    server = FakeServer(saves)

    manager = CommandManager(prefix="!!")
    # Exactly what __main__ does: one staging dict, two names.
    staged: dict = {"slot": None, "at": 0.0, "by": ""}
    for alias in ("qb", "save"):
        manager.register("@core", builtin.build_save_commands(server, alias, staged))
    return manager, server


async def run(commands, text):
    manager, server = commands
    source = Replies(server)
    assert await manager.dispatch(source, text), f"{text!r} was not claimed"
    return source


class TestBothNames:
    async def test_qb_is_the_name(self, commands):
        _, server = commands
        await run(commands, "!!qb make named after QuickBackupM")
        assert server.saves.get(1).comment == "named after QuickBackupM"

    async def test_save_still_works(self, commands):
        """People have it in their fingers; a rename must not break a backup."""
        assert "save.created" in (await run(commands, "!!save make")).text

    async def test_staging_is_shared_between_the_names(self, commands):
        """Stage under one name, confirm under the other -- one restore."""
        _, server = commands
        await run(commands, "!!qb make keep")
        await run(commands, "!!save back 1")
        replies = await run(commands, "!!qb confirm")
        assert server.restored == ["1"]
        assert "save.restored" in replies.text

    async def test_confirming_nothing_says_so(self, commands):
        assert "nothing_staged" in (await run(commands, "!!qb confirm")).text


class TestRings:
    async def test_a_manual_backup_goes_to_the_numbered_ring(self, commands):
        _, server = commands
        await run(commands, "!!qb make by hand")
        assert server.saves.get(1).comment == "by hand"
        assert server.saves.get("a1") is None

    async def test_listing_shows_both_rings(self, commands):
        _, server = commands
        await run(commands, "!!qb make by hand")
        await server.create_snapshot("by timer", automatic=True)
        text = (await run(commands, "!!qb list")).text
        assert "save.auto_header" in text
        assert "by hand" in text and "by timer" in text

    async def test_an_automatic_slot_can_be_restored_by_reference(self, commands):
        _, server = commands
        await server.create_snapshot("by timer", automatic=True)
        await run(commands, "!!qb back a1")
        await run(commands, "!!qb confirm")
        assert server.restored == ["a1"]

    async def test_deleting_an_automatic_slot_leaves_the_manual_one(self, commands):
        _, server = commands
        await run(commands, "!!qb make by hand")
        await server.create_snapshot("by timer", automatic=True)
        await run(commands, "!!qb del a1")
        assert server.saves.get("a1") is None
        assert server.saves.get(1).comment == "by hand"

    async def test_a_bad_reference_is_explained(self, commands):
        assert "a1" in (await run(commands, "!!qb back nonsense")).text
