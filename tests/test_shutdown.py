"""Shutdown ordering: the server must stop before FactorioReforge exits.

Both behaviours here were real bugs. Ctrl-C on an interactive terminal left the
server running with no console attached, and even when a shutdown did start,
``wait_for_exit`` returned early enough that ``asyncio.run`` cancelled it
mid-stop.
"""

import asyncio
import sys

import pytest

from factorio_reforge.core.console import ConsoleReader

pytestmark = pytest.mark.asyncio


class FakeServer:
    """Just enough of ReforgeServer to exercise the exit ordering."""

    def __init__(self, stop_delay: float = 0.05):
        self.stop_delay = stop_delay
        self.stopped = False
        self.stop_finished_at = None
        self._exiting = asyncio.Event()
        self._exited = asyncio.Event()
        self.shutdown_calls = 0

    async def shutdown(self, *, stop_server: bool = True) -> None:
        self.shutdown_calls += 1
        if self._exiting.is_set():
            await self._exited.wait()
            return
        self._exiting.set()
        try:
            if stop_server:
                await asyncio.sleep(self.stop_delay)
                self.stopped = True
                self.stop_finished_at = asyncio.get_running_loop().time()
        finally:
            self._exited.set()

    async def wait_for_exit(self) -> None:
        await self._exited.wait()


class TestExitOrdering:
    async def test_wait_for_exit_returns_only_after_the_server_stopped(self):
        """The bug: it returned as soon as shutdown *started*."""
        server = FakeServer(stop_delay=0.1)
        asyncio.create_task(server.shutdown())
        await server.wait_for_exit()
        assert server.stopped, "returned while the server was still stopping"

    async def test_a_shutdown_scheduled_as_a_task_is_not_cancelled_early(self):
        """Reproduces what asyncio.run() did to the fire-and-forget task."""
        server = FakeServer(stop_delay=0.1)

        async def main():
            asyncio.create_task(server.shutdown())
            await server.wait_for_exit()

        await asyncio.wait_for(main(), timeout=5)
        assert server.stopped

    async def test_a_second_shutdown_waits_for_the_first(self):
        """Two Ctrl-C presses must not let main() return mid-save."""
        server = FakeServer(stop_delay=0.1)
        first = asyncio.create_task(server.shutdown())
        await asyncio.sleep(0.01)
        await server.shutdown()
        assert server.stopped
        await first

    async def test_shutdown_completes_even_if_stopping_raises(self):
        """A failure while stopping must not hang main() forever."""

        class Exploding(FakeServer):
            async def shutdown(self, *, stop_server=True):
                if self._exiting.is_set():
                    await self._exited.wait()
                    return
                self._exiting.set()
                try:
                    raise RuntimeError("stop failed")
                finally:
                    self._exited.set()

        server = Exploding()
        with pytest.raises(RuntimeError):
            await server.shutdown()
        await asyncio.wait_for(server.wait_for_exit(), timeout=1)


class TestConsoleInterrupt:
    """prompt_toolkit consumes Ctrl-C in raw mode, so no signal is ever raised."""

    async def test_keyboard_interrupt_requests_shutdown(self):
        fired = asyncio.Event()

        class FakeSession:
            async def prompt_async(self, prompt):
                raise KeyboardInterrupt

        reader = ConsoleReader(_unused_line, on_interrupt=fired.set)
        await reader._run_prompt_toolkit(FakeSession())
        assert fired.is_set(), "Ctrl-C must ask for shutdown, not just close the console"

    async def test_ctrl_d_requests_shutdown(self):
        fired = asyncio.Event()

        class FakeSession:
            async def prompt_async(self, prompt):
                raise EOFError

        reader = ConsoleReader(_unused_line, on_interrupt=fired.set)
        await reader._run_prompt_toolkit(FakeSession())
        assert fired.is_set()

    async def test_no_handler_warns_rather_than_silently_stranding_the_server(self, caplog):
        class FakeSession:
            async def prompt_async(self, prompt):
                raise KeyboardInterrupt

        reader = ConsoleReader(_unused_line)
        with caplog.at_level("WARNING"):
            await reader._run_prompt_toolkit(FakeSession())
        # Constructed without a translator, so the key itself is the message --
        # which is the documented fallback, and still names the problem.
        assert "console_orphaned" in caplog.text


class TestNonInteractiveEof:
    async def test_piped_eof_does_not_shut_the_server_down(self, monkeypatch):
        """`StandardInput=null` under systemd must not kill the server on boot."""
        fired = asyncio.Event()
        reader = ConsoleReader(_unused_line, on_interrupt=fired.set)
        monkeypatch.setattr(type(reader), "interactive", property(lambda self: False))
        monkeypatch.setattr(sys.stdin, "readline", lambda: "", raising=False)
        await asyncio.wait_for(reader._run_plain(), timeout=5)
        assert not fired.is_set(), "a closed pipe only means there is no console"

    async def test_terminal_eof_does_shut_it_down(self, monkeypatch):
        """Ctrl-D at a real terminal is a person asking to leave."""
        fired = asyncio.Event()
        reader = ConsoleReader(_unused_line, on_interrupt=fired.set)
        monkeypatch.setattr(type(reader), "interactive", property(lambda self: True))
        monkeypatch.setattr(sys.stdin, "readline", lambda: "", raising=False)
        await asyncio.wait_for(reader._run_plain(), timeout=5)
        assert fired.is_set()


async def _unused_line(line: str) -> None:
    raise AssertionError("no line should be delivered in these tests")


class TestTerminalColour:
    """Colour must never reach a pipe: it corrupts logs and breaks greps."""

    def test_disabled_when_not_a_tty(self):
        from factorio_reforge.core.terminal import supports_colour

        class NotATty:
            def isatty(self):
                return False

        assert supports_colour(NotATty()) is False

    def test_no_color_env_wins_over_a_tty(self, monkeypatch):
        from factorio_reforge.core.terminal import supports_colour

        class Tty:
            def isatty(self):
                return True

        monkeypatch.setenv("NO_COLOR", "1")
        assert supports_colour(Tty()) is False

    def test_dumb_terminals_get_no_colour(self, monkeypatch):
        from factorio_reforge.core.terminal import supports_colour

        class Tty:
            def isatty(self):
                return True

        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        assert supports_colour(Tty()) is False

    def test_a_disabled_palette_returns_the_text_unchanged(self):
        from factorio_reforge.core.terminal import ORANGE, Palette

        plain = Palette(False)
        assert plain("hello", ORANGE) == "hello"
        assert plain.orange("hello") == "hello"

    def test_the_formatter_emits_no_escapes_when_colour_is_off(self):
        import logging

        from factorio_reforge.core.terminal import ColourFormatter, Palette

        formatter = ColourFormatter(Palette(False))
        record = logging.LogRecord("factorio", logging.ERROR, "", 0, "boom", (), None)
        assert "\033" not in formatter.format(record)

    def test_the_banner_is_plain_without_colour(self):
        from factorio_reforge.core.terminal import Palette, banner

        assert "\033" not in banner("0.1.0", "hint", Palette(False))
