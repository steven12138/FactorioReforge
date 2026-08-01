"""The structured form of one line of server / console output."""

from __future__ import annotations

import dataclasses
import enum
import itertools
from typing import Optional

_id_counter = itertools.count()


class InfoSource(enum.IntEnum):
    """Where an :class:`Info` came from."""

    SERVER = 0
    """A line read from the server's stdout/stderr."""

    CONSOLE = 1
    """A line typed into the FactorioReforge console."""

    PLUGIN = 2
    """Injected by a plugin, e.g. a message relayed from Telegram."""


class InfoKind(enum.Enum):
    """Which of the four shapes the server emits this line matched.

    See ``docs/M0-findings.md`` -- the shapes were sampled from a real 2.0.77
    headless server, and the fourth one (command output) has no markers at all,
    so it is what we fall back to rather than something we detect.
    """

    ENGINE_LOG = "engine_log"
    """``0.578 Info Foo.cpp:808: text`` -- level/source present."""

    ENGINE_PLAIN = "engine_plain"
    """``0.577 Hosting game at ...`` -- elapsed timestamp only."""

    GAME_EVENT = "game_event"
    """``2026-08-02 02:16:35 [CHAT] Alice: hi`` -- dated, tagged."""

    COMMAND_RESPONSE = "command_response"
    """``Players (0):`` -- no prefix whatsoever. The fallback."""

    CONSOLE_INPUT = "console_input"
    """Typed by the operator, never parsed as server output."""


class InfoActionFlag(enum.Flag):
    """What the reactor is still allowed to do with an :class:`Info`.

    A plugin listening on ``general_info`` can clear bits to suppress echoing or
    to stop the line from reaching the server at all.
    """

    SEND_TO_SERVER = enum.auto()
    """Console input may be forwarded to the server's stdin."""

    ECHO_TO_CONSOLE = enum.auto()
    """The line may be printed to the FactorioReforge console."""

    PROCESS = enum.auto()
    """Command dispatch and plugin event dispatch may proceed."""

    @classmethod
    def default(cls) -> "InfoActionFlag":
        return cls.SEND_TO_SERVER | cls.ECHO_TO_CONSOLE | cls.PROCESS

    @classmethod
    def hidden(cls) -> "InfoActionFlag":
        """Act on it, but keep it off the console."""
        return cls.SEND_TO_SERVER | cls.PROCESS

    @classmethod
    def discarded(cls) -> "InfoActionFlag":
        return cls(0)


@dataclasses.dataclass
class Info:
    source: InfoSource
    raw_content: str
    """Exactly what came off the wire, ANSI codes and all. This is what gets echoed."""

    kind: InfoKind = InfoKind.COMMAND_RESPONSE
    content: str = ""
    """The message body with every prefix stripped off."""

    tag: Optional[str] = None
    """``CHAT`` / ``JOIN`` / ``LEAVE`` / ... for :attr:`InfoKind.GAME_EVENT`."""

    player: Optional[str] = None
    """The player this line is attributed to, when one could be determined."""

    level: Optional[str] = None
    """``Info`` / ``Error`` / ``Warning`` for engine logs."""

    elapsed: Optional[float] = None
    """Server uptime in seconds, from the engine log prefix."""

    timestamp: Optional[str] = None
    """``YYYY-MM-DD HH:MM:SS`` for game events."""

    action_flag: InfoActionFlag = dataclasses.field(default_factory=InfoActionFlag.default)
    id: int = dataclasses.field(default_factory=lambda: next(_id_counter))

    @property
    def is_from_server(self) -> bool:
        return self.source == InfoSource.SERVER

    @property
    def is_user(self) -> bool:
        """True when a human said this, i.e. it can carry a ``!!`` command.

        Chat the server itself emitted is excluded: FactorioReforge's own
        :meth:`say` comes back as ``[CHAT] <server>: ...`` and treating that as
        user input would let a relay plugin talk to itself forever.
        """
        if self.source in (InfoSource.CONSOLE, InfoSource.PLUGIN):
            return True
        return self.tag == "CHAT" and self.player is not None and not self.is_echo

    @property
    def is_echo(self) -> bool:
        """Our own :meth:`say` coming back at us."""
        return self.player == "<server>"

    def cancel_send_to_server(self) -> None:
        self.action_flag &= ~InfoActionFlag.SEND_TO_SERVER

    def cancel_echo(self) -> None:
        self.action_flag &= ~InfoActionFlag.ECHO_TO_CONSOLE

    def cancel_process(self) -> None:
        self.action_flag &= ~InfoActionFlag.PROCESS

    def should(self, flag: InfoActionFlag) -> bool:
        return flag in self.action_flag
