"""Plugin event ids and their default listener names.

A plugin can react to an event three ways, same as MCDReforged:

    def on_player_joined(server, player, info): ...          # by name
    server.register_event_listener('reforge.player_joined', cb, priority=100)
    @event_listener('reforge.player_joined')

Callbacks may be sync or ``async def``; the dispatcher awaits what needs awaiting.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable


@dataclasses.dataclass(frozen=True)
class Event:
    id: str
    default_listener: str | None = None

    def __str__(self) -> str:
        return self.id


GENERAL_INFO = Event("reforge.general_info", "on_info")
"""Every parsed line, from the server or the console."""

USER_INFO = Event("reforge.user_info", "on_user_info")
"""Lines a human produced -- player chat, console input, plugin injection."""

SERVER_START_PRE = Event("reforge.server_start_pre", "on_server_start_pre")
SERVER_START = Event("reforge.server_start", "on_server_start")
SERVER_STARTUP = Event("reforge.server_startup", "on_server_startup")
"""Map finished loading; players can connect."""

SERVER_STOP_PRE = Event("reforge.server_stop_pre", "on_server_stop_pre")
"""About to stop. The server is still up -- announce things here."""

SERVER_STOP = Event("reforge.server_stop", "on_server_stop")
"""The process has exited, with its return code. Files it held are now free.

Fires *after* the process is gone, matching MCDReforged. Anything that touches
files Factorio owns -- mod-list.json above all -- must wait for this, because a
running server rewrites them from memory on exit.
"""
SERVER_CRASH = Event("reforge.server_crash", "on_server_crash")
"""Process died without us asking it to."""

REFORGE_START = Event("reforge.start", "on_reforge_start")
REFORGE_STOP = Event("reforge.stop", "on_reforge_stop")

PLAYER_JOINED = Event("reforge.player_joined", "on_player_joined")
PLAYER_LEFT = Event("reforge.player_left", "on_player_left")
PLAYER_DEATH = Event("reforge.player_death", "on_player_death")

#: A real Factorio event, pushed out of the game rather than polled for. The
#: payload is the dict the game sent; ``event`` names which one it was. See
#: :mod:`factorio_reforge.core.luahooks`.
LUA_EVENT = Event("reforge.lua_event", "on_lua_event")

RCON_CONNECTED = Event("reforge.rcon_connected", "on_rcon_connected")
RCON_LOST = Event("reforge.rcon_lost", "on_rcon_lost")

SNAPSHOT_CREATED = Event("reforge.snapshot_created", "on_snapshot_created")
ROLLBACK_STARTED = Event("reforge.rollback_started", "on_rollback_started")
ROLLBACK_FINISHED = Event("reforge.rollback_finished", "on_rollback_finished")

PLUGIN_LOADED = Event("reforge.plugin_loaded", "on_load")
PLUGIN_UNLOADED = Event("reforge.plugin_unloaded", "on_unload")

ALL_EVENTS: tuple[Event, ...] = tuple(
    value for value in list(globals().values()) if isinstance(value, Event)
)
BY_ID = {event.id: event for event in ALL_EVENTS}


@dataclasses.dataclass(frozen=True)
class EventListener:
    plugin_id: str
    callback: Callable
    priority: int = 1000

    def __lt__(self, other: EventListener) -> bool:
        return self.priority < other.priority


def event_listener(event: Event | str, priority: int = 1000):
    """Mark a function as a listener; the loader picks these up on import."""

    def decorator(func):
        listeners = getattr(func, "_reforge_listeners", [])
        listeners.append((event.id if isinstance(event, Event) else event, priority))
        func._reforge_listeners = listeners
        return func

    return decorator
