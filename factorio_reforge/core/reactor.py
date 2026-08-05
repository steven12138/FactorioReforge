"""Decide what happens to each parsed line.

The order is fixed and matters: core reactions first (so startup state is
correct before anyone is told about it), then ``general_info`` for plugins that
want to veto or rewrite, then echo, then command dispatch, then the specific
events. A plugin that clears :attr:`InfoActionFlag.PROCESS` in ``on_info`` stops
everything after the echo.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from factorio_reforge.command.source import (
    CommandSource,
    ConsoleCommandSource,
    PlayerCommandSource,
)
from factorio_reforge.core import luahooks
from factorio_reforge.core.handler import FactorioHandler
from factorio_reforge.core.info import Info, InfoActionFlag, InfoSource
from factorio_reforge.plugin import events as ev

if TYPE_CHECKING:
    from factorio_reforge.core.server import ReforgeServer


def _without_elapsed(info: Info) -> str:
    """Drop Factorio's leading uptime, which our timestamp column supersedes.

    Everything else is left alone -- the ``Foo.cpp:808:`` references are worth
    keeping for anyone reporting a Factorio bug.
    """
    if info.elapsed is None:
        return info.raw_content
    return _ELAPSED_PREFIX.sub("", info.raw_content, count=1)


_ELAPSED_PREFIX = re.compile(r"^\s*\d+\.\d+ ")


class InfoReactor:
    def __init__(self, server: ReforgeServer, logger: logging.Logger | None = None):
        self.server = server
        self.handler: FactorioHandler = server.handler
        self.logger = logger or logging.getLogger(__name__)
        #: In-flight command tasks, kept referenced so none is collected early.
        self._running: set[asyncio.Task] = set()

    async def react(self, info: Info) -> None:
        # Bridged events are machine traffic, not something a person reads.
        # Handled and dropped before the echo, or every research completion
        # would put a line of JSON in the operator's console.
        if info.is_from_server and await self._handle_lua_event(info):
            return

        await self._core_reactions(info)

        await self.server.plugins.dispatch(ev.GENERAL_INFO, info)

        if info.should(InfoActionFlag.ECHO_TO_CONSOLE) and info.is_from_server:
            self.server.echo(_without_elapsed(info), info)

        if not info.should(InfoActionFlag.PROCESS):
            return

        if info.source is InfoSource.CONSOLE:
            await self._handle_console(info)
        elif info.is_from_server:
            await self._handle_server(info)

    async def _handle_lua_event(self, info: Info) -> bool:
        """True if this line was a bridged Factorio event, now dispatched."""
        payload = luahooks.parse_line(info.content or "")
        if payload is None:
            return False
        self.logger.debug("lua event: %s", payload)
        await self.server.plugins.dispatch(ev.LUA_EVENT, payload)
        return True

    # -- core ----------------------------------------------------------------

    async def _core_reactions(self, info: Info) -> None:
        """State transitions the rest of the system depends on."""
        if not info.is_from_server:
            return

        self.server.loglens.observe(info)

        if self.handler.is_startup_done(info) and not self.server.process.is_startup_done:
            self.server.process.mark_startup_done()
            self.logger.info(self.server.tr("log.startup_complete"))
            self.server.schedule_startup_report()
            await self.server.plugins.dispatch(ev.SERVER_STARTUP)

        if self.handler.is_rcon_ready(info):
            self.server.on_rcon_port_open()

        if self.handler.is_save_done(info):
            self.server.on_save_completed()

        for tag in self.handler.take_new_unknown_tags():
            self.logger.warning(self.server.tr("log.unknown_tag", tag=tag))

    def _run_command(self, source: CommandSource, text: str) -> None:
        """Run a command in its own task, never inline.

        This is not a style choice. Running it inline deadlocks: the line
        arrives on the stdout pump, the handler runs there, and a handler like
        ``!!qb make`` then waits for Factorio to print "Saving finished" --
        which only the pump can read, and the pump is inside the handler. The
        server looks frozen, further commands are ignored, and it clears only
        when the save times out a hundred and twenty seconds later.

        Parsing and event dispatch stay inline so line order is preserved; only
        the command, which may take arbitrarily long, is handed to a task.
        """
        task = asyncio.create_task(self._dispatch(source, text))
        # Hold a reference: a task with no strong reference can be collected
        # mid-flight, which loses the command silently.
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def _dispatch(self, source: CommandSource, text: str) -> None:
        try:
            if await self.server.commands.dispatch(source, text):
                return
            # An unknown !! command is a typo, not something Factorio should see
            # -- forwarding it would broadcast it to every player as chat.
            await source.reply(
                self.server.tr("error.unknown_command", command=text.split()[0])
            )
        except Exception:
            self.logger.exception("Command %r failed", text)

    # -- routing -------------------------------------------------------------

    async def _handle_console(self, info: Info) -> None:
        """Console input is a FactorioReforge command, or it goes to the server."""
        text = info.content
        source = ConsoleCommandSource(self.server.interface, info)
        if self.server.commands.looks_like_command(text):
            self._run_command(source, text)
            return

        await self.server.plugins.dispatch(ev.USER_INFO, info)

        if info.should(InfoActionFlag.SEND_TO_SERVER):
            if not self.server.process.is_running:
                self.logger.warning("Server is not running; discarded: %s", text)
                return
            await self.server.process.write(text)

    async def _handle_server(self, info: Info) -> None:
        if info.tag == "JOIN" and info.player:
            await self.server.plugins.dispatch(ev.PLAYER_JOINED, info.player, info)
        elif info.tag == "LEAVE" and info.player:
            await self.server.plugins.dispatch(ev.PLAYER_LEFT, info.player, info)
        elif info.tag == "DEATH":
            await self.server.plugins.dispatch(ev.PLAYER_DEATH, info.player, info)

        if not info.is_user:
            return

        await self.server.plugins.dispatch(ev.USER_INFO, info)

        if info.player and self.server.commands.looks_like_command(info.content):
            source: CommandSource = PlayerCommandSource(
                self.server.interface, info, info.player
            )
            self._run_command(source, info.content)
