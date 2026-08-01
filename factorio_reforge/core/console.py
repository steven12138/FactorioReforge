"""Read the operator's terminal without blocking the event loop.

``input()`` would block the whole loop, so stdin is read on a thread and lines
are handed back through a callback. prompt_toolkit is used when it is installed
and stdin is a terminal -- it keeps the input line from being chewed up by
server output scrolling past -- and a plain reader is used otherwise, so the
dependency stays optional.

**Ctrl-C is not a signal here.** prompt_toolkit puts the terminal in raw mode,
so the tty driver never generates SIGINT: prompt_toolkit reads the ``\\x03``
byte itself and raises ``KeyboardInterrupt`` inside ``prompt_async``. A signal
handler on the event loop will therefore never fire, and treating that
exception as "the console closed" leaves the whole server running with no way
to talk to it. The interactive reader routes it to ``on_interrupt`` instead.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable

LineHandler = Callable[[str], Awaitable[None]]
InterruptHandler = Callable[[], None]


class ConsoleReader:
    def __init__(
        self,
        on_line: LineHandler,
        *,
        on_interrupt: InterruptHandler | None = None,
        prompt: str = "",
        logger: logging.Logger | None = None,
    ):
        self.on_line = on_line
        #: Called when the operator asks to leave from an interactive terminal
        #: (Ctrl-C or Ctrl-D). Not called for a plain EOF on a pipe -- see
        #: :meth:`_run_plain`.
        self.on_interrupt = on_interrupt
        self.prompt = prompt
        self.logger = logger or logging.getLogger(__name__)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def interactive(self) -> bool:
        return sys.stdin.isatty()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        session = _make_prompt_session(self.prompt)
        if session is not None:
            await self._run_prompt_toolkit(session)
        else:
            await self._run_plain()

    async def _run_prompt_toolkit(self, session) -> None:
        from prompt_toolkit.patch_stdout import patch_stdout

        while not self._stop.is_set():
            try:
                with patch_stdout():
                    line = await session.prompt_async(self.prompt)
            except KeyboardInterrupt:
                # Ctrl-C in raw mode. No signal was raised, so nothing else is
                # going to notice this; ask for shutdown here or the server
                # keeps running with the console gone.
                self.logger.info("Ctrl-C received")
                self._request_interrupt()
                return
            except EOFError:
                self.logger.info("Ctrl-D received")
                self._request_interrupt()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Console read failed; falling back to plain input")
                await self._run_plain()
                return
            await self._deliver(line)

    async def _run_plain(self) -> None:
        loop = asyncio.get_running_loop()
        interactive = self.interactive
        while not self._stop.is_set():
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Console read failed")
                return

            if line == "":
                # EOF. On a terminal a human pressed Ctrl-D and wants out; on a
                # pipe or /dev/null it only means there is no console, which is
                # exactly how an unattended systemd unit runs. Shutting down
                # there would make `StandardInput=null` kill the server on boot.
                if interactive:
                    self.logger.info("Console EOF")
                    self._request_interrupt()
                else:
                    self.logger.info("No console attached; input is closed")
                return

            await self._deliver(line.rstrip("\r\n"))

    def _request_interrupt(self) -> None:
        if self.on_interrupt is None:
            self.logger.warning(
                "Console closed but nothing is listening for it; "
                "the server is still running. Use !!FR exit or send SIGTERM."
            )
            return
        try:
            self.on_interrupt()
        except Exception:
            self.logger.exception("Interrupt handler failed")

    async def _deliver(self, line: str) -> None:
        if not line.strip():
            return
        try:
            await self.on_line(line)
        except Exception:
            self.logger.exception("Error handling console input")


def _make_prompt_session(prompt: str):
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
    except ImportError:
        return None
    if not sys.stdin.isatty():
        # Piped input: prompt_toolkit needs a terminal, plain reading does not.
        return None
    try:
        return PromptSession(history=InMemoryHistory())
    except Exception:
        return None
