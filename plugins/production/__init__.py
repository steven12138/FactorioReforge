"""Track what the factory actually produces, and draw it.

Factorio's own production graph is per-client and vanishes when you leave.
Sampling ``get_flow_count`` on a timer builds a server-side history that
outlives sessions, so you can ask "how is iron doing" from chat or a phone.

Two renderings, chosen for where they land: a Unicode sparkline for chat and
Telegram, where a text line is all that fits, and an SVG the web panel serves,
where a real chart is worth having. No plotting library either way.
"""

from __future__ import annotations

import asyncio
import json
import time

from factorio_reforge.command.builder import GreedyText, Literal, Text
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "production",
    "version": "1.0.0",
    "name": "Production Stats",
    "description": "Sample production rates over time and chart them",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "enabled": True,
    #: Items to sample on a timer. Others can still be queried on demand.
    "watch": [
        "iron-plate", "copper-plate", "steel-plate", "electronic-circuit",
        "advanced-circuit", "processing-unit", "petroleum-gas", "science-pack",
    ],
    "sample_interval_seconds": 300,
    #: How many samples to keep per item (288 x 5min = 24h).
    "history_length": 288,
    "surface": "nauvis",
}

SPARK = "▁▂▃▄▅▆▇█"
_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, server=server, history=_load_history(server), task=None)

    server.register_command(
        Literal("!!prod")
        .requires(PermissionLevel.USER)
        .runs(_cmd_overview)
        .then(Literal("top").runs(_cmd_top))
        .then(Literal("watch").requires(PermissionLevel.ADMIN)
              .then(Text("item").runs(_cmd_watch)))
        .then(GreedyText("item").runs(_cmd_item))
    )
    server.register_help_message("!!prod [item]", server.tr("help"), PermissionLevel.USER)

    if config.get("enabled", True):
        _state["task"] = asyncio.create_task(_sampler(server, config))


async def on_unload(server):
    task = _state.get("task")
    if task is not None:
        task.cancel()
    if _state.get("history"):
        _save_history(server)
    _state.clear()


# -- sampling ---------------------------------------------------------------

async def _sampler(server, config):
    interval = max(30, int(config.get("sample_interval_seconds", 300)))
    while True:
        await asyncio.sleep(interval)
        if not server.is_server_startup():
            continue
        try:
            await _sample_once(server, config)
        except QueryError as exc:
            server.logger.debug("Production sample skipped: %s", exc)
        except Exception:
            server.logger.exception("Production sampling failed")


async def _sample_once(server, config):
    surface = config.get("surface", "nauvis")
    limit = int(config.get("history_length", 288))
    now = time.time()

    for item in config.get("watch", []):
        data = await server.lua_json(lua.production_rate(item, "one_minute", surface))
        if not data:
            continue
        series = _state["history"].setdefault(item, [])
        series.append([round(now), data.get("produced", 0), data.get("consumed", 0)])
        # Trim in place so the list object stays shared with anything holding it.
        if len(series) > limit:
            del series[: len(series) - limit]

    _save_history(server)


def _history_path(server):
    return server.get_data_folder() / "history.json"


def _load_history(server) -> dict:
    path = _history_path(server)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        server.logger.warning("Production history is unreadable; starting fresh")
        return {}
    return data if isinstance(data, dict) else {}


def _save_history(server) -> None:
    path = _history_path(server)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(_state["history"]), encoding="utf-8")
    temp.replace(path)


# -- public helpers, used by web_panel --------------------------------------

def get_history(item: str | None = None) -> dict:
    """Sampled history: ``{item: [[epoch, produced_per_min, consumed_per_min]]}``."""
    history = _state.get("history") or {}
    return {item: history.get(item, [])} if item else dict(history)


def sparkline(values: list[float], width: int = 24) -> str:
    """Render numbers as a single line of block characters."""
    if not values:
        return ""
    sample = values[-width:]
    low, high = min(sample), max(sample)
    if high <= low:
        # A flat series is real information; draw it as a flat line rather than
        # scaling noise up into a fake mountain range.
        return SPARK[0] * len(sample) if high == 0 else SPARK[len(SPARK) // 2] * len(sample)
    span = high - low
    return "".join(SPARK[min(len(SPARK) - 1, int((v - low) / span * len(SPARK)))] for v in sample)


def svg_chart(item: str, series: list[list], width: int = 640, height: int = 180) -> str:
    """A standalone SVG line chart. No JavaScript, no external anything."""
    if not series:
        return f'<svg width="{width}" height="{height}"><text x="10" y="20">no data yet</text></svg>'

    produced = [row[1] for row in series]
    peak = max(produced) or 1
    step = width / max(1, len(series) - 1) if len(series) > 1 else width

    points = " ".join(
        f"{i * step:.1f},{height - 20 - (value / peak) * (height - 40):.1f}"
        for i, value in enumerate(produced)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{item} production">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="2" points="{points}"/>'
        f'<text x="4" y="14" font-size="12" fill="currentColor">{item} - peak {peak:,}/min</text>'
        f"</svg>"
    )


# -- commands ---------------------------------------------------------------

async def _cmd_overview(source):
    config = _state["config"]
    watched = config.get("watch", [])
    if not watched:
        await source.reply(source.server.tr("overview.nothing_watched"))
        return
    await source.reply(source.server.tr("overview.header"))
    for item in watched:
        series = _state["history"].get(item, [])
        try:
            data = await source.server.lua_json(
                lua.production_rate(item, "one_minute", config.get("surface", "nauvis"))
            )
        except QueryError as exc:
            await source.reply(source.server.tr("overview.unavailable", item=item, error=exc))
            continue
        spark = sparkline([row[1] for row in series])
        await source.reply(source.server.tr(
            "overview.entry", item=item, rate=f"{data.get('produced', 0):,}",
            spark=f"  {spark}" if spark else source.server.tr("overview.no_history"),
        ))


async def _cmd_item(source, ctx):
    item = ctx["item"].strip()
    config = _state["config"]
    try:
        data = await source.server.lua_json(
            lua.production_rate(item, "one_minute", config.get("surface", "nauvis"))
        )
    except QueryError as exc:
        if "Unknown item name" in str(exc):
            # Factorio wants the internal name, and its message does not say so.
            # Guessing what was meant is more use than repeating the error.
            await _suggest_item(source, item)
            return
        await source.reply(source.server.tr("item.failed", item=item, error=exc))
        return
    if not data:
        await source.reply(source.server.tr("item.none", item=item))
        return

    tr = source.server.tr
    await source.reply(tr("item.header", item=item))
    await source.reply(tr("item.rates", produced=f"{data.get('produced', 0):,}",
                          consumed=f"{data.get('consumed', 0):,}"))
    await source.reply(tr("item.total", total=f"{data.get('total_produced', 0):,}"))
    series = _state["history"].get(item, [])
    if series:
        interval = config.get("sample_interval_seconds", 300) // 60
        await source.reply(tr("item.history",
                              spark=sparkline([row[1] for row in series]), minutes=interval))


async def _suggest_item(source, wanted: str) -> None:
    """Turn "Unknown item name" into something actionable.

    Factorio takes internal names -- ``iron-plate``, not ``iron`` -- and its
    error says only that the name is unknown. Matching against what the factory
    has actually produced turns a dead end into a list of things to try, and
    those are exactly the items the asker is likely to care about.
    """
    tr = source.server.tr
    await source.reply(tr("unknown.no_such_item", item=wanted))
    await source.reply(tr("unknown.internal_names"))

    try:
        rows = await source.server.lua_json(
            lua.production_totals(_state["config"].get("surface", "nauvis"), limit=200)
        )
    except QueryError:
        await source.reply(tr("unknown.try_top"))
        return

    needle = wanted.lower().replace(" ", "-")
    matches = [row["name"] for row in rows or [] if needle in row["name"].lower()]
    if matches:
        await source.reply(tr("unknown.did_you_mean", items=", ".join(matches[:8])))
    else:
        await source.reply(tr("unknown.try_top"))


async def _cmd_top(source):
    config = _state["config"]
    try:
        rows = await source.server.lua_json(
            lua.production_totals(config.get("surface", "nauvis"), limit=15)
        )
    except QueryError as exc:
        await source.reply(source.server.tr("top.failed", error=exc))
        return
    if not rows:
        await source.reply(source.server.tr("top.nothing"))
        return
    await source.reply(source.server.tr("top.header"))
    for row in rows:
        await source.reply(source.server.tr(
            "top.entry", name=row["name"], count=f"{row['produced']:,}"))


async def _cmd_watch(source, ctx):
    item = ctx["item"].strip()
    config = _state["config"]
    watch = list(config.get("watch", []))
    if item in watch:
        await source.reply(source.server.tr("watch.already", item=item))
        return
    watch.append(item)
    config["watch"] = watch
    source.server.save_config_simple(config)
    await source.reply(source.server.tr("watch.added", item=item))
