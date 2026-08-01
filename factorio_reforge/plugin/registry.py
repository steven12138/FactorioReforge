"""What a plugin has registered, wiped and rebuilt on every load.

Making the registry disposable is what makes hot reload safe: unloading a plugin
is just dropping its registry, so a stale listener from a previous version can
never survive a reload.
"""

from __future__ import annotations

import bisect
import dataclasses
import logging
from collections.abc import Callable

from factorio_reforge.command.builder import ArgumentNode
from factorio_reforge.plugin.events import Event, EventListener


@dataclasses.dataclass
class HelpMessage:
    plugin_id: str
    prefix: str
    message: str
    permission: int = 0


class PluginRegistry:
    """One plugin's registrations."""

    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        self.event_listeners: dict[str, list[EventListener]] = {}
        self.commands: list[ArgumentNode] = []
        self.help_messages: list[HelpMessage] = []

    def add_listener(self, event: Event | str, callback: Callable, priority: int = 1000) -> None:
        event_id = event.id if isinstance(event, Event) else event
        listeners = self.event_listeners.setdefault(event_id, [])
        bisect.insort(listeners, EventListener(self.plugin_id, callback, priority))

    def add_command(self, node: ArgumentNode) -> None:
        self.commands.append(node)

    def add_help(self, prefix: str, message: str, permission: int = 0) -> None:
        self.help_messages.append(HelpMessage(self.plugin_id, prefix, message, permission))

    def clear(self) -> None:
        self.event_listeners.clear()
        self.commands.clear()
        self.help_messages.clear()


class GlobalRegistry:
    """The merged view the dispatcher reads, rebuilt whenever plugins change."""

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger(__name__)
        self.event_listeners: dict[str, list[EventListener]] = {}
        self.help_messages: list[HelpMessage] = []

    def rebuild(self, registries: list[PluginRegistry]) -> None:
        merged: dict[str, list[EventListener]] = {}
        help_messages: list[HelpMessage] = []
        for registry in registries:
            for event_id, listeners in registry.event_listeners.items():
                target = merged.setdefault(event_id, [])
                for listener in listeners:
                    bisect.insort(target, listener)
            help_messages.extend(registry.help_messages)
        self.event_listeners = merged
        self.help_messages = sorted(help_messages, key=lambda h: h.prefix)

    def listeners_for(self, event: Event | str) -> list[EventListener]:
        event_id = event.id if isinstance(event, Event) else event
        return self.event_listeners.get(event_id, [])
