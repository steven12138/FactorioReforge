"""Take a snapshot on a timer, and around events worth being able to undo.

Doubles as a worked example of the plugin API: config, event listeners, a
command tree, and a background task that is cleaned up on unload.
"""

from __future__ import annotations

import asyncio
import time

from factorio_reforge.command.builder import Literal
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "auto_snapshot",
    "version": "1.0.0",
    "name": "Auto Snapshot",
    "description": "Periodic and event-driven snapshots",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "enabled": True,
    "interval_minutes": 30,
    #: Snapshot when the last player leaves, so an empty server is a safe point.
    "on_last_player_left": True,
    #: Skip the timer when nobody is online. With auto_pause the world has not
    #: advanced, so those snapshots would all be byte-identical.
    "skip_when_empty": True,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, task=None, last=0.0)

    server.register_command(
        Literal("!!autosnap")
        .requires(PermissionLevel.HELPER)
        .runs(_report)
        .then(Literal("now").runs(_snapshot_now))
    )
    server.register_help_message("!!autosnap", server.tr("help"), PermissionLevel.HELPER)

    if config.get("enabled", True):
        _state["task"] = asyncio.create_task(_timer(server, config))
        server.logger.info(server.tr("scheduled", minutes=config["interval_minutes"]))


async def on_unload(server):
    task = _state.get("task")
    if task is not None:
        task.cancel()
    _state.clear()


async def _timer(server, config):
    interval = max(1, int(config.get("interval_minutes", 30))) * 60
    while True:
        await asyncio.sleep(interval)
        if not server.is_server_startup():
            continue
        if config.get("skip_when_empty", True):
            try:
                if not await server.get_online_players():
                    server.logger.debug("Nobody online; skipping the timed snapshot")
                    continue
            except Exception:
                # RCON down: take the snapshot rather than skip it.
                pass
        await _take(server, server.tr("reason_scheduled"))


async def on_player_left(server, player, info=None):
    config = _state.get("config") or {}
    if not config.get("on_last_player_left", True):
        return
    try:
        if await server.get_online_players():
            return
    except Exception:
        return
    await _take(server, server.tr("reason_last_left", player=player))


async def _take(server, reason: str) -> None:
    try:
        snapshot = await server.snapshot(reason, created_by="auto_snapshot")
    except Exception as exc:
        server.logger.error(server.tr("failed", error=exc))
        return
    _state["last"] = time.time()
    server.logger.info(server.tr("made", slot=snapshot.describe()))


async def _report(source):
    last = _state.get("last", 0.0)
    tr = source.server.tr
    when = time.strftime("%H:%M:%S", time.localtime(last)) if last else tr("never")
    interval = (_state.get("config") or {}).get("interval_minutes", "?")
    await source.reply(tr("report", interval=interval, when=when))


async def _snapshot_now(source):
    await source.reply(source.server.tr("taking"))
    await _take(source.server, source.server.tr("reason_manual"))
    await source.reply(source.server.tr("done"))
