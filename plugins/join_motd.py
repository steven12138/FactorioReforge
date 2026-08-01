"""Greet players when they join, with a message that knows about the server.

A port of MCDReforged's joinMOTD. Placeholders are filled from live RCON data,
so the greeting says something true rather than something static.

Available placeholders:
    {player} {online} {total} {uptime} {evolution} {pollution}
    {research} {snapshots} {last_snapshot} {day}
"""

from __future__ import annotations

import asyncio
import time

from factorio_reforge.core.errors import QueryError

PLUGIN_METADATA = {
    "id": "join_motd",
    "version": "1.0.0",
    "name": "Join MOTD",
    "description": "Show a message when a player joins",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

#: An empty "lines" means "use the translated default", so a zh_cn server gets
#: a Chinese greeting without anyone editing config.json. Put lines here to
#: override, and they are used verbatim in every language.
DEFAULT_CONFIG = {
    "enabled": True,
    "lines": [],
    #: Give the client a moment to finish connecting, or the message can land
    #: before the player's console is ready to show it.
    "delay_seconds": 3,
    #: Send only to the joining player rather than the whole server.
    "private": True,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, tasks=set())


async def on_unload(server):
    for task in _state.get("tasks", ()):
        task.cancel()
    _state.clear()


async def on_player_joined(server, player, info=None):
    config = _state.get("config") or {}
    if not config.get("enabled", True):
        return
    task = asyncio.create_task(_greet(server, player, config))
    _state["tasks"].add(task)
    task.add_done_callback(_state["tasks"].discard)


async def _greet(server, player, config):
    await asyncio.sleep(max(0, config.get("delay_seconds", 3)))
    try:
        values = await _placeholders(server, player)
    except QueryError as exc:
        server.logger.warning("Could not build the MOTD for %s: %s", player, exc)
        return

    templates = config.get("lines") or [
        server.tr(f"lines.{index}") for index in range(6)
    ]
    for template in templates:
        try:
            line = template.format(**values)
        except KeyError as exc:
            # A typo in the operator's config should not silence the whole MOTD.
            server.logger.warning("MOTD line has an unknown placeholder %s: %r", exc, template)
            continue
        if config.get("private", True):
            await server.tell(player, line)
        else:
            await server.game_print(line)


async def _placeholders(server, player) -> dict:
    stats = await server.get_server_stats()
    snapshots = server.saves.list()
    ticks = stats.get("ticks_played", 0)

    last_snapshot = server.tr("never")
    if snapshots:
        age_minutes = int((time.time() - snapshots[0].created_at) / 60)
        last_snapshot = (
            server.tr("ago", minutes=age_minutes) if age_minutes else server.tr("just_now")
        )

    return {
        "player": player,
        "online": stats.get("players_online", 0),
        "total": stats.get("players_total", 0),
        "uptime": _ticks_to_text(ticks),
        "day": ticks // (60 * 60 * 25) + 1,  # a Factorio day is ~25000 ticks
        "evolution": f"{(stats.get('evolution') or 0) * 100:.2f}%",
        "pollution": f"{stats.get('pollution', 0):.0f}",
        "research": stats.get("research") or server.tr("research_idle"),
        "snapshots": len(snapshots),
        "last_snapshot": last_snapshot,
    }


def _ticks_to_text(ticks: int) -> str:
    minutes = int(ticks) // 3600
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"
