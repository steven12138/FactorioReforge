"""The read loop must never be blocked by a command it dispatched.

The bug this guards: `!!save make` typed in game arrives on the stdout pump,
its handler runs inline on that pump, and the handler waits for Factorio to
print "Saving finished" -- a line only the pump can read. The pump is inside
the handler, so nothing reads it. The server appears frozen, further commands
are ignored, and it clears only when the save times out two minutes later.
"""

import asyncio
import logging

import pytest

from factorio_reforge.core.handler import FactorioHandler
from factorio_reforge.core.reactor import InfoReactor

pytestmark = pytest.mark.asyncio


class FakeCommands:
    """A command manager whose handler waits for a later line to arrive."""

    def __init__(self):
        self.marker = asyncio.Event()
        self.finished = asyncio.Event()
        self.dispatched: list[str] = []

    def looks_like_command(self, text):
        return text.startswith("!!")

    async def dispatch(self, source, text):
        self.dispatched.append(text)
        # Exactly what !!save make does: wait for something the read loop has
        # not delivered yet.
        await asyncio.wait_for(self.marker.wait(), timeout=5)
        self.finished.set()
        return True


class FakePlugins:
    async def dispatch(self, *args, **kwargs):
        return None


class FakeProcess:
    is_running = True
    is_startup_done = True

    def mark_startup_done(self):
        pass

    async def write(self, text):
        pass


class FakeServer:
    def __init__(self, commands):
        self.handler = FactorioHandler()
        self.commands = commands
        self.plugins = FakePlugins()
        self.process = FakeProcess()
        self.interface = object()
        self.logger = logging.getLogger("test")
        self.echoed: list[str] = []
        self.loglens = _NullLens()

    def echo(self, line, info=None):
        self.echoed.append(line)

    def tr(self, key, /, *a, **kw):
        return key

    def on_rcon_port_open(self):
        pass

    def on_save_completed(self):
        self.commands.marker.set()

    def schedule_startup_report(self):
        pass


class _NullLens:
    def observe(self, info):
        pass


class TestReadLoopIsNeverBlocked:
    async def test_a_command_that_waits_for_a_later_line_still_completes(self):
        """The deadlock, reproduced end to end."""
        commands = FakeCommands()
        server = FakeServer(commands)
        reactor = InfoReactor(server, logging.getLogger("test"))

        # The command arrives on the read loop, as in-game chat does.
        await reactor.react(
            server.handler.parse_server_stdout(
                "2026-08-02 14:38:14 [CHAT] steven12138: !!save make"
            )
        )
        await asyncio.sleep(0)  # let the dispatched task start
        assert commands.dispatched == ["!!save make"]

        # The loop must still be free to deliver the line the handler awaits.
        await reactor.react(
            server.handler.parse_server_stdout(
                "   4.619 Info AppManager.cpp:419: Saving finished"
            )
        )

        await asyncio.wait_for(commands.finished.wait(), timeout=5)

    async def test_further_input_is_still_read_while_a_command_runs(self):
        """`!!FR` typed after a slow command must not be ignored."""
        commands = FakeCommands()
        server = FakeServer(commands)
        reactor = InfoReactor(server, logging.getLogger("test"))

        await reactor.react(server.handler.parse_console_input("!!save make"))
        await reactor.react(server.handler.parse_console_input("!!FR status"))
        await asyncio.sleep(0)

        assert commands.dispatched == ["!!save make", "!!FR status"]
        commands.marker.set()
        await asyncio.sleep(0)

    async def test_server_output_keeps_being_echoed_during_a_command(self):
        commands = FakeCommands()
        server = FakeServer(commands)
        reactor = InfoReactor(server, logging.getLogger("test"))

        await reactor.react(server.handler.parse_console_input("!!save make"))
        await reactor.react(
            server.handler.parse_server_stdout("   1.0 Hosting game at IP ADDR")
        )
        assert any("Hosting game" in line for line in server.echoed)
        commands.marker.set()
        await asyncio.sleep(0)

    async def test_a_failing_command_is_logged_and_does_not_escape(self, caplog):
        class Exploding(FakeCommands):
            async def dispatch(self, source, text):
                raise RuntimeError("boom")

        server = FakeServer(Exploding())
        reactor = InfoReactor(server, logging.getLogger("test"))
        with caplog.at_level("ERROR"):
            await reactor.react(server.handler.parse_console_input("!!boom"))
            await asyncio.sleep(0.05)
        assert "boom" in caplog.text
