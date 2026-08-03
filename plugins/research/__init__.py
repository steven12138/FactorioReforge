"""Queue research from chat, so nobody has to be at a keyboard in the base.

The lab never stops mattering and the queue is trivially editable through the
API -- ``force.add_research`` is a function on 2.0.77, and ``research_queue``
reads back as a list of technologies. What the game does not give you is a way
to say "queue logistics 3" while standing in a mine, or from a phone.

``research_queue_enabled`` does not exist on 2.0.77 (it was a 1.1 property), so
queueing is simply attempted and the result reported -- which is the honest
version anyway: a technology whose prerequisites are missing is refused by the
game, and repeating the game's refusal beats predicting it wrong.
"""

from __future__ import annotations

from factorio_reforge.command.builder import GreedyText, Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "research",
    "version": "1.0.0",
    "name": "Research Queue",
    "description": "See and change what the labs are working on",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    #: Who may change what is being researched. Reading is always open.
    "manage_permission": "helper",
    #: Announce in chat when the queue is changed from outside the game.
    "announce_changes": True,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config)

    manage = _parse_level(config.get("manage_permission", "helper"), server)
    server.register_command(
        Literal("!!research")
        .requires(PermissionLevel.USER)
        .runs(_cmd_show)
        .then(Literal("queue").runs(_cmd_show))
        .then(Literal("add").requires(manage).then(GreedyText("name").runs(_cmd_add)))
        .then(Literal("cancel").requires(manage).runs(_cmd_cancel))
        .then(Literal("search").then(GreedyText("term").runs(_cmd_search)))
    )
    server.register_help_message(
        "!!research", server.tr("help"), PermissionLevel.USER,
        detail=(server.tr("detail.add"), server.tr("detail.search")),
    )


async def on_unload(server):
    _state.clear()


def _parse_level(value, server) -> PermissionLevel:
    try:
        return PermissionLevel.parse(value)
    except ValueError:
        server.logger.warning("manage_permission %r is not a level; using helper", value)
        return PermissionLevel.HELPER


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def normalise(name: str) -> str:
    """``Logistics 3`` and ``logistics-3`` are the same technology."""
    return "-".join(name.strip().lower().split())


def progress_bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(fraction * width))))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def format_eta(remaining_units: float, units_per_second: float) -> str | None:
    """Seconds until done, from science consumed per second.

    Returns None when nothing is being consumed -- an infinite ETA is worse
    than no ETA, because it looks like a number.
    """
    if units_per_second <= 0 or remaining_units <= 0:
        return None
    seconds = int(remaining_units / units_per_second)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def status_query() -> str:
    return (
        "(function() "
        "local function safe(f) local ok, v = pcall(f) if ok then return v end end "
        "local f = game.forces.player "
        "local current = f.current_research "
        "local queue = {} "
        "for _, t in pairs(safe(function() return f.research_queue end) or {}) do "
        "  queue[#queue + 1] = t.name end "
        "local units, per_unit = nil, nil "
        "if current then "
        "  units = current.research_unit_count "
        "  per_unit = current.research_unit_energy "
        "end "
        "return {current = current and current.name or nil, "
        "        level = current and current.level or nil, "
        "        progress = f.research_progress, "
        "        units = units, unit_energy = per_unit, queue = queue} end)()"
    )


def add_query(name: str) -> str:
    """Ask the game to queue it, and report what the game said.

    ``add_research`` returns false for a technology that is already researched
    or whose prerequisites are missing, which is exactly the answer to relay.
    """
    return (
        "(function() local f = game.forces.player "
        "local t = f.technologies[%s] "
        "if not t then return {ok = false, reason = 'no such technology'} end "
        "if t.researched then return {ok = false, reason = 'already researched'} end "
        "local ok = f.add_research(t) "
        "return {ok = ok, name = t.name, "
        "        reason = ok and '' or 'the game refused it (prerequisites?)'} end)()"
    ) % lua.lua_string(name)


def cancel_query() -> str:
    return (
        "(function() local f = game.forces.player "
        "local was = f.current_research and f.current_research.name or nil "
        "f.cancel_current_research() "
        "return {cancelled = was} end)()"
    )


def search_query(term: str, limit: int = 8) -> str:
    return (
        "(function() local out = {} "
        f"local term = {lua.lua_string(term)} "
        "for name, t in pairs(game.forces.player.technologies) do "
        f"  if #out < {int(limit)} and string.find(name, term, 1, true) then "
        "    out[#out + 1] = {name = name, researched = t.researched, enabled = t.enabled} end "
        "end return out end)()"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def _cmd_show(source):
    server = source.server
    try:
        data = await server.lua_json(status_query()) or {}
    except QueryError as exc:
        await source.reply(server.tr("error.failed", error=exc))
        return

    current = data.get("current")
    if not current:
        await source.reply(server.tr("show.idle"))
    else:
        fraction = float(data.get("progress") or 0)
        line = server.tr(
            "show.current", name=current, bar=progress_bar(fraction),
            percent=f"{fraction * 100:.0f}",
        )
        units = float(data.get("units") or 0)
        remaining = units * (1 - fraction)
        if remaining > 0:
            line += server.tr("show.remaining", units=f"{remaining:,.0f}")
        await source.reply(line)

    queue = [name for name in (data.get("queue") or []) if name != current]
    if queue:
        await source.reply(server.tr("show.queue", count=len(queue)))
        for name in queue[:8]:
            await source.reply(f"  {name}")
    elif current:
        await source.reply(server.tr("show.queue_empty"))


async def _cmd_add(source, ctx):
    server = source.server
    name = normalise(ctx["name"])
    try:
        result = await server.lua_json(add_query(name)) or {}
    except QueryError as exc:
        await source.reply(server.tr("error.failed", error=exc))
        return

    if not result.get("ok"):
        reason = result.get("reason") or ""
        key = {
            "no such technology": "error.no_such",
            "already researched": "error.already",
        }.get(reason, "error.refused")
        await source.reply(server.tr(key, name=name, reason=reason))
        return

    message = server.tr("add.done", name=result.get("name", name),
                        who=source.player or server.tr("add.console"))
    await source.reply(message)
    if _state["config"].get("announce_changes", True) and source.player is None:
        # Announce only what happened from outside the game: a player who typed
        # it in chat has already told everyone.
        try:
            await server.game_print(message)
        except QueryError:
            pass


async def _cmd_cancel(source):
    server = source.server
    try:
        result = await server.lua_json(cancel_query()) or {}
    except QueryError as exc:
        await source.reply(server.tr("error.failed", error=exc))
        return
    was = result.get("cancelled")
    await source.reply(
        server.tr("cancel.done", name=was) if was else server.tr("cancel.nothing")
    )


async def _cmd_search(source, ctx):
    server = source.server
    term = normalise(ctx["term"])
    try:
        found = await server.lua_json(search_query(term)) or []
    except QueryError as exc:
        await source.reply(server.tr("error.failed", error=exc))
        return
    if not found:
        await source.reply(server.tr("search.nothing", term=term))
        return
    await source.reply(server.tr("search.header", count=len(found)))
    for entry in found:
        state = server.tr(
            "search.researched" if entry.get("researched")
            else "search.available" if entry.get("enabled")
            else "search.locked"
        )
        await source.reply(f"  {entry.get('name')} {state}")
