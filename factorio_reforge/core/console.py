"""Read the operator's terminal without blocking the event loop.

``input()`` would block the whole loop, so stdin is read on a thread and lines
are handed back through a queue. prompt_toolkit is used when it is installed --
it keeps the input line from being chewed up by server output scrolling past --
and a plain reader is used otherwise, so the dependency stays optional.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Awaitable, Callable, Optional

LineHandler = Callable[[str], Awaitable[None]]


class ConsoleReader:
    def __init__(
        self,
        on_line: LineHandler,
        *,
        prompt: str = "",
        logger: Optional[logging.Logger] = None,
    ):
        self.on_line = on_line
        self.prompt = prompt
        self.logger = logger or logging.getLogger(__name__)
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

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
            except (EOFError, KeyboardInterrupt):
                self.logger.info("Console closed")
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
        while not self._stop.is_set():
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Console read failed")
                return
            if line == "":
                self.logger.info("Console stdin closed")
                return
            await self._deliver(line.rstrip("\r\n"))

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
