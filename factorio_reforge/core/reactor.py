"""Decide what happens to each parsed line.

The order is fixed and matters: core reactions first (so startup state is
correct before anyone is told about it), then ``general_info`` for plugins that
want to veto or rewrite, then echo, then command dispatch, then the specific
events. A plugin that clears :attr:`InfoActionFlag.PROCESS` in ``on_info`` stops
everything after the echo.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from factorio_reforge.command.source import (
    CommandSource,
    ConsoleCommandSource,
    PlayerCommandSource,
)
from factorio_reforge.core.handler import FactorioHandler
from factorio_reforge.core.info import Info, InfoActionFlag, InfoSource
from factorio_reforge.plugin import events as ev

if TYPE_CHECKING:
    from factorio_reforge.core.server import ReforgeServer


class InfoReactor:
    def __init__(self, server: "ReforgeServer", logger: Optional[logging.Logger] = None):
        self.server = server
        self.handler: FactorioHandler = server.handler
        self.logger = logger or logging.getLogger(__name__)

    async def react(self, info: Info) -> None:
        await self._core_reactions(info)

        await self.server.plugins.dispatch(ev.GENERAL_INFO, info)

        if info.should(InfoActionFlag.ECHO_TO_CONSOLE) and info.is_from_server:
            self.server.echo(info.raw_content)

        if not info.should(InfoActionFlag.PROCESS):
            return

        if info.source is InfoSource.CONSOLE:
            await self._handle_console(info)
        elif info.is_from_server:
            await self._handle_server(info)

    # -- core ----------------------------------------------------------------

    async def _core_reactions(self, info: Info) -> None:
        """State transitions the rest of the system depends on."""
        if not info.is_from_server:
            return

        if self.handler.is_startup_done(info) and not self.server.process.is_startup_done:
            self.server.process.mark_startup_done()
            self.logger.info("Server startup complete")
            await self.server.plugins.dispatch(ev.SERVER_STARTUP)

        if self.handler.is_rcon_ready(info):
            self.server.on_rcon_port_open()

        if self.handler.is_save_done(info):
            self.server.on_save_completed()

        for tag in self.handler.take_new_unknown_tags():
            self.logger.warning(
                "Unrecognised game event tag [%s]; treating it as a plain message. "
                "Please report it so a parser rule can be added.", tag
            )

    # -- routing -------------------------------------------------------------

    async def _handle_console(self, info: Info) -> None:
        """Console input is a FactorioReforge command, or it goes to the server."""
        text = info.content
        source = ConsoleCommandSource(self.server.interface, info)
        if self.server.commands.looks_like_command(text):
            if await self.server.commands.dispatch(source, text):
                return
            # An unknown !! command is a typo, not something Factorio should see
            # -- forwarding it would broadcast it to every player as chat.
            await source.reply(f"Unknown command: {text.split()[0]}")
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
            await self.server.commands.dispatch(source, info.content)
