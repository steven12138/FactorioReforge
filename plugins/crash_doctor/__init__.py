"""Say *why* the server died, instead of leaving you to read the log.

Factorio's failure output is precise but buried: the reason for a refused start
is one line among a hundred, and by the time the process is gone it has scrolled
past. This keeps a rolling buffer of recent output and, on an unexpected exit,
matches it against the failure signatures that actually happen -- then reports
the cause and the command that fixes it.

Signatures were taken from real 2.0.77 output, e.g. an incompatible mod:

    Error Util.cpp:81: Failed to load mod "flib":
    - Incompatible Factorio version (current: 2.0, required: 2.1)
"""

from __future__ import annotations

import collections
import re

from factorio_reforge.command.builder import Literal
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "crash_doctor",
    "version": "1.0.0",
    "name": "Crash Doctor",
    "description": "Diagnose why the server exited and say what to do about it",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "enabled": True,
    #: How many recent output lines to keep for the post-mortem.
    "buffer_lines": 200,
    #: Also push the diagnosis to Telegram, if the bridge is loaded.
    "notify_telegram": True,
}


class Diagnosis:
    """A cause, optional detail, and what to do -- all as translation keys.

    Keeping the text in the catalogue rather than in the signature table is
    what lets a Chinese operator read the diagnosis in Chinese; the table stays
    a list of patterns.
    """

    def __init__(self, key: str, values: dict | None = None, detail: str = ""):
        self.key = key
        self.values = values or {}
        self.detail = detail

    def summary(self, tr) -> str:
        return tr(f"cause.{self.key}", **self.values)

    def fix(self, tr) -> str:
        return tr(f"fix.{self.key}", **self.values)


def _is_continuation(line: str) -> bool:
    """True for the indented detail lines Factorio prints under a failure.

        Error Util.cpp:81: Failed to load mod "flib":     <- header
        • flib                                            <- continuation
            • Incompatible Factorio version (...)         <- continuation

    Engine log lines carry a three-space timestamp indent, so the threshold for
    "indented detail" is four, and bullets count regardless of depth.
    """
    stripped = line.lstrip()
    if stripped.startswith(("•", "-", "*")):
        return True
    return line.startswith("    ") and bool(stripped)

#: (priority, pattern, builder). Lower priority wins within one failure block.
#:
#: Priority matters because Factorio prints a failure as a header plus indented
#: detail lines: the header ("Failed to load mod X") names the culprit, while the
#: detail lines below it ("Dependency base >= 2.1.0 is not satisfied") describe a
#: symptom. Scanning newest-first alone picks the last detail line and reports
#: the symptom instead of the cause.
_SIGNATURES: list[tuple[int, re.Pattern, object]] = [
    (
        0,
        re.compile(r'Failed to load mod "(?P<mod>[^"]+)"'),
        lambda m, lines: Diagnosis(
            "mod_load_failed", {"mod": m.group("mod")}, _collect_mod_reasons(lines)
        ),
    ),
    (
        1,
        re.compile(r"Incompatible Factorio version \(current: (?P<have>[\d.]+), required: (?P<need>[\d.]+)\)"),
        lambda m, lines: Diagnosis(
            "version_mismatch", {"need": m.group("need"), "have": m.group("have")}
        ),
    ),
    (
        2,
        re.compile(r"Dependency (?P<dep>.+?) is not satisfied"),
        lambda m, lines: Diagnosis("missing_dependency", {"dep": m.group("dep")}),
    ),
    (
        0,
        re.compile(r"Couldn't open (?:save )?file|Error Zip\.cpp|is corrupt|Unable to read"),
        lambda m, lines: Diagnosis("corrupt_save"),
    ),
    (
        0,
        re.compile(r"Is another instance already running\?|\.lock: Resource temporarily unavailable"),
        lambda m, lines: Diagnosis("another_instance"),
    ),
    (
        0,
        re.compile(r"Address already in use|Failed to bind|bind: Address"),
        lambda m, lines: Diagnosis("port_in_use"),
    ),
    (
        0,
        re.compile(r"Map version (?P<ver>[\d.\-]+) cannot be loaded|is higher than the game version"),
        lambda m, lines: Diagnosis("save_too_new"),
    ),
    (
        3,
        re.compile(r"Error ServerMultiplayerManager\.cpp.*?: (?P<msg>.+)"),
        lambda m, lines: Diagnosis("multiplayer_failed", detail=m.group("msg")),
    ),
    (
        4,
        re.compile(r"Cannot execute command\. Error: (?P<msg>.+)"),
        lambda m, lines: Diagnosis("command_failed", detail=m.group("msg")),
    ),
    (
        0,
        re.compile(r"(?P<msg>.*(?:std::bad_alloc|out of memory|Cannot allocate).*)"),
        lambda m, lines: Diagnosis("out_of_memory", detail=m.group("msg")),
    ),
]

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(
        config=config,
        buffer=collections.deque(maxlen=max(20, config.get("buffer_lines", 200))),
        last=None,
    )
    server.register_command(
        Literal("!!why").requires(PermissionLevel.ADMIN).runs(_cmd_why)
    )
    server.register_help_message("!!why", server.tr("help"), PermissionLevel.ADMIN)


async def on_unload(server):
    _state.clear()


async def on_info(server, info):
    """Keep a rolling window of output for the post-mortem.

    Errors and warnings only would be tempting, but the reason for a refused mod
    load is spread over several plain continuation lines beneath the error, so
    the whole stream has to be kept.
    """
    if info.is_from_server and _state.get("buffer") is not None:
        _state["buffer"].append(info.content)


async def on_server_crash(server, code):
    if not (_state.get("config") or {}).get("enabled", True):
        return

    lines = list(_state.get("buffer") or [])
    diagnosis = diagnose(lines)
    _state["last"] = (code, diagnosis, lines[-15:])

    if diagnosis is None:
        server.logger.error(server.tr("unknown_exit", code=code))
        return

    tr = server.tr
    server.logger.error(tr("exited", code=code, summary=diagnosis.summary(tr)))
    if diagnosis.detail:
        server.logger.error(tr("detail", detail=diagnosis.detail))
    if diagnosis.fix(tr):
        server.logger.error(tr("fix_line", fix=diagnosis.fix(tr)))

    if (_state["config"]).get("notify_telegram", True):
        bridge = server.get_plugin_instance("telegram_bridge")
        if bridge is not None:
            text = tr("telegram", code=code, summary=diagnosis.summary(tr))
            if diagnosis.detail:
                text += f"\n\n<code>{diagnosis.detail}</code>"
            if diagnosis.fix(tr):
                text += f"\n\n<code>{diagnosis.fix(tr)}</code>"
            await bridge.broadcast(text)


def diagnose(lines: list[str]) -> Diagnosis | None:
    """Match recent output against known failure signatures.

    Two rules, both learned the hard way:

    * Newest first, so a stale error still sitting in the buffer from an earlier
      start does not shadow the failure that just happened.
    * Within one failure block, most specific first, so the header naming the
      culprit beats the indented detail lines describing its symptoms.
    """
    newest = None
    for index in range(len(lines) - 1, -1, -1):
        if any(pattern.search(lines[index]) for _, pattern, _ in _SIGNATURES):
            newest = index
            break
    if newest is None:
        return None

    # Widen backwards only across continuation lines, so the block covers the
    # header that names the culprit but stops before an unrelated older error.
    start = newest
    while start > 0 and _is_continuation(lines[start]):
        start -= 1

    best: tuple[int, re.Match, object] | None = None
    for index in range(start, newest + 1):
        for priority, pattern, build in _SIGNATURES:
            match = pattern.search(lines[index])
            if match and (best is None or priority < best[0]):
                best = (priority, match, build)
    if best is None:
        return None
    _, match, build = best
    return build(match, lines)


def _collect_mod_reasons(lines: list[str]) -> str:
    """Gather the bullet lines Factorio prints under a mod-load failure."""
    reasons = [
        line.strip(" \t•-")
        for line in lines
        if line.strip().startswith(("•", "-"))
        and any(word in line for word in ("version", "Dependency", "dependency"))
    ]
    return "; ".join(dict.fromkeys(reasons))[:300]


async def _cmd_why(source):
    last = _state.get("last")
    if last is None:
        await source.reply(source.server.tr("nothing_yet"))
        return
    code, diagnosis, tail = last
    tr = source.server.tr
    await source.reply(tr("last_exit", code=code))
    if diagnosis is None:
        await source.reply(tr("no_match"))
        for line in tail:
            await source.reply(f"    {line}")
        return
    await source.reply(tr("cause_line", summary=diagnosis.summary(tr)))
    if diagnosis.detail:
        await source.reply(tr("detail", detail=diagnosis.detail))
    if diagnosis.fix(tr):
        await source.reply(tr("fix_line", fix=diagnosis.fix(tr)))
