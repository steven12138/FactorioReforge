"""Dispatch ``!!``-prefixed lines to registered command trees."""

from __future__ import annotations

import logging
from typing import Optional

from factorio_reforge.command.builder import (
    ArgumentNode,
    CommandContext,
    CommandError,
    Literal,
    tokenize,
)
from factorio_reforge.command.source import CommandSource


class CommandManager:
    def __init__(self, prefix: str = "!!", logger: Optional[logging.Logger] = None):
        self.prefix = prefix
        self.logger = logger or logging.getLogger(__name__)
        #: root literal -> [(plugin_id, node)]; a list so a collision is visible
        #: rather than silently overwriting whichever plugin loaded first.
        self._roots: dict[str, list[tuple[str, ArgumentNode]]] = {}

    def register(self, plugin_id: str, node: ArgumentNode) -> None:
        if not isinstance(node, Literal):
            raise TypeError("a command tree must be rooted at a Literal node")
        existing = self._roots.setdefault(node.name, [])
        if existing:
            owners = ", ".join(pid for pid, _ in existing)
            self.logger.warning(
                "Plugin %r registers %r which is already owned by %s; both will be tried",
                plugin_id, node.name, owners,
            )
        existing.append((plugin_id, node))

    def unregister_plugin(self, plugin_id: str) -> None:
        for root, entries in list(self._roots.items()):
            kept = [entry for entry in entries if entry[0] != plugin_id]
            if kept:
                self._roots[root] = kept
            else:
                del self._roots[root]

    def clear(self) -> None:
        self._roots.clear()

    def roots(self) -> dict[str, list[str]]:
        return {root: [pid for pid, _ in entries] for root, entries in self._roots.items()}

    def looks_like_command(self, text: str) -> bool:
        return text.startswith(self.prefix)

    async def dispatch(self, source: CommandSource, text: str) -> bool:
        """Run ``text`` as a command. Returns True if a tree claimed it."""
        if not self.looks_like_command(text):
            return False
        tokens = tokenize(text.strip())
        if not tokens:
            return False
        entries = self._roots.get(tokens[0])
        if not entries:
            return False

        errors: list[str] = []
        for plugin_id, node in entries:
            try:
                await node.execute(source, tokens, CommandContext(source))
                return True
            except CommandError as exc:
                errors.append(str(exc))
            except Exception:
                self.logger.exception("Plugin %r raised while handling %r", plugin_id, text)
                await source.reply(f"Command failed: internal error in plugin {plugin_id}")
                return True

        await source.reply(errors[0] if errors else f"Unknown command: {tokens[0]}")
        return True
