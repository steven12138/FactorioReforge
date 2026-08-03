"""Tell you your base is being eaten, including when nobody is there to see it.

Factorio has an alert system -- turret out of ammo, entity destroyed, no fuel,
robot network out of power -- but it belongs to *players*: ``player.get_alerts``
is per-player and only meaningful while they are connected. That covers the case
where somebody is online and already looking at the screen, and misses the case
worth waking up for: the wall coming down at four in the morning with the server
empty.

So there are two detectors, and the second is the point:

* **alerts**, read from connected players, relayed to chat and Telegram;
* **losses**, from counting the force's entities by type on a timer. Nothing
  destroys forty walls except an attack, and a count is cheap, works with the
  server empty, and needs no companion mod.

The second cannot say *what* attacked or exactly where. It can say "you lost 40
walls and 6 turrets in the last two minutes", which is the sentence that gets
somebody to log in.
"""

from __future__ import annotations

import asyncio
import time

from factorio_reforge.command.builder import Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "alerts",
    "version": "1.0.0",
    "name": "Alerts",
    "description": "Relay in-game alerts, and notice things being destroyed",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "enabled": True,
    "poll_interval_seconds": 60,
    #: Entity types worth counting. Structures that only disappear when
    #: something goes wrong -- not belts, which players remove constantly.
    "watch_types": [
        "wall", "gate", "ammo-turret", "electric-turret", "fluid-turret",
        "artillery-turret", "radar", "solar-panel", "accumulator",
        "assembling-machine", "furnace", "mining-drill", "roboport", "lab",
    ],
    #: How many of a type must vanish between polls before it is reported.
    #: One is somebody rebuilding; a dozen is an attack.
    "loss_threshold": 5,
    #: Relay alerts from players who are connected.
    "relay_player_alerts": True,
    #: Alert types to ignore -- these fire constantly on a working base.
    "ignore_alerts": ["no_material_for_construction", "not_enough_construction_robots"],
    "announce_in_game": True,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, counts={}, seen=set(), last_loss=0.0, task=None)

    server.register_command(
        Literal("!!alerts")
        .requires(PermissionLevel.USER)
        .runs(_cmd_report)
        .then(Literal("check").requires(PermissionLevel.HELPER).runs(_cmd_check))
    )
    server.register_help_message("!!alerts", server.tr("help"), PermissionLevel.USER)

    if config.get("enabled", True):
        _state["task"] = asyncio.create_task(_poller(server, config))


async def on_unload(server):
    task = _state.get("task")
    if task is not None:
        task.cancel()
    _state.clear()


# ---------------------------------------------------------------------------
# Pure logic -- what counts as news
# ---------------------------------------------------------------------------

def losses(before: dict[str, int], after: dict[str, int], threshold: int) -> dict[str, int]:
    """Types that lost at least ``threshold`` entities since the last count.

    Only drops. A base that grew is not news, and the first poll after a
    restart has nothing to compare against, which reads as no losses -- right,
    because a count from before a restart says nothing about now.
    """
    result = {}
    for name, previous in before.items():
        lost = previous - after.get(name, 0)
        if lost >= threshold:
            result[name] = lost
    return result


def alert_key(alert: dict) -> str:
    """Identity for de-duplication.

    Position is rounded to a chunk: the same turret complaining about ammo
    reports at slightly different coordinates as the entity is re-resolved, and
    relaying that repeatedly is how a useful alert becomes noise.
    """
    position = alert.get("position") or {}
    x = int(position.get("x", 0)) // 32
    y = int(position.get("y", 0)) // 32
    return f"{alert.get('type', '?')}@{x},{y}"


def new_alerts(alerts: list[dict], seen: set[str], ignore: list[str]) -> list[dict]:
    """Alerts not relayed before, minus the ones that fire on a healthy base."""
    fresh = []
    for alert in alerts:
        if alert.get("type") in ignore:
            continue
        key = alert_key(alert)
        if key in seen:
            continue
        seen.add(key)
        fresh.append(alert)
    return fresh


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def counts_query(types: list[str], surface: str = "nauvis") -> str:
    listed = "{" + ",".join(lua.lua_string(name) for name in types) + "}"
    return (
        "(function() local s = game.get_surface(%s) or game.surfaces[1] "
        "local out = {} "
        f"for _, t in pairs({listed}) do "
        "  out[t] = s.count_entities_filtered{force = 'player', type = t} end "
        "return out end)()"
    ) % lua.lua_string(surface)


def alerts_query() -> str:
    """Every connected player's alerts, flattened.

    Behind pcall because alerts are a client-side notion and a headless server
    with nobody connected has nothing to ask.
    """
    return (
        "(function() "
        "local function safe(f) local ok, v = pcall(f) if ok then return v end end "
        "local out = {} "
        "for _, p in pairs(game.connected_players) do "
        "  local groups = safe(function() return p.get_alerts{} end) or {} "
        "  for _, by_surface in pairs(groups) do "
        "    for kind, list in pairs(by_surface) do "
        "      for _, a in pairs(list) do "
        "        out[#out + 1] = {type = tostring(kind), "
        "          position = a.position, "
        "          entity = a.target and a.target.valid and a.target.name or nil, "
        "          message = a.message and tostring(a.message) or nil} "
        "      end end end end "
        "return out end)()"
    )


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

async def _poller(server, config):
    interval = max(15, int(config.get("poll_interval_seconds", 60)))
    while True:
        await asyncio.sleep(interval)
        if not server.is_server_startup():
            continue
        try:
            await _poll_once(server, config)
        except QueryError as exc:
            server.logger.debug("Alert poll skipped: %s", exc)
        except Exception:
            server.logger.exception("Alert polling failed")


async def _poll_once(server, config) -> None:
    types = list(config.get("watch_types", []))
    if types:
        counts = await server.lua_json(counts_query(types)) or {}
        previous = _state["counts"]
        _state["counts"] = counts
        if previous:
            lost = losses(previous, counts, int(config.get("loss_threshold", 5)))
            if lost:
                await _report_losses(server, lost)

    if config.get("relay_player_alerts", True):
        raw = await server.lua_json(alerts_query()) or []
        fresh = new_alerts(raw, _state["seen"], config.get("ignore_alerts", []))
        for alert in fresh[:5]:
            await _say(server, server.tr(
                "relay.alert",
                type=alert.get("type", "?"),
                what=alert.get("entity") or server.tr("relay.something"),
                gps=lua.gps(
                    (alert.get("position") or {}).get("x", 0),
                    (alert.get("position") or {}).get("y", 0),
                ),
            ))


async def _report_losses(server, lost: dict[str, int]) -> None:
    _state["last_loss"] = time.time()
    total = sum(lost.values())
    detail = ", ".join(f"{name} x{count}" for name, count in
                       sorted(lost.items(), key=lambda kv: -kv[1])[:5])
    await _say(server, server.tr("loss.detected", total=total, detail=detail))


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
    counts = _state.get("counts") or {}
    if not counts:
        await source.reply(server.tr("report.no_data"))
        return
    standing = sum(counts.values())
    await source.reply(server.tr("report.standing", count=f"{standing:,}"))
    last = _state.get("last_loss") or 0.0
    if last:
        ago = int((time.time() - last) / 60)
        await source.reply(server.tr("report.last_loss", minutes=ago))
    else:
        await source.reply(server.tr("report.quiet"))


async def _cmd_check(source):
    """Poll now rather than waiting for the timer."""
    server = source.server
    try:
        await _poll_once(server, _state["config"])
    except QueryError as exc:
        await source.reply(server.tr("report.failed", error=exc))
        return
    await _cmd_report(source)
