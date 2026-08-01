"""Watch the world for things worth announcing: threats crossing a line, and progress.

Alerts and milestones are one plugin because they are one mechanism -- poll the
world, compare against what was seen last time, announce transitions. Splitting
them would mean two timers asking the same server the same questions. Each half
has its own config switch, so you can run either alone.

**Alerts** fire once per threshold crossing, not once per poll: evolution passing
50% is news, evolution sitting at 51% is not.

**Milestones** are the moments a run is measured by -- a technology finishing, a
rocket launching, the first time a player joins.
"""

from __future__ import annotations

import asyncio
import json

from factorio_reforge.command.builder import Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "world_watch",
    "version": "1.0.0",
    "name": "World Watch",
    "description": "Evolution and pollution alerts, plus research and rocket milestones",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "poll_interval_seconds": 120,
    "surface": "nauvis",

    "alerts_enabled": True,
    #: Announce when evolution first passes each of these (0-1).
    "evolution_thresholds": [0.25, 0.5, 0.75, 0.9, 0.95],
    #: Announce when total pollution first passes each of these.
    "pollution_thresholds": [10000, 50000, 100000, 500000],
    "alert_in_game": True,
    "alert_telegram": True,

    "milestones_enabled": True,
    "announce_research": True,
    "announce_rockets": True,
    #: Take a snapshot when a milestone lands, so the moment is recoverable.
    "snapshot_on_milestone": False,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(
        config=config, server=server, task=None,
        seen=_load_seen(server),
    )
    server.register_command(
        Literal("!!watch").requires(PermissionLevel.USER).runs(_cmd_status)
    )
    server.register_help_message("!!watch", server.tr("help"), PermissionLevel.USER)
    _state["task"] = asyncio.create_task(_poller(server, config))


async def on_unload(server):
    task = _state.get("task")
    if task is not None:
        task.cancel()
    _state.clear()


# -- persistence ------------------------------------------------------------
#
# What has already been announced has to outlive a restart, or every restart
# would replay every milestone the world has ever passed.

def _seen_path(server):
    return server.get_data_folder() / "seen.json"


def _load_seen(server) -> dict:
    path = _seen_path(server)
    default = {"evolution": 0.0, "pollution": 0.0, "rockets": 0, "technologies": []}
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        server.logger.warning("world_watch state is unreadable; starting fresh")
        return default
    return {**default, **data} if isinstance(data, dict) else default


def _save_seen(server) -> None:
    path = _seen_path(server)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(_state["seen"]), encoding="utf-8")
    temp.replace(path)


# -- polling ----------------------------------------------------------------

async def _poller(server, config):
    interval = max(30, int(config.get("poll_interval_seconds", 120)))
    while True:
        await asyncio.sleep(interval)
        if not server.is_server_startup():
            continue
        try:
            await _poll_once(server, config)
        except QueryError as exc:
            server.logger.debug("world_watch poll skipped: %s", exc)
        except Exception:
            server.logger.exception("world_watch poll failed")


async def _poll_once(server, config):
    surface = config.get("surface", "nauvis")
    changed = False

    if config.get("alerts_enabled", True):
        alerts = await server.lua_json(lua.world_alerts(surface))
        changed |= await _check_evolution(server, config, alerts.get("evolution", 0.0))
        changed |= await _check_pollution(server, config, alerts.get("pollution", 0.0))

    if config.get("milestones_enabled", True):
        research = await server.lua_json(lua.research_state())
        changed |= await _check_rockets(server, config, research.get("rockets_launched", 0))
        changed |= await _check_research(server, config)

    if changed:
        _save_seen(server)


async def _check_evolution(server, config, value: float) -> bool:
    crossed = _crossings(
        config.get("evolution_thresholds", []), _state["seen"]["evolution"], value
    )
    if not crossed:
        _state["seen"]["evolution"] = max(_state["seen"]["evolution"], value)
        return False
    _state["seen"]["evolution"] = value
    for threshold in crossed:
        await _announce(
            server, config,
            server.tr("alert.evolution", threshold=f"{threshold * 100:.0f}%",
                      value=f"{value * 100:.2f}%"),
            server.tr("alert.evolution_detail"),
        )
    return True


async def _check_pollution(server, config, value: float) -> bool:
    crossed = _crossings(
        config.get("pollution_thresholds", []), _state["seen"]["pollution"], value
    )
    if not crossed:
        _state["seen"]["pollution"] = max(_state["seen"]["pollution"], value)
        return False
    _state["seen"]["pollution"] = value
    for threshold in crossed:
        await _announce(
            server, config,
            server.tr("alert.pollution", threshold=f"{threshold:,.0f}", value=f"{value:,.0f}"),
            server.tr("alert.pollution_detail"),
        )
    return True


async def _check_rockets(server, config, launched: int) -> bool:
    if not config.get("announce_rockets", True):
        return False
    previous = _state["seen"]["rockets"]
    if launched <= previous:
        return False
    _state["seen"]["rockets"] = launched
    message = (
        server.tr("milestone.first_rocket") if previous == 0 and launched == 1
        else server.tr("milestone.rocket", count=launched)
    )
    await _announce(server, config, message, "", milestone=True)
    return True


async def _check_research(server, config) -> bool:
    if not config.get("announce_research", True):
        return False
    finished = set(await server.lua_json(lua.researched_technologies()) or [])
    known = set(_state["seen"]["technologies"])
    new = finished - known
    if not new:
        return False

    _state["seen"]["technologies"] = sorted(finished)
    # First run against an established world would otherwise announce hundreds
    # of already-finished technologies in one burst.
    if not known and len(new) > 5:
        server.logger.info(
            "world_watch recorded %d already-researched technologies without announcing them",
            len(new),
        )
        return True

    for name in sorted(new):
        await _announce(
            server, config,
            server.tr("milestone.research", tag=lua.technology_tag(name), name=name),
            "", milestone=True,
        )
    return True


def _crossings(thresholds, previous: float, current: float) -> list:
    """Thresholds strictly between the last seen value and the current one."""
    return sorted(t for t in thresholds if previous < t <= current)


async def _announce(server, config, message: str, detail: str = "", *, milestone=False):
    server.logger.info("%s", message)

    if config.get("alert_in_game", True):
        try:
            await server.game_print(f"[FactorioReforge] {message}")
        except QueryError:
            pass

    if config.get("alert_telegram", True):
        bridge = server.get_plugin_instance("telegram_bridge")
        if bridge is not None:
            text = message + (f"\n<i>{detail}</i>" if detail else "")
            await bridge.broadcast(text)

    if milestone and config.get("snapshot_on_milestone", False):
        try:
            await server.snapshot(message[:60], created_by="world_watch")
        except Exception as exc:
            server.logger.warning("Milestone snapshot failed: %s", exc)


# -- command ----------------------------------------------------------------

async def _cmd_status(source):
    seen = _state["seen"]
    config = _state["config"]
    try:
        alerts = await source.server.lua_json(lua.world_alerts(config.get("surface", "nauvis")))
        research = await source.server.lua_json(lua.research_state())
    except QueryError as exc:
        await source.reply(source.server.tr("status.failed", error=exc))
        return

    tr = source.server.tr
    await source.reply(tr("status.evolution", value=f"{alerts.get('evolution', 0) * 100:.2f}%"))
    await source.reply(tr("status.pollution", value=f"{alerts.get('pollution', 0):,.0f}"))
    await source.reply(tr(
        "status.research",
        done=research.get("researched", 0), total=research.get("total", 0),
        current=(tr("status.researching", name=research["current"])
                 if research.get("current") else tr("status.idle")),
    ))
    await source.reply(tr("status.rockets", count=research.get("rockets_launched", 0)))

    remaining = [
        t for t in config.get("evolution_thresholds", []) if t > seen.get("evolution", 0)
    ]
    if remaining:
        await source.reply(tr("status.next_alert", threshold=f"{remaining[0] * 100:.0f}%"))
