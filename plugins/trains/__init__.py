"""Find the train that is holding up the railway.

On any base with more than a handful of trains, "something is not arriving" is a
weekly question, and the answer is nearly always one of two things: a train with
no path, or a train that has been sitting at a station long enough that it is
not coming back. Both are visible from ``train.state``; neither is visible from
inside the game without walking the network.

``force.get_trains()`` is gone in 2.0 -- measured, it raises *"LuaForce doesn't
contain key get_trains"*. The replacement is ``game.train_manager.get_trains{}``.
"""

from __future__ import annotations

import asyncio
import time

from factorio_reforge.command.builder import Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "trains",
    "version": "1.0.0",
    "name": "Train Monitor",
    "description": "No-path and stuck trains, and what the network is doing",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

#: ``defines.train_state`` as numbers, because that is what comes back as JSON.
#: Names from the runtime docs; ``no_path`` is 2, measured on 2.0.77.
STATE_NAMES = {
    0: "on_the_path",
    1: "path_lost",
    2: "no_path",
    3: "arrive_signal",
    4: "wait_signal",
    5: "arrive_station",
    6: "wait_station",
    7: "manual_control_stop",
    8: "manual_control",
    9: "destination_full",
}

#: States that are wrong the moment they happen, rather than after a while.
BROKEN_STATES = {1, 2}

#: States that are fine briefly and a problem if they persist.
STALLED_STATES = {4, 6, 8, 9}

DEFAULT_CONFIG = {
    "enabled": True,
    "poll_interval_seconds": 120,
    #: How long a train may sit in a waiting state before it is reported.
    "stuck_after_minutes": 15,
    "announce_in_game": True,
    #: Announce at most this many trains per poll, so one broken junction
    #: does not produce forty lines of chat.
    "max_reported": 5,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, since={}, reported=set(), task=None)

    server.register_command(
        Literal("!!trains")
        .requires(PermissionLevel.USER)
        .runs(_cmd_report)
        .then(Literal("stuck").runs(_cmd_stuck))
    )
    server.register_help_message("!!trains", server.tr("help"), PermissionLevel.USER)

    if config.get("enabled", True):
        _state["task"] = asyncio.create_task(_poller(server, config))


async def on_unload(server):
    task = _state.get("task")
    if task is not None:
        task.cancel()
    _state.clear()


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def classify(
    trains: list[dict], since: dict[int, float], now: float, stuck_after: float
) -> tuple[list[dict], list[dict]]:
    """Split into ``(broken, stalled)`` and update how long each has been so.

    ``since`` maps train id to when it entered its current state; it is mutated
    here so the caller keeps one clock across polls. A train that changed state
    is forgiven, which is the difference between "waiting at a signal" and
    "has been waiting at that signal since Tuesday".
    """
    broken, stalled = [], []
    live = set()

    for train in trains:
        train_id = train.get("id")
        state = train.get("state")
        live.add(train_id)

        key = (train_id, state)
        entered = since.get(train_id)
        if not isinstance(entered, tuple) or entered[0] != key:
            entered = (key, now)
            since[train_id] = entered
        waited = now - entered[1]
        train = {**train, "waited": waited, "state_name": STATE_NAMES.get(state, str(state))}

        if state in BROKEN_STATES:
            broken.append(train)
        elif state in STALLED_STATES and waited >= stuck_after:
            stalled.append(train)

    for train_id in [t for t in since if t not in live]:
        del since[train_id]
    return broken, stalled


def describe(train: dict) -> str:
    """``#3 (iron) no_path`` -- the id is what you type into the train GUI."""
    name = train.get("station") or train.get("name") or "?"
    return f"#{train.get('id')} {name} {train.get('state_name', '?')}"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def trains_query(surface: str = "nauvis") -> str:
    """Every train with the three things that matter: id, state, where it is.

    ``get_trains`` is on ``game.train_manager`` in 2.0; the 1.1 spelling on the
    force does not exist any more, so there is no fallback worth writing.
    """
    return (
        "(function() "
        "local function safe(f) local ok, v = pcall(f) if ok then return v end end "
        "local out = {} "
        "for _, t in pairs(game.train_manager.get_trains{surface = %s}) do "
        "  if t.valid then "
        "    local loco = t.front_movers[1] or t.back_movers[1] "
        "    out[#out + 1] = { "
        "      id = t.id, state = t.state, "
        "      station = t.station and t.station.backer_name or nil, "
        "      schedule = safe(function() return #t.get_schedule().get_records() end), "
        "      position = loco and loco.position or nil, "
        "      surface = loco and loco.surface.name or nil, "
        "      passengers = #t.passengers} "
        "  end end "
        "return out end)()"
    ) % lua.lua_string(surface)


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

async def _poller(server, config):
    interval = max(30, int(config.get("poll_interval_seconds", 120)))
    while True:
        await asyncio.sleep(interval)
        if not server.is_server_startup():
            continue
        try:
            broken, stalled = await _look(server, config)
        except QueryError as exc:
            server.logger.debug("Train poll skipped: %s", exc)
            continue
        except Exception:
            server.logger.exception("Train polling failed")
            continue

        limit = int(config.get("max_reported", 5))
        for train in (broken + stalled)[:limit]:
            # Report a given train once per state, not once per poll: a train
            # with no path stays that way until somebody fixes the rails.
            key = (train.get("id"), train.get("state"))
            if key in _state["reported"]:
                continue
            _state["reported"].add(key)
            await _say(server, server.tr(
                "alert.stuck" if train in stalled else "alert.no_path",
                train=describe(train),
                minutes=int(train.get("waited", 0) / 60),
                gps=_gps(train),
            ))
        live = {(t.get("id"), t.get("state")) for t in broken + stalled}
        _state["reported"] &= live


async def _look(server, config):
    trains = await server.lua_json(trains_query()) or []
    stuck_after = max(60.0, float(config.get("stuck_after_minutes", 15)) * 60)
    return classify(trains, _state["since"], time.monotonic(), stuck_after)


def _gps(train: dict) -> str:
    position = train.get("position") or {}
    return lua.gps(position.get("x", 0), position.get("y", 0),
                   train.get("surface") or "nauvis")


async def _say(server, message: str) -> None:
    server.logger.warning(message)
    if _state["config"].get("announce_in_game", True):
        try:
            await server.game_print(message)
        except QueryError:
            pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def _cmd_report(source):
    server = source.server
    try:
        trains = await server.lua_json(trains_query()) or []
    except QueryError as exc:
        await source.reply(server.tr("report.failed", error=exc))
        return
    if not trains:
        await source.reply(server.tr("report.none"))
        return

    counts: dict[str, int] = {}
    for train in trains:
        name = STATE_NAMES.get(train.get("state"), "?")
        counts[name] = counts.get(name, 0) + 1
    await source.reply(server.tr("report.header", count=len(trains)))
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        await source.reply(server.tr("report.state", state=name, count=count))


async def _cmd_stuck(source):
    server = source.server
    try:
        broken, stalled = await _look(server, _state["config"])
    except QueryError as exc:
        await source.reply(server.tr("report.failed", error=exc))
        return
    if not broken and not stalled:
        await source.reply(server.tr("report.all_moving"))
        return
    for train in broken:
        await source.reply(server.tr(
            "alert.no_path", train=describe(train), minutes=0, gps=_gps(train)))
    for train in stalled:
        await source.reply(server.tr(
            "alert.stuck", train=describe(train),
            minutes=int(train.get("waited", 0) / 60), gps=_gps(train)))
