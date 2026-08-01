"""The built-in ``!!FR`` command tree.

Registered by the core rather than by a plugin file, so these controls exist
even when every plugin fails to load -- which is exactly when you need them.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from factorio_reforge.command.builder import (
    CommandContext,
    GreedyText,
    Integer,
    Literal,
    Text,
)
from factorio_reforge.command.source import CommandSource
from factorio_reforge.permission import PermissionLevel
from factorio_reforge.plugin.manager import CORE_VERSION

if TYPE_CHECKING:
    from factorio_reforge.core.server import ReforgeServer


def build(server: "ReforgeServer"):
    """Build the ``!!FR`` tree bound to ``server``."""
    prefix = server.config.command_prefix + "FR"

    async def status(source: CommandSource):
        process = server.process
        lines = [
            f"FactorioReforge {CORE_VERSION} - up {_duration(server.uptime)}",
            f"Server: {process.state.value}"
            + (f" (pid {process.pid}, up {_duration(process.uptime or 0)})" if process.pid else ""),
            f"RCON: {'connected' if server.interface.is_rcon_running() else 'not connected'}",
            f"Plugins: {len(server.plugins.list_ids())} loaded",
            f"Snapshots: {len(server.saves.list())}",
        ]
        if server.interface.is_rcon_running():
            try:
                players = await server.interface.get_online_players()
                lines.append(f"Online ({len(players)}): {', '.join(players) or '-'}")
            except Exception as exc:
                lines.append(f"Online: unavailable ({exc})")
        for line in lines:
            await source.reply(line)

    async def plugin_list(source: CommandSource):
        ids = server.plugins.list_ids()
        if not ids:
            await source.reply("No plugins loaded")
            return
        await source.reply(f"{len(ids)} plugin(s):")
        for plugin_id in ids:
            plugin = server.plugins.get(plugin_id)
            changed = " [file changed]" if plugin and plugin.file_changed() else ""
            await source.reply(f"  {plugin.metadata}{changed}")

    async def plugin_reload(source: CommandSource, ctx: CommandContext):
        plugin_id = ctx["plugin_id"]
        ok = await server.plugins.reload(plugin_id)
        await source.reply(f"Reloaded {plugin_id}" if ok else f"Could not reload {plugin_id}")

    async def plugin_unload(source: CommandSource, ctx: CommandContext):
        plugin_id = ctx["plugin_id"]
        ok = await server.plugins.unload(plugin_id)
        await source.reply(f"Unloaded {plugin_id}" if ok else f"No such plugin: {plugin_id}")

    async def reload_changed(source: CommandSource):
        changed = await server.plugins.reload_changed()
        await source.reply(
            f"Reloaded: {', '.join(changed)}" if changed else "No plugin files have changed"
        )

    async def server_start(source: CommandSource):
        await source.reply("Starting..." if await server.start_server() else "Already running")

    async def server_stop(source: CommandSource):
        await source.reply("Stopping the server...")
        await server.stop_server()
        await source.reply("Server stopped")

    async def server_restart(source: CommandSource):
        await source.reply("Restarting the server...")
        await server.restart_server()

    async def server_kill(source: CommandSource):
        await source.reply("SIGKILL -- everything since the last save is lost")
        await server.interface.kill()

    async def exit_all(source: CommandSource):
        await source.reply("Stopping the server, then exiting")
        await server.shutdown(stop_server=True)

    async def permission_list(source: CommandSource):
        entries = server.permissions.all()
        await source.reply(f"Default level: {server.permissions.default_level.label}")
        if not entries:
            await source.reply("No per-player overrides")
            return
        for player, level in sorted(entries.items()):
            await source.reply(f"  {player}: {level.label}")

    async def permission_set(source: CommandSource, ctx: CommandContext):
        try:
            level = server.permissions.set(ctx["player"], ctx["level"])
        except ValueError as exc:
            await source.reply(str(exc))
            return
        await source.reply(f"{ctx['player']} is now {level.label}")

    async def help_message(source: CommandSource):
        await source.reply(f"FactorioReforge {CORE_VERSION}")
        await source.reply(f"  {prefix} status            - server and framework state")
        await source.reply(f"  {prefix} plugin list        - loaded plugins")
        await source.reply(f"  {prefix} plugin reload <id> - reload one plugin")
        await source.reply(f"  {prefix} reload             - reload every changed plugin")
        await source.reply(f"  {prefix} server start|stop|restart|kill")
        await source.reply(f"  {prefix} permission list|set <player> <level>")
        await source.reply(f"  {prefix} exit               - stop the server and quit")
        for help_entry in server.plugins.registry.help_messages:
            if source.has_permission(help_entry.permission):
                await source.reply(f"  {help_entry.prefix} - {help_entry.message}")

    admin = PermissionLevel.ADMIN
    owner = PermissionLevel.OWNER

    return (
        Literal(prefix)
        .runs(help_message)
        .then(Literal("help").runs(help_message))
        .then(Literal("status").requires(PermissionLevel.USER).runs(status))
        .then(
            Literal("plugin")
            .requires(admin)
            .runs(plugin_list)
            .then(Literal("list").runs(plugin_list))
            .then(Literal("reload").then(Text("plugin_id").runs(plugin_reload)))
            .then(Literal("unload").then(Text("plugin_id").runs(plugin_unload)))
        )
        .then(Literal("reload").requires(admin).runs(reload_changed))
        .then(
            Literal("server")
            .requires(admin)
            .then(Literal("start").runs(server_start))
            .then(Literal("stop").runs(server_stop))
            .then(Literal("restart").runs(server_restart))
            .then(Literal("kill").requires(owner).runs(server_kill))
        )
        .then(
            Literal("permission")
            .requires(admin)
            .runs(permission_list)
            .then(Literal("list").runs(permission_list))
            .then(
                Literal("set")
                .requires(owner)
                .then(Text("player").then(Text("level").runs(permission_set)))
            )
        )
        .then(Literal("exit").requires(owner).runs(exit_all))
    )


def build_save_commands(server: "ReforgeServer"):
    """The ``!!save`` tree. Lives in the core because rollback is a core concern.

    ``back`` deliberately does not act immediately: it arms a pending request
    that ``confirm`` completes, so a mistyped id cannot wipe a world in one
    keystroke.
    """
    prefix = server.config.command_prefix + "save"
    pending: dict[str, object] = {}

    async def make(source: CommandSource, ctx: CommandContext):
        comment = ctx.get("comment", "")
        await source.reply("Saving and snapshotting...")
        try:
            snapshot = await server.create_snapshot(
                comment, created_by=str(source.player or "console")
            )
        except Exception as exc:
            await source.reply(f"Snapshot failed: {exc}")
            return
        await source.reply(f"Created {snapshot.describe()}")

    async def listing(source: CommandSource):
        snapshots = server.saves.list()
        if not snapshots:
            await source.reply("No snapshots yet")
            return
        await source.reply(f"{len(snapshots)} snapshot(s), newest first:")
        for snapshot in snapshots[:20]:
            await source.reply(f"  {snapshot.describe()}")

    async def back(source: CommandSource, ctx: CommandContext):
        snapshot_id = ctx["id"]
        snapshot = server.saves.get(snapshot_id)
        if snapshot is None:
            await source.reply(f"No snapshot with id {snapshot_id}")
            return
        pending[str(source)] = (snapshot_id, time.monotonic())
        await source.reply(f"About to roll back to {snapshot.describe()}")
        await source.reply(
            f"This stops the server and replaces the current world. "
            f"Type '{prefix} confirm' within 60s to proceed, or '{prefix} abort' to cancel."
        )

    async def confirm(source: CommandSource):
        entry = pending.pop(str(source), None)
        if entry is None:
            await source.reply(f"Nothing to confirm. Start with '{prefix} back <id>'")
            return
        snapshot_id, requested_at = entry
        if time.monotonic() - requested_at > 60:
            await source.reply("That confirmation expired; run the command again")
            return
        await source.reply("Rolling back...")
        try:
            snapshot = await server.rollback(
                snapshot_id, requested_by=str(source.player or "console")
            )
        except Exception as exc:
            await source.reply(f"Rollback failed: {exc}")
            return
        await source.reply(f"Rolled back to {snapshot.describe()}")

    async def abort(source: CommandSource):
        await source.reply(
            "Rollback cancelled" if pending.pop(str(source), None) else "Nothing pending"
        )

    async def delete(source: CommandSource, ctx: CommandContext):
        ok = server.saves.delete(ctx["id"])
        await source.reply(f"Deleted snapshot #{ctx['id']}" if ok else "No such snapshot")

    helper = PermissionLevel.HELPER
    admin = PermissionLevel.ADMIN

    return (
        Literal(prefix)
        .runs(listing)
        .then(
            Literal("make")
            .requires(helper)
            .runs(make)
            .then(GreedyText("comment").runs(make))
        )
        .then(Literal("list").requires(PermissionLevel.USER).runs(listing))
        .then(Literal("back").requires(admin).then(Integer("id").runs(back)))
        .then(Literal("confirm").requires(admin).runs(confirm))
        .then(Literal("abort").requires(admin).runs(abort))
        .then(Literal("del").requires(admin).then(Integer("id").runs(delete)))
    )


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"
