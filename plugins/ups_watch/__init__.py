"""Watch UPS, and say something before the factory becomes unplayable.

Factorio's failure mode is not a crash, it is a slow decline: the base grows,
the update rate slips from 60 to 55 to 40, and by the time anyone says "the game
feels laggy" it has been getting worse for a week. There is no UPS API, so this
samples ``game.tick`` against the wall clock -- the difference over a known
interval *is* the update rate.

One measured detail decides whether this is useful or a nuisance. On a server
with ``auto_pause`` and nobody online, the tick barely moves: a probe on 2.0.77
read **0.5 ticks/s** on an idle server. Reporting that as a collapse would page
the operator every night. So a sample with nobody connected is not a sample.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time

from factorio_reforge.command.builder import Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "ups_watch",
    "version": "1.0.0",
    "name": "UPS Watchdog",
    "description": "Measure the update rate and warn before the factory bogs down",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

#: Factorio's target update rate. Everything here is relative to it.
NOMINAL_UPS = 60.0

DEFAULT_CONFIG = {
    "enabled": True,
    "sample_interval_seconds": 60,
    #: Announce when the average drops below this, and again when it recovers.
    "warn_below_ups": 55.0,
    "critical_below_ups": 45.0,
    #: Samples in the window whose *median* is judged. One slow tick is not a
    #: trend: an autosave or a chunk-generating burst dips a single sample, and
    #: a median ignores that while still moving on a real decline.
    "window": 5,
    #: Tell everyone in game, not just the log.
    "announce_in_game": True,
    #: Keep this many samples for `!!ups` history.
    "history_length": 240,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, monitor=Monitor(config), task=None)

    server.register_command(
        Literal("!!ups")
        .requires(PermissionLevel.USER)
        .runs(_cmd_report)
        .then(Literal("why").requires(PermissionLevel.HELPER).runs(_cmd_why))
    )
    server.register_help_message(
        "!!ups", server.tr("help"), PermissionLevel.USER,
        detail=(server.tr("detail.why"),),
    )

    if config.get("enabled", True):
        _state["task"] = asyncio.create_task(_sampler(server, config))


async def on_unload(server):
    task = _state.get("task")
    if task is not None:
        task.cancel()
    _state.clear()


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Sample:
    at: float
    """Wall clock, monotonic."""

    tick: int
    players: int

    @property
    def counts(self) -> bool:
        """A paused world is not a slow one.

        With ``auto_pause`` on and nobody connected the tick advances at
        roughly nothing, and calling that a UPS collapse is how a watchdog
        teaches people to ignore it.
        """
        return self.players > 0


class Monitor:
    """Turns tick samples into an update rate, and rates into one alert."""

    def __init__(self, config: dict):
        self.window = max(1, int(config.get("window", 5)))
        self.warn = float(config.get("warn_below_ups", 55.0))
        self.critical = float(config.get("critical_below_ups", 45.0))
        self.history_length = int(config.get("history_length", 240))
        self.samples: list[Sample] = []
        self.rates: list[tuple[float, float]] = []
        #: "", "warn" or "critical" -- the level already announced.
        self.level = ""

    def add(self, sample: Sample) -> float | None:
        """Record a sample; returns the update rate it produced, if any."""
        previous = self.samples[-1] if self.samples else None
        self.samples.append(sample)
        del self.samples[:-2]

        if previous is None:
            return None
        # Both ends must be running. A window that starts paused and ends busy
        # would report the average of two different worlds.
        if not (previous.counts and sample.counts):
            return None
        elapsed = sample.at - previous.at
        if elapsed <= 0:
            return None

        rate = (sample.tick - previous.tick) / elapsed
        self.rates.append((time.time(), rate))
        if len(self.rates) > self.history_length:
            del self.rates[: len(self.rates) - self.history_length]
        return rate

    @property
    def average(self) -> float | None:
        """The *median* of the recent window, which is what gets judged.

        Not the mean. An autosave, a chunk-generation burst or a player pasting
        a huge blueprint dips exactly one sample, and a mean carries that dip
        into the verdict -- five samples of 60, 60, 30, 60, 60 average to 54 and
        would be announced as a slow factory. A median ignores a single outlier
        completely while still moving as soon as the decline is real, which is
        the distinction between a watchdog people read and one they mute.
        """
        recent = sorted(rate for _, rate in self.rates[-self.window:])
        if not recent:
            return None
        middle = len(recent) // 2
        if len(recent) % 2:
            return recent[middle]
        return (recent[middle - 1] + recent[middle]) / 2

    def verdict(self) -> tuple[str, float] | None:
        """The level to announce, if it changed. ``("", ups)`` means recovered.

        Only transitions are reported. A base sitting at 48 UPS is not news
        every minute; it was news once.
        """
        average = self.average
        if average is None or len(self.rates) < self.window:
            return None

        level = ""
        if average < self.critical:
            level = "critical"
        elif average < self.warn:
            level = "warn"

        if level == self.level:
            return None
        self.level = level
        return level, average


def describe(ups: float) -> str:
    """``52.4 UPS (87%)`` -- the percentage is what people actually compare."""
    return f"{ups:.1f} UPS ({ups / NOMINAL_UPS * 100:.0f}%)"


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def sample_query() -> str:
    return (
        "(function() return {tick = game.tick, "
        "players = #game.connected_players, speed = game.speed} end)()"
    )


def cost_query(surface: str = "nauvis") -> str:
    """What the tick is being spent on, roughly, in one round trip.

    Not a profiler: ``game.create_profiler`` exists but reports through a
    localised string that is awkward to parse and meaningless without a
    baseline. Entity counts are cruder and actually actionable -- "you have
    forty thousand inserters" is a sentence somebody can do something about.
    """
    return (
        "(function() local s = game.get_surface(%s) or game.surfaces[1] "
        "local out = {} "
        "for _, t in pairs({'inserter', 'transport-belt', 'assembling-machine', "
        "  'furnace', 'mining-drill', 'logistic-robot', 'construction-robot', "
        "  'car', 'locomotive', 'electric-pole', 'lamp'}) do "
        "  out[t] = s.count_entities_filtered{type = t} end "
        "out['_enemy'] = s.count_entities_filtered{force = 'enemy'} "
        "out['_pollution'] = s.get_total_pollution() "
        "return out end)()" % lua.lua_string(surface)
    )


async def _sampler(server, config):
    interval = max(10, int(config.get("sample_interval_seconds", 60)))
    monitor: Monitor = _state["monitor"]
    while True:
        await asyncio.sleep(interval)
        if not server.is_server_startup():
            continue
        try:
            data = await server.lua_json(sample_query()) or {}
        except QueryError as exc:
            server.logger.debug("UPS sample skipped: %s", exc)
            continue
        except Exception:
            server.logger.exception("UPS sampling failed")
            continue

        monitor.add(Sample(
            at=time.monotonic(),
            tick=int(data.get("tick", 0)),
            players=int(data.get("players", 0)),
        ))
        verdict = monitor.verdict()
        if verdict is not None:
            await _announce(server, *verdict)


async def _announce(server, level: str, ups: float) -> None:
    key = {"": "recovered", "warn": "slow", "critical": "critical"}[level]
    message = server.tr(f"alert.{key}", ups=describe(ups))
    server.logger.warning(message) if level else server.logger.info(message)
    if _state["config"].get("announce_in_game", True):
        try:
            await server.game_print(message)
        except QueryError:
            pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def _cmd_report(source):
    monitor: Monitor = _state["monitor"]
    server = source.server
    average = monitor.average
    if average is None:
        await source.reply(server.tr("report.no_data"))
        return

    await source.reply(server.tr(
        "report.current", ups=describe(average), samples=len(monitor.rates)
    ))
    recent = [rate for _, rate in monitor.rates[-monitor.window:]]
    if recent:
        await source.reply(server.tr(
            "report.range", low=f"{min(recent):.1f}", high=f"{max(recent):.1f}"
        ))
    if monitor.level:
        await source.reply(server.tr("report.hint"))


async def _cmd_why(source):
    """What the tick is going on, when it is going somewhere."""
    server = source.server
    try:
        counts = await server.lua_json(cost_query()) or {}
    except QueryError as exc:
        await source.reply(server.tr("report.failed", error=exc))
        return

    pollution = counts.pop("_pollution", 0)
    enemies = counts.pop("_enemy", 0)
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
    await source.reply(server.tr("why.header"))
    for name, count in ranked:
        if count:
            await source.reply(server.tr("why.entry", name=name, count=f"{count:,}"))
    await source.reply(server.tr(
        "why.world", enemies=f"{enemies:,}", pollution=f"{pollution:,.0f}"
    ))


# -- used by web_panel and anything else that wants the series ---------------

def get_history() -> list[tuple[float, float]]:
    """``[(epoch, ups)]`` for whoever wants to draw it."""
    monitor = _state.get("monitor")
    return list(monitor.rates) if monitor else []
