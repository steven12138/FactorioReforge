"""Own the Factorio child process: spawn it, read its output, stop it.

Two behaviours here come straight from measuring a real 2.0.77 server rather
than from the docs (``docs/M0-findings.md``):

* stdout is line-flushed even when it is a pipe, so no pty or ``stdbuf`` dance
  is needed -- a plain asyncio pipe delivers ``[CHAT]`` lines within one frame.
* closing stdin does **not** stop the server. It logs ``Got EOF on stdin`` and
  keeps running, having lost its only command channel. So stdin stays open for
  the whole lifetime and shutdown escalates through signals instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

LineCallback = Callable[[str], Awaitable[None]]

#: Flags whose *value* must never reach a log file. Factorio redacts these in
#: its own output; logging the command verbatim would undo that, and logs are
#: the first thing people paste when asking for help.
SECRET_FLAGS = frozenset({"--rcon-password", "--password", "--token"})


def redact_command(command: Sequence[str]) -> str:
    """Render a command line with secret argument values masked."""
    parts: list[str] = []
    redact_next = False
    for argument in command:
        if redact_next:
            parts.append("<redacted>")
            redact_next = False
            continue
        parts.append(argument)
        redact_next = argument in SECRET_FLAGS
    return " ".join(parts)


class ServerState(enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    """Process is up but the map has not finished loading."""
    STARTUP_DONE = "startup_done"
    """Reached ``to(InGame)``; players can connect."""
    STOPPING = "stopping"


class ServerProcess:
    """A single Factorio child process and its I/O pumps."""

    def __init__(
        self,
        command: Sequence[str],
        working_directory: Path,
        on_line: LineCallback,
        *,
        logger: logging.Logger | None = None,
        tr: Callable[..., str] | None = None,
        quit_timeout: float = 60.0,
        sigint_timeout: float = 30.0,
        sigterm_timeout: float = 15.0,
        encoding: str = "utf-8",
    ) -> None:
        self.command = list(command)
        self.working_directory = Path(working_directory)
        self.on_line = on_line
        self.logger = logger or logging.getLogger(__name__)
        #: Translates log lines. Falls back to the key when absent, which keeps
        #: ServerProcess usable standalone -- the tests construct it directly.
        self.tr = tr or (lambda key, **kwargs: key)
        self.quit_timeout = quit_timeout
        self.sigint_timeout = sigint_timeout
        self.sigterm_timeout = sigterm_timeout
        self.encoding = encoding

        self._proc: asyncio.subprocess.Process | None = None
        self._pumps: list[asyncio.Task] = []
        self._state = ServerState.STOPPED
        self._stopped = asyncio.Event()
        self._stopped.set()
        self._start_time: float | None = None
        self._stdin_lock = asyncio.Lock()

    # -- state ---------------------------------------------------------------

    @property
    def state(self) -> ServerState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state not in (ServerState.STOPPED,)

    @property
    def is_startup_done(self) -> bool:
        return self._state is ServerState.STARTUP_DONE

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def uptime(self) -> float | None:
        return None if self._start_time is None else time.monotonic() - self._start_time

    @property
    def return_code(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    def mark_startup_done(self) -> None:
        """Called by the reactor once the ``to(InGame)`` marker is seen."""
        if self._state is ServerState.RUNNING:
            self._state = ServerState.STARTUP_DONE

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        if self.is_running:
            raise RuntimeError("server is already running")
        if not self.working_directory.is_dir():
            raise FileNotFoundError(f"working directory does not exist: {self.working_directory}")

        self.logger.info(self.tr("log.starting_server",
                                 command=redact_command(self.command)))
        self._state = ServerState.STARTING
        self._stopped.clear()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=str(self.working_directory),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Its own process group, so a Ctrl-C in our terminal does not
                # race us to the child -- we want to drive shutdown ourselves.
                start_new_session=True,
            )
        except OSError:
            self._state = ServerState.STOPPED
            self._stopped.set()
            raise

        self._start_time = time.monotonic()
        self._state = ServerState.RUNNING
        self._pumps = [
            asyncio.create_task(self._pump(self._proc.stdout, "stdout")),
            asyncio.create_task(self._pump(self._proc.stderr, "stderr")),
            asyncio.create_task(self._reap()),
        ]
        self.logger.info(self.tr("log.server_started", pid=self._proc.pid))

    async def _pump(self, stream: asyncio.StreamReader, name: str) -> None:
        while True:
            try:
                raw = await stream.readline()
            except (asyncio.LimitOverrunError, ValueError):
                # A single absurdly long line must not kill the pump.
                self.logger.warning("Dropped an over-long line on %s", name)
                continue
            if not raw:
                return
            try:
                await self.on_line(raw.decode(self.encoding, errors="replace"))
            except Exception:
                self.logger.exception("Error handling server output line")

    async def _reap(self) -> None:
        assert self._proc is not None
        await self._proc.wait()
        self.logger.info(self.tr("log.server_exited", code=self._proc.returncode))
        # Let the output pumps drain whatever is still buffered before we
        # declare the server stopped; "Goodbye" tends to arrive right at the end.
        await asyncio.sleep(0.2)
        self._state = ServerState.STOPPED
        self._start_time = None
        self._stopped.set()

    # -- input ---------------------------------------------------------------

    async def write(self, text: str) -> None:
        """Send one line to the server's stdin. Never closes the pipe."""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise RuntimeError("cannot write: server is not running")
        if not text.endswith("\n"):
            text += "\n"
        async with self._stdin_lock:
            proc.stdin.write(text.encode(self.encoding, errors="replace"))
            await proc.stdin.drain()

    # -- shutdown ------------------------------------------------------------

    async def stop(self) -> bool:
        """Shut the server down as gently as possible, escalating if it resists.

        ``/quit`` and ``SIGINT`` both save the map first; ``SIGTERM`` and
        ``SIGKILL`` do not, so they are strictly last resorts.
        """
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return True

        self._state = ServerState.STOPPING

        with contextlib.suppress(Exception):
            self.logger.info(self.tr("log.sending_quit"))
            await self.write("/quit")
            if await self._wait_exit(self.quit_timeout):
                return True

        self.logger.warning(self.tr("log.quit_timeout", seconds=int(self.quit_timeout)))
        if await self._signal_and_wait(signal.SIGINT, self.sigint_timeout):
            return True

        self.logger.warning(self.tr("log.sigint_timeout"))
        if await self._signal_and_wait(signal.SIGTERM, self.sigterm_timeout):
            return True

        self.logger.error(self.tr("log.sigterm_timeout"))
        await self._signal_and_wait(signal.SIGKILL, 10.0)
        return proc.returncode is not None

    async def kill(self) -> None:
        """Immediate SIGKILL. Loses everything since the last save."""
        await self._signal_and_wait(signal.SIGKILL, 10.0)

    async def _signal_and_wait(self, sig: signal.Signals, timeout: float) -> bool:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return True
        try:
            # Signal the whole group: start_new_session put the child in its own.
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(sig)
        return await self._wait_exit(timeout)

    async def _wait_exit(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._stopped.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def wait_until_stopped(self, timeout: float | None = None) -> None:
        if timeout is None:
            await self._stopped.wait()
        else:
            await asyncio.wait_for(self._stopped.wait(), timeout)

    async def cleanup(self) -> None:
        for task in self._pumps:
            task.cancel()
        for task in self._pumps:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._pumps.clear()
        self._proc = None
