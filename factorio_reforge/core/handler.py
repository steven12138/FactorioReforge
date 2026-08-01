"""Turn raw server output lines into :class:`Info` objects.

The regexes here were written against output sampled from a live 2.0.77 headless
server; see ``docs/M0-findings.md`` for the samples and for why there are four
shapes instead of the two the design originally assumed.
"""

from __future__ import annotations

import re
import time

from factorio_reforge.core.info import Info, InfoKind, InfoSource

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# 2026-08-02 02:16:35 [CHAT] Alice: hello
GAME_EVENT = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(?P<tag>[A-Z][A-Z-]*)\] (?P<content>.*)"
)

# The elapsed-seconds prefix is always present on engine output. The
# "Info Foo.cpp:808:" part is not -- plenty of lines (Hosting game at ...,
# Received SIGINT, Loading map ...) carry only the timestamp, which is exactly
# what broke the original single-regex plan.
ENGINE_LOG = re.compile(
    r"\s*(?P<elapsed>\d+\.\d+) "
    r"(?:(?P<level>[A-Z][a-z]+) (?P<src>[\w./-]+\.\w+:\d+): )?"
    r"(?P<content>.*)"
)

# "[CHAT] Alice: hello" -> player Alice. Also matches "<server>", which is how
# our own say() comes back and how Info.is_echo detects it.
_CHAT_BODY = re.compile(r"(?P<player>[^:]+): (?P<content>.*)", re.DOTALL)

# "[JOIN] Alice joined the game" / "[LEAVE] Alice left the game"
_JOIN_BODY = re.compile(r"(?P<player>\S+) joined the game")
_LEAVE_BODY = re.compile(r"(?P<player>\S+) left the game")

STARTUP_MARKER = "changing state from(CreatingGame) to(InGame)"
RCON_READY_MARKER = "Starting RCON interface"
GOODBYE_MARKER = "Goodbye"

#: A ``/server-save`` finishes with AppManager's "Saving finished"; the save
#: taken while shutting down goes through MainLoop instead and only ever reports
#: progress. Both have to count, or snapshotting waits out its whole timeout.
SAVE_DONE_MARKERS = ("Saving finished", "Saving progress: 100.000000%")

KNOWN_TAGS = frozenset(
    {"CHAT", "JOIN", "LEAVE", "KICK", "BAN", "UNBANNED", "DEATH", "PROMOTE", "DEMOTE",
     "MUTE", "UNMUTE", "COMMAND", "SHOUT", "WHISPER", "WARNING", "ADMIN", "COLOR"}
)


class FactorioHandler:
    """Stateless line parser. Instantiated once and reused.

    Kept free of I/O on purpose so the parsing rules can be unit-tested against
    the recorded samples without spawning a server.
    """

    def __init__(self) -> None:
        self._unknown_tags_seen: set[str] = set()

    @staticmethod
    def pre_parse(text: str) -> str:
        return _ANSI.sub("", text).rstrip("\r\n")

    def parse_console_input(self, text: str) -> Info:
        return Info(
            source=InfoSource.CONSOLE,
            raw_content=text,
            kind=InfoKind.CONSOLE_INPUT,
            content=text.strip(),
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def parse_server_stdout(self, text: str) -> Info:
        """Try each shape in turn; an unmatched line is command output, not an error.

        Order matters: game events are checked first because their date prefix is
        unambiguous, then engine logs, and whatever is left is a bare command
        response with no markers to key off of.
        """
        clean = self.pre_parse(text)
        info = Info(source=InfoSource.SERVER, raw_content=text.rstrip("\r\n"), content=clean)

        if (m := GAME_EVENT.fullmatch(clean)) is not None:
            self._fill_game_event(info, m)
            return info

        if (m := ENGINE_LOG.fullmatch(clean)) is not None:
            info.elapsed = float(m.group("elapsed"))
            info.level = m.group("level")
            info.content = m.group("content")
            info.kind = InfoKind.ENGINE_LOG if m.group("level") else InfoKind.ENGINE_PLAIN
            return info

        info.kind = InfoKind.COMMAND_RESPONSE
        return info

    def _fill_game_event(self, info: Info, m: re.Match) -> None:
        info.kind = InfoKind.GAME_EVENT
        info.timestamp = m.group("ts")
        info.tag = m.group("tag")
        body = m.group("content")
        info.content = body

        if info.tag == "CHAT":
            if (cm := _CHAT_BODY.fullmatch(body)) is not None:
                info.player = cm.group("player")
                info.content = cm.group("content")
        elif info.tag == "JOIN":
            if (jm := _JOIN_BODY.fullmatch(body)) is not None:
                info.player = jm.group("player")
        elif info.tag == "LEAVE":
            if (lm := _LEAVE_BODY.fullmatch(body)) is not None:
                info.player = lm.group("player")
        elif info.tag not in KNOWN_TAGS and info.tag not in self._unknown_tags_seen:
            # Warn once per tag rather than per line: a new Factorio release
            # adding a tag should be visible but must not flood the log.
            self._unknown_tags_seen.add(info.tag)

    def take_new_unknown_tags(self) -> set[str]:
        """Unknown tags seen since the last call, for the caller to log."""
        tags, self._unknown_tags_seen = self._unknown_tags_seen, set()
        return tags

    @staticmethod
    def is_startup_done(info: Info) -> bool:
        return STARTUP_MARKER in info.content

    @staticmethod
    def is_rcon_ready(info: Info) -> bool:
        return RCON_READY_MARKER in info.content

    @staticmethod
    def is_save_done(info: Info) -> bool:
        return any(marker in info.content for marker in SAVE_DONE_MARKERS)

    @staticmethod
    def format_say(text: str) -> str:
        return text

    @staticmethod
    def format_tell(player: str, text: str) -> str | None:
        """There is no ``/tell`` in Factorio; whispering needs RCON + Lua.

        Returned as ``None`` so :class:`ServerInterface` knows to route through
        RCON instead of silently broadcasting a private message to everyone.
        """
        return None
