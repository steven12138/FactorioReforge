"""In-game utility commands, ported from the MCDReforged plugins people miss most.

    !!here            share where you are -- and drop a clickable map marker
    !!seen <player>   playtime and when they were last online
    !!list            who is online, with playtime
    !!stats           world state: evolution, pollution, research, uptime
    !!tp <a> <b>      teleport a to b (admin)

``!!here`` is the interesting one. MCDReforged makes the shared coordinate a
clickable link in Minecraft chat; Factorio has the same thing in the form of a
``[gps=x,y,surface]`` rich-text tag, which renders as a clickable coordinate that
pings the position on everyone's map. ``!!here`` sends that *and* drops a chart
tag, so the location is both immediately pingable and permanently on the map.
"""

from __future__ import annotations

import time

from factorio_reforge.command.builder import GreedyText, Literal, Text
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "server_utils",
    "version": "1.0.0",
    "name": "Server Utils",
    "description": "!!here, !!seen, !!list, !!stats, !!tp",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "here_marker": True,
    "here_marker_icon": {"type": "virtual", "name": "signal-info"},
    #: Ignore repeated !!here from the same player within this many seconds, so
    #: the shared map does not fill up with markers.
    "here_cooldown_seconds": 30,
    #: Teleporting skips the walking, the trains and the danger a Factorio run
    #: is built around, so it is off unless an operator decides their server
    #: wants it. Turning it on registers !!tp; leaving it off does not.
    "enable_teleport": False,
    #: With teleport on, who may use it. "admin" keeps it to staff; "user"
    #: opens it to everyone, which is a different game.
    "teleport_permission": "admin",
    #: Announce teleports in chat so they are not invisible to other players.
    "teleport_announce": True,
}

_state: dict = {}
TICKS_PER_MINUTE = 3600


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, last_here={})

    server.register_command(Literal("!!here").requires(PermissionLevel.USER).runs(_here))
    server.register_command(
        Literal("!!seen").requires(PermissionLevel.USER)
        .runs(_seen_usage)
        .then(Text("player").runs(_seen))
    )
    server.register_command(Literal("!!list").requires(PermissionLevel.USER).runs(_list))
    server.register_command(Literal("!!stats").requires(PermissionLevel.USER).runs(_stats))
    server.register_command(
        Literal("!!info").requires(PermissionLevel.USER)
        .runs(_info_self)
        .then(Text("player").runs(_info))
    )

    help_entries = [
        ("!!here", "share your position and pin it on the map"),
        ("!!info [player]", "detailed player info"),
        ("!!seen <player>", "playtime and last seen"),
        ("!!list", "who is online"),
        ("!!stats", "world statistics"),
    ]

    # !!tp exists only when the operator opts in -- an unregistered command
    # cannot be discovered or accidentally granted, which is a stronger guard
    # than registering it and refusing at call time.
    if config.get("enable_teleport", False):
        level = _parse_level(config.get("teleport_permission", "admin"), server)
        server.register_command(
            Literal("!!tp").requires(level)
            .runs(_tp_usage)
            .then(Text("who").then(GreedyText("target").runs(_tp)))
        )
        help_entries.append(("!!tp <player> <target>", f"teleport ({level.label}+)"))
        server.logger.info("Teleport is enabled for %s and above", level.label)

    for prefix, message in help_entries:
        server.register_help_message(prefix, message)


def _parse_level(value, server) -> PermissionLevel:
    try:
        return PermissionLevel.parse(value)
    except ValueError:
        server.logger.warning(
            "teleport_permission %r is not a level; falling back to admin", value
        )
        return PermissionLevel.ADMIN


# ---------------------------------------------------------------------------
# !!here
# ---------------------------------------------------------------------------

async def _here(source):
    player = source.player
    if player is None:
        await source.reply("!!here only makes sense from in game -- the console has no position.")
        return

    server = source.server
    config = _state["config"]

    cooldown = config.get("here_cooldown_seconds", 30)
    last = _state["last_here"].get(player, 0.0)
    if cooldown and time.monotonic() - last < cooldown:
        remaining = int(cooldown - (time.monotonic() - last))
        await source.reply(f"Slow down -- try !!here again in {remaining}s.")
        return

    try:
        info = await server.get_player_info(player)
    except QueryError as exc:
        await source.reply(f"Could not read your position: {exc}")
        return
    if not info or not info.get("position"):
        await source.reply("Could not read your position -- are you controlling a character?")
        return

    position = info["position"]
    surface = info.get("surface") or "nauvis"
    x, y = int(position["x"]), int(position["y"])
    _state["last_here"][player] = time.monotonic()

    # The gps tag is the clickable part: players can click it to ping the spot.
    await server.game_print(f"{player} is at {lua.gps(x, y, surface)}")

    if not config.get("here_marker", True):
        return
    try:
        marker = await server.add_map_marker(
            {"x": x, "y": y}, player, surface=surface,
            icon=config.get("here_marker_icon"),
        )
    except QueryError as exc:
        server.logger.warning("Could not place the !!here marker: %s", exc)
        return
    if marker is None:
        # Factorio can reject a position outright; say so rather than leaving
        # the player believing a marker was pinned.
        await server.game_print("(the map marker could not be placed there)")


# ---------------------------------------------------------------------------
# !!seen / !!list
# ---------------------------------------------------------------------------

async def _seen_usage(source):
    await source.reply("Usage: !!seen <player>")


async def _seen(source, ctx):
    server = source.server
    name = ctx["player"]
    try:
        info = await server.get_player_info(name)
        stats = await server.get_server_stats()
    except QueryError as exc:
        await source.reply(f"Could not look that up: {exc}")
        return

    if not info:
        await source.reply(f"{name} has never joined this server.")
        return

    played = _ticks_to_text(info.get("online_time", 0))
    if info.get("connected"):
        await source.reply(f"{info['name']} is online now, {played} played.")
        return

    ago_ticks = max(0, stats.get("tick", 0) - info.get("last_online", 0))
    await source.reply(
        f"{info['name']} was last seen {_ticks_to_text(ago_ticks)} ago (game time), "
        f"{played} played."
    )


async def _list(source):
    try:
        players = await source.server.get_online_player_details()
    except QueryError as exc:
        await source.reply(f"Could not read the player list: {exc}")
        return
    if not players:
        await source.reply("Nobody is online.")
        return
    await source.reply(f"{len(players)} online:")
    for entry in players:
        tag = " [admin]" if entry.get("admin") else ""
        await source.reply(
            f"  {entry['name']}{tag} - {_ticks_to_text(entry.get('online_time', 0))} played"
        )


# ---------------------------------------------------------------------------
# !!info -- a port of MCDReforged's player_info
# ---------------------------------------------------------------------------

async def _info_self(source):
    if source.player is None:
        await source.reply("Usage: !!info <player>")
        return
    await _report_player(source, source.player)


async def _info(source, ctx):
    await _report_player(source, ctx["player"])


async def _report_player(source, name):
    server = source.server
    try:
        info = await server.get_player_info(name)
        stats = await server.get_server_stats()
    except QueryError as exc:
        await source.reply(f"Could not look that up: {exc}")
        return

    if not info:
        await source.reply(f"{name} has never joined this server.")
        return

    level = server.get_permission_level(info["name"])
    await source.reply(f"=== {info['name']} ===")
    await source.reply(f"  Status: {'online' if info.get('connected') else 'offline'}")
    await source.reply(f"  Playtime: {_ticks_to_text(info.get('online_time', 0))}")
    await source.reply(
        f"  Game admin: {'yes' if info.get('admin') else 'no'}"
        f" | FactorioReforge: {level.label}"
    )
    await source.reply(f"  Force: {info.get('force', '?')}")

    if info.get("connected") and info.get("position"):
        position = info["position"]
        await source.reply(
            f"  At: ({int(position['x'])}, {int(position['y'])}) on {info.get('surface', '?')}"
        )
    else:
        ago = max(0, stats.get("tick", 0) - info.get("last_online", 0))
        await source.reply(f"  Last seen: {_ticks_to_text(ago)} ago (game time)")


# ---------------------------------------------------------------------------
# !!stats
# ---------------------------------------------------------------------------

async def _stats(source):
    try:
        stats = await source.server.get_server_stats()
    except QueryError as exc:
        await source.reply(f"Could not read the world stats: {exc}")
        return

    research = stats.get("research")
    progress = stats.get("research_progress") or 0
    await source.reply(f"Surface: {stats.get('surface', '?')}")
    await source.reply(f"Played: {_ticks_to_text(stats.get('ticks_played', 0))}")
    await source.reply(f"Players: {stats.get('players_online', 0)} online / {stats.get('players_total', 0)} total")
    await source.reply(f"Evolution: {(stats.get('evolution') or 0) * 100:.2f}%")
    await source.reply(f"Pollution: {stats.get('pollution', 0):.0f}")
    await source.reply(
        f"Research: {research} ({progress * 100:.0f}%)" if research else "Research: idle"
    )


# ---------------------------------------------------------------------------
# !!tp
# ---------------------------------------------------------------------------

async def _tp_usage(source):
    await source.reply("Usage: !!tp <player> <target player | x y>")


async def _tp(source, ctx):
    server = source.server
    who = ctx["who"]
    target = ctx["target"].strip()

    parts = target.replace(",", " ").split()
    if len(parts) == 2 and _is_number(parts[0]) and _is_number(parts[1]):
        position = {"x": float(parts[0]), "y": float(parts[1])}
        surface = None
        destination = f"({parts[0]}, {parts[1]})"
    else:
        try:
            info = await server.get_player_info(target)
        except QueryError as exc:
            await source.reply(f"Could not look up {target}: {exc}")
            return
        if not info or not info.get("position"):
            await source.reply(f"{target} is not online, so there is nowhere to send {who}.")
            return
        position = info["position"]
        surface = info.get("surface")
        destination = f"{info['name']}"

    try:
        result = await server.teleport_player(who, position, surface)
    except QueryError as exc:
        await source.reply(f"Teleport failed: {exc}")
        return

    if not result.get("ok"):
        await source.reply(f"Teleport failed: {result.get('reason', 'the destination is blocked')}")
        return
    await source.reply(f"Teleported {who} to {destination}.")
    if _state["config"].get("teleport_announce", True):
        # Say it out loud: a silent teleport looks like a desync or a cheat to
        # anyone standing nearby.
        await server.game_print(
            f"[FactorioReforge] {who} was teleported to {destination} by {source}"
        )


# ---------------------------------------------------------------------------

def _ticks_to_text(ticks: int) -> str:
    """Factorio counts at 60 ticks per second; render that as human time."""
    minutes = int(ticks) // TICKS_PER_MINUTE
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False
