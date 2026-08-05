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

import json
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
    _state.update(config=config, last_here={}, server=server, seen=_load_seen(server))

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
        ("!!here", server.tr("help.here")),
        ("!!info [player]", server.tr("help.info")),
        ("!!seen <player>", server.tr("help.seen")),
        ("!!list", server.tr("help.list")),
        ("!!stats", server.tr("help.stats")),
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
        help_entries.append(
            ("!!tp <player> <target>", server.tr("help.tp", level=level.label))
        )
        server.logger.info(server.tr("tp.enabled_for", level=level.label))

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
        await source.reply(source.server.tr("here.no_position"))
        return

    server = source.server
    config = _state["config"]

    cooldown = config.get("here_cooldown_seconds", 30)
    last = _state["last_here"].get(player, 0.0)
    if cooldown and time.monotonic() - last < cooldown:
        remaining = int(cooldown - (time.monotonic() - last))
        await source.reply(source.server.tr("here.cooldown", seconds=remaining))
        return

    try:
        info = await server.get_player_info(player)
    except QueryError as exc:
        await source.reply(source.server.tr("here.read_failed", error=exc))
        return
    if not info or not info.get("position"):
        await source.reply(source.server.tr("here.no_character"))
        return

    position = info["position"]
    surface = info.get("surface") or "nauvis"
    x, y = int(position["x"]), int(position["y"])
    _state["last_here"][player] = time.monotonic()

    # The gps tag is the clickable part: players can click it to ping the spot.
    await server.game_print(server.tr("here.announce", player=player, gps=lua.gps(x, y, surface)))

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
        await server.game_print(server.tr("here.marker_failed"))


# ---------------------------------------------------------------------------
# !!seen / !!list
# ---------------------------------------------------------------------------

async def _seen_usage(source):
    await source.reply(source.server.tr("seen.usage"))


async def _seen(source, ctx):
    server = source.server
    name = ctx["player"]
    try:
        info = await server.get_player_info(name)
        stats = await server.get_server_stats()
    except QueryError as exc:
        await source.reply(source.server.tr("error.lookup_failed", error=exc))
        return

    if not info:
        await source.reply(source.server.tr("seen.never", player=name))
        return

    played = _ticks_to_text(info.get("online_time", 0))
    if info.get("connected"):
        await source.reply(
            source.server.tr("seen.online_now", player=info["name"], played=played)
        )
        return

    recorded = _state["seen"].get(info["name"])
    if recorded:
        when, ago = _when_text(recorded)
        await source.reply(source.server.tr(
            "seen.last_seen", player=info["name"], when=when, ago=ago, played=played))
        return

    # Nobody was watching when they left -- they have not been seen since this
    # record started. Game time is all there is, and it is labelled as such.
    ago_ticks = max(0, stats.get("tick", 0) - info.get("last_online", 0))
    await source.reply(source.server.tr(
        "seen.last_seen_game_time", player=info["name"],
        ago=_ticks_to_text(ago_ticks), played=played,
    ))


async def _list(source):
    try:
        players = await source.server.get_online_player_details()
    except QueryError as exc:
        await source.reply(source.server.tr("error.lookup_failed", error=exc))
        return
    if not players:
        await source.reply(source.server.tr("list.nobody"))
        return
    await source.reply(source.server.tr("list.header", count=len(players)))
    for entry in players:
        tag = source.server.tr("list.admin_tag") if entry.get("admin") else ""
        await source.reply(source.server.tr(
            "list.entry", name=entry["name"], admin=tag,
            played=_ticks_to_text(entry.get("online_time", 0)),
        ))


# ---------------------------------------------------------------------------
# !!info -- a port of MCDReforged's player_info
# ---------------------------------------------------------------------------

async def _info_self(source):
    if source.player is None:
        await source.reply(source.server.tr("info.usage"))
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
        await source.reply(source.server.tr("error.lookup_failed", error=exc))
        return

    tr = server.tr
    if not info:
        await source.reply(tr("info.never", player=name))
        return

    level = server.get_permission_level(info["name"])
    await source.reply(tr("info.header", player=info["name"]))
    await source.reply(tr("info.status", status=tr(
        "info.online" if info.get("connected") else "info.offline")))
    await source.reply(tr("info.playtime", played=_ticks_to_text(info.get("online_time", 0))))
    await source.reply(tr(
        "info.admin",
        game=tr("common.enabled" if info.get("admin") else "common.disabled"),
        level=level.label,
    ))
    await source.reply(tr("info.force", force=info.get("force", "?")))

    if info.get("connected") and info.get("position"):
        position = info["position"]
        await source.reply(tr(
            "info.at", x=int(position["x"]), y=int(position["y"]),
            surface=info.get("surface", "?"),
        ))
    else:
        recorded = _state["seen"].get(info.get("name", ""))
        if recorded:
            when, ago = _when_text(recorded)
            await source.reply(tr("info.last_seen", when=when, ago=ago))
        else:
            ago = max(0, stats.get("tick", 0) - info.get("last_online", 0))
            await source.reply(tr("info.last_seen_game_time", ago=_ticks_to_text(ago)))


# ---------------------------------------------------------------------------
# when someone was last here, in real time
# ---------------------------------------------------------------------------
#
# Factorio's own answer is ``player.last_online``, a tick. Turning that into a
# duration measures *game* time, and the two come apart badly: with auto_pause
# on, an empty server does not tick at all, so somebody who left last night
# reads as "20m ago" the moment the next player logs in. A server that spent
# the week idle reports everybody as having just left.
#
# There is no wall clock in the Lua API, so the only correct source is this
# side of the pipe: FactorioReforge sees the join and leave events as they
# happen and can note the time itself.

SEEN_FILE = "last_seen.json"


def _load_seen(server) -> dict[str, float]:
    path = server.get_data_folder() / SEEN_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        server.logger.warning("%s is unreadable; starting a new last-seen record", path)
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}


def _save_seen(server) -> None:
    path = server.get_data_folder() / SEEN_FILE
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(_state["seen"], indent=2), encoding="utf-8")
    temp.replace(path)


def _note_seen(player: str) -> None:
    """Both joining and leaving count: a player online when the server stopped
    never emits a leave, and their join is still the last time anyone saw them.
    """
    server = _state.get("server")
    if server is None or not player:
        return
    _state["seen"][player] = time.time()
    _save_seen(server)


async def on_player_joined(server, player):
    _note_seen(player)


async def on_player_left(server, player):
    _note_seen(player)


def _when_text(timestamp: float, now: float | None = None) -> tuple[str, str]:
    """``("2026-08-05 21:05", "3h20m")`` -- the date, and how long ago."""
    now = time.time() if now is None else now
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
    return when, _duration_text(max(0.0, now - timestamp))


def _duration_text(seconds: float) -> str:
    minutes = int(seconds) // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"

# ---------------------------------------------------------------------------
# !!stats
# ---------------------------------------------------------------------------

async def _stats(source):
    try:
        stats = await source.server.get_server_stats()
    except QueryError as exc:
        await source.reply(source.server.tr("error.lookup_failed", error=exc))
        return

    tr = source.server.tr
    research = stats.get("research")
    progress = stats.get("research_progress") or 0
    await source.reply(tr("stats.surface", surface=stats.get("surface", "?")))
    await source.reply(tr("stats.played", played=_ticks_to_text(stats.get("ticks_played", 0))))
    await source.reply(tr("stats.players", online=stats.get("players_online", 0),
                          total=stats.get("players_total", 0)))
    await source.reply(tr("stats.evolution", value=f"{(stats.get('evolution') or 0) * 100:.2f}%"))
    await source.reply(tr("stats.pollution", value=f"{stats.get('pollution', 0):.0f}"))
    await source.reply(
        tr("stats.research", name=research, progress=f"{progress * 100:.0f}%")
        if research else tr("stats.research_idle")
    )


# ---------------------------------------------------------------------------
# !!tp
# ---------------------------------------------------------------------------

async def _tp_usage(source):
    await source.reply(source.server.tr("tp.usage"))


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
            await source.reply(server.tr("tp.lookup_failed", target=target, error=exc))
            return
        if not info or not info.get("position"):
            await source.reply(server.tr("tp.target_offline", target=target, player=who))
            return
        position = info["position"]
        surface = info.get("surface")
        destination = f"{info['name']}"

    try:
        result = await server.teleport_player(who, position, surface)
    except QueryError as exc:
        await source.reply(server.tr("tp.failed", reason=exc))
        return

    if not result.get("ok"):
        await source.reply(server.tr(
            "tp.failed", reason=result.get("reason") or server.tr("tp.blocked")))
        return
    await source.reply(server.tr("tp.done", player=who, destination=destination))
    if _state["config"].get("teleport_announce", True):
        # Say it out loud: a silent teleport looks like a desync or a cheat to
        # anyone standing nearby.
        await server.game_print(server.tr(
            "tp.announced", player=who, destination=destination, by=source))


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
