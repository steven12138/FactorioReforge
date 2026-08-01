"""Rankings: who has played most, and what the factory has killed and built.

Factorio tracks playtime per player natively (``online_time``), so that board is
exact. Things it does *not* track per player -- items crafted, distance walked --
are deliberately absent rather than guessed at: a made-up number on a
leaderboard is worse than no leaderboard.
"""

from __future__ import annotations

from factorio_reforge.command.builder import Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "leaderboard",
    "version": "1.0.0",
    "name": "Leaderboard",
    "description": "Playtime rankings and factory-wide totals",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "top_n": 10,
    "surface": "nauvis",
}

_state: dict = {}
TICKS_PER_MINUTE = 3600


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config)

    server.register_command(
        Literal("!!top")
        .requires(PermissionLevel.USER)
        .runs(_cmd_playtime)
        .then(Literal("time").runs(_cmd_playtime))
        .then(Literal("kills").runs(_cmd_kills))
        .then(Literal("built").runs(_cmd_built))
    )
    server.register_help_message("!!top [time|kills|built]", "rankings", PermissionLevel.USER)


async def on_unload(server):
    _state.clear()


async def _cmd_playtime(source):
    try:
        players = await source.server.get_all_players()
    except QueryError as exc:
        await source.reply(f"Could not read the player list: {exc}")
        return
    if not players:
        await source.reply("Nobody has joined this server yet.")
        return

    ranked = sorted(players, key=lambda p: p.get("online_time", 0), reverse=True)
    top = ranked[: _state["config"].get("top_n", 10)]
    await source.reply("Most time played:")
    for index, player in enumerate(top, start=1):
        marker = " (online)" if player.get("connected") else ""
        await source.reply(
            f"  {index}. {player['name']} - {_ticks_to_text(player.get('online_time', 0))}{marker}"
        )


async def _cmd_kills(source):
    """Factory-wide, not per player: Factorio attributes kills to the force."""
    try:
        rows = await source.server.lua_json(
            lua.kill_counts(_state["config"].get("surface", "nauvis"), limit=12)
        )
    except QueryError as exc:
        await source.reply(f"Could not read the kill counts: {exc}")
        return
    if not rows:
        await source.reply("Nothing has been killed yet.")
        return
    await source.reply("Most-killed enemies (whole force):")
    for index, row in enumerate(rows, start=1):
        await source.reply(f"  {index}. {row['name']}: {row['kills']:,}")


async def _cmd_built(source):
    try:
        rows = await source.server.lua_json(
            lua.production_totals(_state["config"].get("surface", "nauvis"), limit=12)
        )
    except QueryError as exc:
        await source.reply(f"Could not read the totals: {exc}")
        return
    if not rows:
        await source.reply("Nothing has been produced yet.")
        return
    await source.reply("Most-produced items:")
    for index, row in enumerate(rows, start=1):
        await source.reply(f"  {index}. {row['name']}: {row['produced']:,}")


def _ticks_to_text(ticks: int) -> str:
    minutes = int(ticks) // TICKS_PER_MINUTE
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"
