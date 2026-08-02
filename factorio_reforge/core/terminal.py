"""Colour and the startup banner.

Colour is applied only when the output is a terminal that wants it. Three things
turn it off, and all three matter:

* **Not a TTY.** Piping into ``grep`` or a log collector must produce clean
  text; escape sequences in a pipeline are noise at best and corrupt a parsed
  log at worst.
* **``NO_COLOR``** is set, per https://no-color.org.
* **``TERM=dumb``**, which is what editors and CI shells report.

The file log handler never gets colour regardless, so ``logs/reforge.log``
stays greppable.
"""

from __future__ import annotations

import logging
import os
import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

#: Factorio's palette, in 256-colour form so it survives a plain xterm.
ORANGE = "\033[38;5;214m"
AMBER = "\033[38;5;179m"
GREY = "\033[38;5;245m"
WHITE = "\033[38;5;255m"
RED = "\033[38;5;203m"
YELLOW = "\033[38;5;221m"
GREEN = "\033[38;5;114m"
CYAN = "\033[38;5;80m"
BLUE = "\033[38;5;111m"
MAGENTA = "\033[38;5;176m"


def supports_colour(stream=None) -> bool:
    """Whether to emit escape sequences on ``stream``."""
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class Palette:
    """Colouring that becomes a no-op when colour is off.

    Callers never branch on whether colour is enabled -- they always wrap, and
    a disabled palette returns the text unchanged.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + RESET

    def orange(self, text: str) -> str:
        return self(text, ORANGE)

    def amber(self, text: str) -> str:
        return self(text, AMBER)

    def grey(self, text: str) -> str:
        return self(text, GREY)

    def white(self, text: str) -> str:
        return self(text, WHITE)

    def red(self, text: str) -> str:
        return self(text, RED)

    def yellow(self, text: str) -> str:
        return self(text, YELLOW)

    def green(self, text: str) -> str:
        return self(text, GREEN)

    def cyan(self, text: str) -> str:
        return self(text, CYAN)

    def blue(self, text: str) -> str:
        return self(text, BLUE)

    def magenta(self, text: str) -> str:
        return self(text, MAGENTA)

    def bold(self, text: str) -> str:
        return self(text, BOLD)

    def dim(self, text: str) -> str:
        return self(text, DIM)


#: "FactorioReforge" in the Calvin S figlet face -- three rows, 45 columns, so
#: it fits an 80-column terminal with room to spare. Split into the two words so
#: "Reforge" can carry the accent colour, matching docs/banner.svg.
_WORDMARK = (
    ("╔═╗┌─┐┌─┐┌┬┐┌─┐┬─┐┬┌─┐", "╦═╗┌─┐┌─┐┌─┐┬─┐┌─┐┌─┐"),
    ("╠╣ ├─┤│   │ │ │├┬┘││ │", "╠╦╝├┤ ├┤ │ │├┬┘│ ┬├┤ "),
    ("╚  ┴ ┴└─┘ ┴ └─┘┴└─┴└─┘", "╩╚═└─┘└  └─┘┴└─└─┘└─┘"),
)

#: A transport belt under the wordmark: the stdout stream this is all built on.
_BELT = "›" * 45


def banner(version: str, hint: str, palette: Palette) -> str:
    """The startup banner, coloured if the terminal allows it."""
    lines = ["", *(
        "  " + palette.white(first) + " " + palette.orange(second)
        for first, second in _WORDMARK
    )]
    lines.append("  " + palette(_BELT, DIM, ORANGE))
    lines.append(
        "  " + palette.grey(f"v{version}") + "  " + palette.grey(hint)
    )
    lines.append("")
    return "\n".join(lines)



class ColourFormatter(logging.Formatter):
    """Log formatter that tints the level and dims the scaffolding.

    Only the console handler uses it. The file handler keeps the plain
    formatter, so ``logs/reforge.log`` stays greppable and safe to paste.
    """

    LEVEL_COLOURS = {
        logging.DEBUG: GREY,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED + BOLD,
    }
    LEVEL_LABELS = {
        logging.DEBUG: "DBG",
        logging.INFO: "INF",
        logging.WARNING: "WRN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "CRT",
    }

    def __init__(self, palette: Palette):
        super().__init__(datefmt="%H:%M:%S")
        self.palette = palette

    def format(self, record: logging.LogRecord) -> str:
        colour = self.LEVEL_COLOURS.get(record.levelno, "")
        label = self.LEVEL_LABELS.get(record.levelno, record.levelname[:3])

        # Plugins log under "plugin.<id>"; showing just the id keeps the prefix
        # short enough that the message still starts in the same column.
        name = record.name.split(".", 1)[-1] if record.name != "reforge" else "reforge"
        name_colour = ORANGE if record.name == "factorio" else ""

        prefix = (
            self.palette.dim(self.formatTime(record, self.datefmt))
            + " "
            + self.palette(label, colour, BOLD)
            + " "
            + self.palette(f"{name:<14.14}", DIM, *([name_colour] if name_colour else []))
            + " "
        )
        message = record.getMessage()
        info = getattr(record, "server_info", None)
        if info is not None:
            # Factorio's own words: tint by what the line is, not by level, so
            # chat, joins and deaths stand out from the engine's chatter.
            message = colour_server_line(message, info, self.palette)
        elif record.levelno >= logging.WARNING:
            message = self.palette(message, colour)

        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        return prefix + message


#: How each kind of server output is tinted when echoed to the console.
#: The point is that a person can tell at a glance whether a line came from
#: Factorio's engine, from a player, or from a command they just typed.
def colour_server_line(raw: str, info, palette: Palette) -> str:
    """Tint one line of Factorio output by what it is."""
    from factorio_reforge.core.info import InfoKind

    if info.kind is InfoKind.GAME_EVENT:
        tag = info.tag or ""
        if tag == "CHAT":
            return palette(raw, CYAN)
        if tag in ("JOIN", "LEAVE"):
            return palette(raw, GREEN)
        if tag in ("DEATH", "KICK", "BAN"):
            return palette(raw, RED)
        return palette(raw, MAGENTA)

    if info.level == "Error":
        return palette(raw, RED)
    if info.level == "Warning":
        return palette(raw, YELLOW)
    if info.kind is InfoKind.COMMAND_RESPONSE:
        # A bare reply to something the operator typed: keep it bright, it is
        # the answer they are waiting for.
        return palette(raw, AMBER)
    # Ordinary engine chatter, of which there is a great deal.
    return palette.dim(raw)
