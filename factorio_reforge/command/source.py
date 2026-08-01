"""Who issued a command, and where its reply should go."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Optional

from factorio_reforge.core.info import Info
from factorio_reforge.permission import CONSOLE_LEVEL, PermissionLevel

if TYPE_CHECKING:
    from factorio_reforge.plugin.interface import ServerInterface


class CommandSource(abc.ABC):
    """A command's origin. ``reply`` is what makes routing transparent to plugins."""

    def __init__(self, server: "ServerInterface", info: Optional[Info] = None):
        self.server = server
        self.info = info

    @property
    @abc.abstractmethod
    def permission_level(self) -> PermissionLevel: ...

    @property
    def player(self) -> Optional[str]:
        return None

    @abc.abstractmethod
    async def reply(self, text: str) -> None: ...

    def has_permission(self, level: "int | PermissionLevel") -> bool:
        return self.permission_level >= level

    def __str__(self) -> str:
        return type(self).__name__


class ConsoleCommandSource(CommandSource):
    """Typed into the FactorioReforge terminal. Always OWNER.

    Anyone at that terminal can already stop the process and edit the config, so
    gating them below OWNER would be theatre.
    """

    @property
    def permission_level(self) -> PermissionLevel:
        return CONSOLE_LEVEL

    async def reply(self, text: str) -> None:
        self.server.logger.info(text)

    def __str__(self) -> str:
        return "Console"


class PlayerCommandSource(CommandSource):
    """A player typing in the game chat."""

    def __init__(self, server: "ServerInterface", info: Info, player: str):
        super().__init__(server, info)
        self._player = player

    @property
    def player(self) -> Optional[str]:
        return self._player

    @property
    def permission_level(self) -> PermissionLevel:
        return self.server.get_permission_level(self._player)

    async def reply(self, text: str) -> None:
        await self.server.tell(self._player, text)

    def __str__(self) -> str:
        return f"Player {self._player}"


class PluginCommandSource(CommandSource):
    """Synthesised by a plugin, e.g. a Telegram message.

    The plugin states the level it has already authenticated, rather than the
    core guessing from a chat id it knows nothing about.
    """

    def __init__(
        self,
        server: "ServerInterface",
        level: PermissionLevel,
        name: str,
        reply_callback=None,
        info: Optional[Info] = None,
    ):
        super().__init__(server, info)
        self._level = level
        self._name = name
        self._reply_callback = reply_callback

    @property
    def permission_level(self) -> PermissionLevel:
        return self._level

    async def reply(self, text: str) -> None:
        if self._reply_callback is not None:
            await self._reply_callback(text)
        else:
            self.server.logger.info("[%s] %s", self._name, text)

    def __str__(self) -> str:
        return self._name
