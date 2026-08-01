"""ServerProcess tests, driven against tests/fake_factorio.py."""

import asyncio
import sys
from pathlib import Path

import pytest

from factorio_reforge.core.handler import FactorioHandler
from factorio_reforge.core.process import ServerProcess, ServerState

FAKE = Path(__file__).parent / "fake_factorio.py"
pytestmark = pytest.mark.asyncio


class Collector:
    def __init__(self):
        self.lines: list[str] = []
        self.handler = FactorioHandler()
        self.startup = asyncio.Event()

    async def __call__(self, line: str) -> None:
        self.lines.append(line.rstrip("\n"))
        if self.handler.is_startup_done(self.handler.parse_server_stdout(line)):
            self.startup.set()

    def joined(self) -> str:
        return "\n".join(self.lines)


def make(collector, env_extra=None, **kwargs) -> ServerProcess:
    cmd = [sys.executable, str(FAKE)]
    proc = ServerProcess(cmd, FAKE.parent, collector, **kwargs)
    if env_extra:
        # ServerProcess does not take env; the fake reads os.environ of the
        # parent, so tests set it via monkeypatch instead.
        raise NotImplementedError
    return proc


async def wait_startup(collector, timeout=10.0):
    await asyncio.wait_for(collector.startup.wait(), timeout)


class TestLifecycle:
    async def test_start_reaches_startup_and_quit_stops_cleanly(self):
        c = Collector()
        p = make(c)
        await p.start()
        assert p.state is ServerState.RUNNING
        await wait_startup(c)
        p.mark_startup_done()
        assert p.is_startup_done
        assert p.pid is not None and p.uptime is not None

        assert await p.stop() is True
        assert p.state is ServerState.STOPPED
        assert "Goodbye" in c.joined()
        assert "Saving progress: 100.000000%" in c.joined(), "quit path must save the map"
        await p.cleanup()

    async def test_stdout_arrives_promptly_rather_than_in_one_burst(self):
        """The whole event design rests on lines not sitting in a pipe buffer."""
        c = Collector()
        p = make(c)
        await p.start()
        await wait_startup(c, timeout=5.0)
        seen_before_write = len(c.lines)
        await p.write("/players")
        await asyncio.sleep(0.5)
        assert any("Players (0):" in line for line in c.lines[seen_before_write:])
        await p.stop()
        await p.cleanup()

    async def test_double_start_is_rejected(self):
        c = Collector()
        p = make(c)
        await p.start()
        with pytest.raises(RuntimeError):
            await p.start()
        await p.stop()
        await p.cleanup()

    async def test_write_after_stop_raises_instead_of_silently_dropping(self):
        c = Collector()
        p = make(c)
        await p.start()
        await wait_startup(c)
        await p.stop()
        with pytest.raises(RuntimeError):
            await p.write("/players")
        await p.cleanup()

    async def test_stop_on_a_stopped_process_is_a_noop(self):
        c = Collector()
        p = make(c)
        assert await p.stop() is True

    async def test_missing_working_directory_is_reported(self, tmp_path):
        c = Collector()
        p = ServerProcess([sys.executable, str(FAKE)], tmp_path / "nope", c)
        with pytest.raises(FileNotFoundError):
            await p.start()
        assert p.state is ServerState.STOPPED


class TestShutdownEscalation:
    async def test_sigint_takes_over_when_quit_is_ignored(self, monkeypatch):
        monkeypatch.setenv("FAKE_IGNORE_QUIT", "1")
        c = Collector()
        p = make(c, quit_timeout=1.0, sigint_timeout=10.0)
        await p.start()
        await wait_startup(c)
        assert await p.stop() is True
        assert "Received SIGINT" in c.joined()
        assert "Saving progress" in c.joined(), "SIGINT path still saves"
        await p.cleanup()

    async def test_sigterm_takes_over_when_quit_and_sigint_are_ignored(self, monkeypatch):
        monkeypatch.setenv("FAKE_IGNORE_QUIT", "1")
        monkeypatch.setenv("FAKE_IGNORE_SIGINT", "1")
        c = Collector()
        p = make(c, quit_timeout=1.0, sigint_timeout=1.0, sigterm_timeout=10.0)
        await p.start()
        await wait_startup(c)
        assert await p.stop() is True
        assert p.return_code is not None
        await p.cleanup()

    async def test_kill_is_immediate(self):
        c = Collector()
        p = make(c)
        await p.start()
        await wait_startup(c)
        await p.kill()
        await p.wait_until_stopped(timeout=10)
        assert "Goodbye" not in c.joined(), "SIGKILL cannot save"
        await p.cleanup()


class TestStdinStaysOpen:
    async def test_process_survives_stdin_eof_so_we_never_close_it(self):
        """Mirrors real 2.0.77: EOF on stdin logs an error and changes nothing.

        Documenting it as a test keeps anyone from "optimising" shutdown into
        closing the pipe, which would strand a live server with no control channel.
        """
        c = Collector()
        p = make(c)
        await p.start()
        await wait_startup(c)
        p._proc.stdin.close()
        await asyncio.sleep(1.0)
        assert p.return_code is None, "server must still be running after stdin EOF"
        assert "Got EOF on stdin" in c.joined()
        await p.kill()
        await p.cleanup()


class TestSecretRedaction:
    """Logs are the first thing people paste when asking for help."""

    def test_rcon_password_is_masked(self):
        from factorio_reforge.core.process import redact_command

        rendered = redact_command(
            ["./factorio", "--start-server", "s.zip", "--rcon-password", "hunter2"]
        )
        assert "hunter2" not in rendered
        assert rendered.endswith("--rcon-password <redacted>")

    def test_ordinary_arguments_survive(self):
        from factorio_reforge.core.process import redact_command

        assert redact_command(["./factorio", "--port", "34197"]) == "./factorio --port 34197"

    def test_a_trailing_secret_flag_with_no_value_does_not_crash(self):
        from factorio_reforge.core.process import redact_command

        assert redact_command(["./factorio", "--rcon-password"]) == "./factorio --rcon-password"
