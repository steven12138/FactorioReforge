"""The API surface plugins are given.

The split between the two transports is the thing to understand here:

* **stdin** carries anything fire-and-forget -- chat, admin commands, ``/quit``.
  It always works, needs no extra port, and returns nothing.
* **RCON** carries anything with an answer -- the player list, a Lua expression.
  It needs the server to be up and the port configured.

Methods say which one they use, and a method that needs RCON raises when it is
unavailable rather than pretending to have succeeded.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factorio_reforge.command.builder import ArgumentNode
from factorio_reforge.core import lua
from factorio_reforge.core.lua import parse_json_result
from factorio_reforge.core.rcon import RconError
from factorio_reforge.permission import PermissionLevel
from factorio_reforge.plugin.events import Event

if TYPE_CHECKING:
    from factorio_reforge.core.server import ReforgeServer
    from factorio_reforge.plugin.manager import LoadedPlugin
    from factorio_reforge.saves.manager import SaveManager


class ServerInterface:
    """Core-wide API. Plugins get the :class:`PluginServerInterface` subclass."""

    def __init__(self, server: ReforgeServer):
        self._server = server

    # -- logging -------------------------------------------------------------

    @property
    def logger(self) -> logging.Logger:
        return self._server.logger

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> bool:
        return await self._server.start_server()

    async def stop(self) -> bool:
        """Stop Factorio, leave FactorioReforge running."""
        return await self._server.stop_server()

    async def kill(self) -> None:
        """SIGKILL Factorio. Everything since the last save is lost."""
        await self._server.process.kill()

    async def restart(self) -> bool:
        return await self._server.restart_server()

    async def stop_exit(self) -> None:
        """Stop Factorio, then exit FactorioReforge."""
        await self._server.shutdown(stop_server=True)

    async def exit(self) -> None:
        """Exit FactorioReforge, leaving Factorio running."""
        await self._server.shutdown(stop_server=False)

    async def wait_until_stop(self, timeout: float | None = None) -> None:
        await self._server.process.wait_until_stopped(timeout)

    def is_server_running(self) -> bool:
        return self._server.process.is_running

    def is_server_startup(self) -> bool:
        return self._server.process.is_startup_done

    def is_rcon_running(self) -> bool:
        return self._server.rcon is not None and self._server.rcon.connected

    def get_server_pid(self) -> int | None:
        return self._server.process.pid

    def get_server_uptime(self) -> float | None:
        return self._server.process.uptime

    # -- output, via stdin ---------------------------------------------------

    async def execute(self, text: str) -> None:
        """Write a raw line to the server's stdin, exactly as typed."""
        await self._server.process.write(text)

    async def say(self, text: str) -> None:
        """Broadcast to everyone in the game.

        Comes back as ``[CHAT] <server>: ...``; the parser flags that as an echo
        so relay plugins do not loop on their own output.
        """
        await self._server.process.write(text)

    #: Same channel, kept as a separate name for parity with MCDReforged.
    broadcast = say

    async def tell(self, player: str, text: str) -> None:
        """Message one player. Needs RCON -- Factorio has no ``/tell``.

        Falls back to a broadcast when RCON is down, so a reply is never simply
        swallowed, and says so in the log.
        """
        if self.is_rcon_running():
            await self.lua_json(lua.print_to_player(player, text))
            return
        self.logger.warning("RCON unavailable; broadcasting a message meant for %s", player)
        await self.say(f"{player}: {text}")

    async def reply(self, source, text: str) -> None:
        """Answer wherever the command came from."""
        await source.reply(text)

    # -- queries, via RCON ---------------------------------------------------

    async def rcon_query(self, command: str) -> str:
        """Run a command through RCON and return its output."""
        if self._server.rcon is None:
            raise RconError("RCON is disabled in config.yml")
        return await self._server.rcon.execute(command)

    async def lua(self, code: str) -> str:
        """Evaluate Lua through ``/sc`` and return the raw text it printed.

        ``/sc`` (silent-command) is used rather than ``/c`` on purpose: ``/c``
        permanently marks the save as having used cheats.
        """
        return await self.rcon_query(f"/sc {code}")

    async def lua_json(self, expression: str) -> Any:
        """Evaluate a Lua *expression* and return it as parsed Python data.

        Prefer this over :meth:`lua` for anything you need to read: the reply
        comes back as JSON, so you get numbers and lists instead of text to pick
        apart, and a Lua error arrives as :class:`LuaError` rather than as a
        string that happens to start with "Cannot execute command".
        """
        return parse_json_result(await self.lua(lua.json_query(expression)))

    async def get_online_players(self) -> list[str]:
        """Just the names. Use :meth:`get_online_player_details` for more."""
        return [entry["name"] for entry in await self.get_online_player_details()]

    async def get_online_player_details(self) -> list[dict]:
        """Name, admin flag, playtime, position and surface for everyone online."""
        return await self.lua_json(lua.online_players()) or []

    async def get_all_players(self) -> list[dict]:
        """Everyone who has ever joined, with playtime and last-seen tick."""
        return await self.lua_json(lua.all_players()) or []

    async def get_player_info(self, player: str) -> dict | None:
        return await self.lua_json(lua.player_info(player))

    async def get_server_stats(self) -> dict:
        """Tick, playtime, pollution, evolution, current research, player counts."""
        return await self.lua_json(lua.server_stats()) or {}

    async def add_map_marker(
        self,
        position: dict,
        text: str,
        *,
        surface: str = "nauvis",
        icon: dict | None = None,
    ) -> dict | None:
        """Place a chart tag players can click on the in-game map.

        Returns ``None`` if Factorio rejected the position, so callers must not
        assume a marker exists just because the call returned.
        """
        return await self.lua_json(lua.add_map_marker(surface, position, text, icon))

    async def teleport_player(
        self, player: str, position: dict, surface: str | None = None
    ) -> dict:
        return await self.lua_json(lua.teleport(player, position, surface)) or {}

    async def game_print(self, message: str) -> None:
        """Broadcast via Lua rather than stdin.

        Unlike :meth:`say`, this does not come back as ``[CHAT] <server>:``, so
        it is the right choice for plugin output that should not be relayed
        onward by chat bridges.
        """
        await self.lua_json(lua.print_to_all(message))

    async def server_save(self) -> None:
        """Ask the server to write the map to disk now (stdin)."""
        await self.execute("/server-save")

    # -- saves ---------------------------------------------------------------

    @property
    def saves(self) -> SaveManager:
        return self._server.saves

    async def snapshot(self, comment: str = "", *, created_by: str = "unknown"):
        return await self._server.create_snapshot(comment, created_by=created_by)

    async def rollback(
        self, slot: int, *, countdown: float = 10.0, requested_by: str = "unknown"
    ):
        """Restore a backup slot. Stops the server, swaps the world, starts it."""
        return await self._server.rollback(
            slot, countdown=countdown, requested_by=requested_by
        )

    # -- permissions ---------------------------------------------------------

    def get_permission_level(self, player: str | None) -> PermissionLevel:
        return self._server.permissions.get(player)

    def set_permission_level(self, player: str, level) -> PermissionLevel:
        return self._server.permissions.set(player, level)

    # -- plugins -------------------------------------------------------------

    async def load_plugin(self, path: str) -> bool:
        return await self._server.plugins.load_all() is not None

    async def unload_plugin(self, plugin_id: str) -> bool:
        return await self._server.plugins.unload(plugin_id)

    async def reload_plugin(self, plugin_id: str) -> bool:
        return await self._server.plugins.reload(plugin_id)

    def get_plugin_list(self) -> list[str]:
        return self._server.plugins.list_ids()

    def get_plugin_metadata(self, plugin_id: str):
        plugin = self._server.plugins.get(plugin_id)
        return plugin.metadata if plugin else None

    def get_plugin_instance(self, plugin_id: str):
        """The plugin's module, for calling another plugin's public functions."""
        plugin = self._server.plugins.get(plugin_id)
        return plugin.module if plugin else None

    async def dispatch_event(self, event: Event | str, *args: Any) -> None:
        await self._server.plugins.dispatch(event, *args)


class PluginServerInterface(ServerInterface):
    """A :class:`ServerInterface` bound to one plugin.

    Registration goes through the plugin's own registry, which is what makes
    unloading complete: drop the registry and every trace of the plugin's
    listeners and commands goes with it.
    """

    def __init__(self, server: ReforgeServer, plugin: LoadedPlugin):
        super().__init__(server)
        self._plugin = plugin

    @property
    def plugin_id(self) -> str:
        return self._plugin.id

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"plugin.{self._plugin.id}")

    def register_event_listener(
        self, event: Event | str, callback: Callable, priority: int = 1000
    ) -> None:
        self._plugin.registry.add_listener(event, callback, priority)
        self._server.plugins._rebuild()

    def register_command(self, node: ArgumentNode) -> None:
        self._plugin.registry.add_command(node)
        self._server.commands.register(self._plugin.id, node)

    def register_help_message(self, prefix: str, message: str, permission: int = 0) -> None:
        self._plugin.registry.add_help(prefix, message, permission)
        self._server.plugins._rebuild()

    # -- per-plugin storage --------------------------------------------------

    def get_data_folder(self) -> Path:
        folder = self._server.config.resolve("config") / self._plugin.id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def load_config_simple(
        self, filename: str = "config.json", default: dict | None = None
    ) -> dict:
        """Read the plugin's config, writing defaults out on first run.

        Missing keys are filled in from ``default`` so adding a setting in a new
        plugin version does not require the operator to edit their file.
        """
        default = dict(default or {})
        path = self.get_data_folder() / filename
        if not path.is_file():
            self.save_config_simple(default, filename)
            return default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.error("%s is unreadable (%s); using defaults", path, exc)
            return default
        if not isinstance(data, dict):
            self.logger.error("%s must contain an object; using defaults", path)
            return default
        merged = {**default, **data}
        if merged != data:
            self.save_config_simple(merged, filename)
        return merged

    def save_config_simple(self, data: dict, filename: str = "config.json") -> None:
        path = self.get_data_folder() / filename
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
