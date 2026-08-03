"""Warn about the brownout before it happens, not after the lab stops.

A Factorio power failure is quiet: accumulators drain, machines slow instead of
stopping, and the first visible symptom is research taking longer than it should.
The number that predicts it is accumulator charge -- on a solar base, charge at
dawn is the entire story -- so that is what gets watched.

Charge is read as ``entity.energy`` summed over accumulators, against the
prototype's ``buffer_capacity`` (5 MJ, measured on 2.0.77). Reading every
accumulator individually would be a query proportional to the base, so the sum
is done in Lua and one number comes back.
"""

from __future__ import annotations

import asyncio

from factorio_reforge.command.builder import Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "power",
    "version": "1.0.0",
    "name": "Power Monitor",
    "description": "Accumulator charge and the supply gap, before the lights go out",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "enabled": True,
    "poll_interval_seconds": 120,
    #: Charge fractions to announce when crossed downwards, and again on the
    #: way back up. 0.3 is roughly "you will not survive the night".
    "charge_thresholds": [0.3, 0.1],
    "announce_in_game": True,
    "surface": "nauvis",
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, below=set(), last=None, task=None)

    server.register_command(
        Literal("!!power").requires(PermissionLevel.USER).runs(_cmd_report)
    )
    server.register_help_message("!!power", server.tr("help"), PermissionLevel.USER)

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

def crossings(fraction: float, thresholds: list[float], below: set[float]) -> list[tuple[float, bool]]:
    """Thresholds newly crossed, as ``(threshold, is_falling)``.

    State is a set of thresholds currently under, so a base hovering at 29%
    reports once rather than every two minutes. ``below`` is mutated.
    """
    events = []
    for threshold in sorted(thresholds, reverse=True):
        under = fraction < threshold
        was_under = threshold in below
        if under and not was_under:
            below.add(threshold)
            events.append((threshold, True))
        elif not under and was_under:
            below.discard(threshold)
            events.append((threshold, False))
    return events


def format_watts(watts: float) -> str:
    for unit, scale in (("GW", 1e9), ("MW", 1e6), ("kW", 1e3)):
        if abs(watts) >= scale:
            return f"{watts / scale:.1f} {unit}"
    return f"{watts:.0f} W"


def charge_bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(fraction * width))))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def power_query(surface: str = "nauvis") -> str:
    """Accumulator charge, and what the network made and used last tick.

    Summed in Lua: a megabase has thousands of accumulators and shipping them
    one JSON object each would cost more than the answer is worth.
    """
    return (
        "(function() "
        "local function safe(f) local ok, v = pcall(f) if ok then return v end end "
        "local s = game.get_surface(%s) or game.surfaces[1] "
        "local acc = s.find_entities_filtered{type = 'accumulator', force = 'player'} "
        "local stored, capacity = 0, 0 "
        "for _, a in pairs(acc) do "
        "  stored = stored + a.energy "
        "  capacity = capacity + (safe(function() "
        "    return a.prototype.electric_energy_source_prototype.buffer_capacity end) or 0) "
        "end "
        "local generated, used = 0, 0 "
        "local pole = s.find_entities_filtered{type = 'electric-pole', force = 'player', limit = 1}[1] "
        "if pole then "
        "  local stats = safe(function() return pole.electric_network_statistics end) "
        "  if stats then "
        "    for name in pairs(stats.output_counts) do "
        "      generated = generated + stats.get_flow_count{name = name, category = 'output', "
        "        precision_index = defines.flow_precision_index.one_minute} end "
        "    for name in pairs(stats.input_counts) do "
        "      used = used + stats.get_flow_count{name = name, category = 'input', "
        "        precision_index = defines.flow_precision_index.one_minute} end "
        "  end "
        "end "
        "return {accumulators = #acc, stored = stored, capacity = capacity, "
        "        generated = generated, used = used, "
        "        daytime = s.daytime} end)()"
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
            data = await server.lua_json(power_query(config.get("surface", "nauvis"))) or {}
        except QueryError as exc:
            server.logger.debug("Power poll skipped: %s", exc)
            continue
        except Exception:
            server.logger.exception("Power polling failed")
            continue

        _state["last"] = data
        capacity = float(data.get("capacity") or 0)
        if capacity <= 0:
            continue  # No accumulators: nothing to predict from.
        fraction = float(data.get("stored") or 0) / capacity
        for threshold, falling in crossings(
            fraction, [float(t) for t in config.get("charge_thresholds", [])], _state["below"]
        ):
            await _say(server, server.tr(
                "alert.falling" if falling else "alert.recovered",
                percent=f"{fraction * 100:.0f}",
                threshold=f"{threshold * 100:.0f}",
            ))


async def _say(server, message: str) -> None:
    server.logger.warning(message)
    if _state["config"].get("announce_in_game", True):
        try:
            await server.game_print(message)
        except QueryError:
            pass


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

async def _cmd_report(source):
    server = source.server
    try:
        data = await server.lua_json(
            power_query(_state["config"].get("surface", "nauvis"))) or {}
    except QueryError as exc:
        await source.reply(server.tr("report.failed", error=exc))
        return

    capacity = float(data.get("capacity") or 0)
    if not data.get("accumulators"):
        await source.reply(server.tr("report.no_accumulators"))
    else:
        fraction = (float(data.get("stored") or 0) / capacity) if capacity else 0.0
        await source.reply(server.tr(
            "report.charge",
            bar=charge_bar(fraction), percent=f"{fraction * 100:.0f}",
            count=data.get("accumulators", 0),
        ))

    generated = float(data.get("generated") or 0)
    used = float(data.get("used") or 0)
    if generated or used:
        await source.reply(server.tr(
            "report.flow",
            generated=format_watts(generated), used=format_watts(used),
            margin=format_watts(generated - used),
        ))
    daytime = data.get("daytime")
    if daytime is not None:
        await source.reply(server.tr("report.daytime", daytime=f"{float(daytime):.2f}"))
