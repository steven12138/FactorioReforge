"""The built-in ``!!FR`` command tree.

Registered by the core rather than by a plugin file, so these controls exist
even when every plugin fails to load -- which is exactly when you need them.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from factorio_reforge.command.builder import (
    CommandContext,
    GreedyText,
    Integer,
    Literal,
    Text,
)
from factorio_reforge.command.source import CommandSource
from factorio_reforge.config import CONFIG_FILE
from factorio_reforge.permission import PermissionLevel
from factorio_reforge.plugin.manager import CORE_VERSION
from factorio_reforge.saves.manager import NoSlotAvailable, SaveError, format_duration

if TYPE_CHECKING:
    from factorio_reforge.core.server import ReforgeServer


def build(server: ReforgeServer):
    """Build the ``!!FR`` tree bound to ``server``."""
    prefix = server.config.command_prefix + "FR"
    tr = server.tr

    async def status(source: CommandSource):
        process = server.process
        lines = [
            tr("status.header", version=CORE_VERSION, uptime=_duration(server.uptime)),
            tr("status.server_with_pid", state=process.state.value, pid=process.pid,
               uptime=_duration(process.uptime or 0))
            if process.pid else tr("status.server", state=process.state.value),
            tr("status.rcon", state=tr(
                "status.connected" if server.interface.is_rcon_running()
                else "status.disconnected")),
            tr("status.plugins", count=len(server.plugins.list_ids())),
            tr("status.backups", count=len(server.saves.list())),
        ]
        if server.interface.is_rcon_running():
            try:
                players = await server.interface.get_online_players()
                lines.append(tr("status.online", count=len(players),
                                names=", ".join(players) or tr("common.none")))
            except Exception as exc:
                lines.append(tr("status.online_unavailable", error=exc))
        for line in lines:
            await source.reply(line)

    async def plugin_list(source: CommandSource):
        ids = server.plugins.list_ids()
        if not ids:
            await source.reply(tr("plugin.none_loaded"))
            return
        await source.reply(tr("plugin.count", count=len(ids)))
        for plugin_id in ids:
            plugin = server.plugins.get(plugin_id)
            changed = tr("plugin.file_changed") if plugin and plugin.file_changed() else ""
            commands = _commands_of(server, plugin_id)
            await source.reply(tr(
                "plugin.entry",
                id=plugin_id,
                version=plugin.metadata.version,
                description=plugin.metadata.description or tr("common.none"),
                changed=changed,
            ))
            if commands:
                await source.reply(tr("plugin.entry_commands", commands=", ".join(commands)))
        await source.reply(tr("plugin.help_hint", prefix=server.config.command_prefix))

    async def plugin_reload(source: CommandSource, ctx: CommandContext):
        plugin_id = ctx["plugin_id"]
        ok = await server.plugins.reload(plugin_id)
        await source.reply(tr("plugin.reloaded", id=plugin_id) if ok
                           else tr("plugin.reload_failed", id=plugin_id))

    async def plugin_unload(source: CommandSource, ctx: CommandContext):
        plugin_id = ctx["plugin_id"]
        ok = await server.plugins.unload(plugin_id)
        await source.reply(tr("plugin.unloaded", id=plugin_id) if ok
                           else tr("plugin.not_found", id=plugin_id))

    async def reload_changed(source: CommandSource):
        changed = await server.plugins.reload_changed()
        await source.reply(
            tr("plugin.reloaded_changed", names=", ".join(changed)) if changed
            else tr("plugin.nothing_changed")
        )

    async def server_start(source: CommandSource):
        await source.reply(tr("server.starting") if await server.start_server()
                           else tr("server.already_running"))

    async def server_stop(source: CommandSource):
        await source.reply(tr("server.stopping"))
        await server.stop_server()
        await source.reply(tr("server.stopped"))

    async def server_restart(source: CommandSource):
        await source.reply(tr("server.restarting"))
        await server.restart_server()

    async def server_kill(source: CommandSource):
        await source.reply(tr("server.killing"))
        await server.interface.kill()

    async def exit_all(source: CommandSource):
        await source.reply(tr("server.exiting"))
        await server.shutdown(stop_server=True)

    async def permission_list(source: CommandSource):
        entries = server.permissions.all()
        await source.reply(tr("permission.default", level=server.permissions.default_level.label))
        if not entries:
            await source.reply(tr("permission.no_overrides"))
            return
        for player, level in sorted(entries.items()):
            await source.reply(f"  {player}: {level.label}")

    async def permission_set(source: CommandSource, ctx: CommandContext):
        try:
            level = server.permissions.set(ctx["player"], ctx["level"])
        except ValueError as exc:
            await source.reply(str(exc))
            return
        await source.reply(tr("permission.changed", player=ctx["player"], level=level.label))

    async def lang_status(source: CommandSource):
        i18n = server.i18n
        await source.reply(tr("lang.current", language=i18n.language))
        await source.reply(tr("lang.available", languages=", ".join(i18n.languages())))
        await source.reply(tr("lang.keys", count=i18n.key_count()))
        for language in i18n.languages():
            missing = i18n.missing_keys(language)
            if missing:
                await source.reply(tr("lang.incomplete", language=language, count=len(missing)))

    async def lang_set(source: CommandSource, ctx: CommandContext):
        language = ctx["language"]
        available = server.i18n.languages()
        if language not in available:
            await source.reply(tr("lang.unknown", language=language,
                                  languages=", ".join(available)))
            return

        server.i18n.set_language(language)
        server.config.set_language(language, server.config.root / CONFIG_FILE)
        # Reply in the language just selected, so the change is self-evident.
        await source.reply(tr("lang.changed", language=language))
        missing = server.i18n.missing_keys(language)
        if missing:
            await source.reply(tr("lang.incomplete", language=language, count=len(missing)))

    async def lang_missing(source: CommandSource, ctx: CommandContext):
        missing = server.i18n.missing_keys(ctx["language"])
        if not missing:
            await source.reply(tr("lang.complete", language=ctx["language"]))
            return
        await source.reply(tr("lang.incomplete", language=ctx["language"], count=len(missing)))
        for key in missing[:40]:
            await source.reply(f"  {key}")

    async def help_message(source: CommandSource):
        """The index: core commands, then one grouped block per plugin."""
        await source.reply(tr("help.header", version=CORE_VERSION))
        for key, suffix in (
            ("status", " status"), ("plugin_list", " plugin list"),
            ("plugin_reload", " plugin reload <id>"), ("reload", " reload"),
            ("server", " server start|stop|restart|kill"),
            ("permission", " permission list|set <player> <level>"),
            ("lang", " lang [set <code>]"), ("exit", " exit"),
        ):
            await source.reply(f"  {prefix}{suffix:<28} {tr('help.' + key)}")

        entries = [
            entry for entry in server.plugins.registry.help_messages
            if source.has_permission(entry.permission)
        ]
        if not entries:
            return

        by_plugin: dict[str, list] = {}
        for entry in entries:
            by_plugin.setdefault(entry.plugin_id, []).append(entry)

        for plugin_id in sorted(by_plugin):
            plugin = server.plugins.get(plugin_id)
            title = plugin.metadata.name if plugin else plugin_id
            await source.reply("")
            await source.reply(tr("help.plugin_header", name=title, id=plugin_id))
            for entry in by_plugin[plugin_id]:
                await source.reply(f"  {entry.prefix:<28} {entry.message}")
        await source.reply("")
        await source.reply(tr("help.detail_hint", prefix=prefix))

    async def help_plugin(source: CommandSource, ctx: CommandContext):
        """Everything one plugin has to say about itself."""
        plugin_id = ctx["plugin_id"]
        plugin = server.plugins.get(plugin_id)
        if plugin is None:
            await source.reply(tr("plugin.not_found", id=plugin_id))
            return

        meta = plugin.metadata
        await source.reply(tr("help.plugin_header", name=meta.name, id=meta.id))
        await source.reply(tr("help.plugin_version", version=meta.version,
                              author=meta.author or tr("common.unknown")))
        if meta.description:
            await source.reply(f"  {meta.description}")

        entries = [
            entry for entry in server.plugins.registry.help_messages
            if entry.plugin_id == plugin_id and source.has_permission(entry.permission)
        ]
        if not entries:
            await source.reply(tr("help.no_commands"))
            return
        for entry in entries:
            await source.reply(f"  {entry.prefix:<28} {entry.message}")
            for line in entry.detail:
                await source.reply(f"      {line}")

    admin = PermissionLevel.ADMIN
    owner = PermissionLevel.OWNER

    return (
        Literal(prefix)
        .runs(help_message)
        .then(
            Literal("help").runs(help_message)
            .then(Text("plugin_id").runs(help_plugin))
        )
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
        .then(
            Literal("lang")
            .requires(PermissionLevel.USER)
            .runs(lang_status)
            .then(Literal("missing").then(Text("language").runs(lang_missing)))
            .then(
                Literal("set")
                .requires(admin)
                .then(Text("language").runs(lang_set))
            )
        )
        .then(Literal("exit").requires(owner).runs(exit_all))
    )


def _commands_of(server: ReforgeServer, plugin_id: str) -> list[str]:
    """The command prefixes a plugin registered, for the listing."""
    return sorted({
        entry.prefix.split()[0]
        for entry in server.plugins.registry.help_messages
        if entry.plugin_id == plugin_id and entry.prefix
    })


def build_save_commands(server: ReforgeServer):
    """The ``!!save`` tree, following QuickBackupM's command set.

    ``back`` stages a slot rather than acting on it; ``confirm`` starts an
    abortable countdown and only then touches the world. Two deliberate
    steps, because a mistyped slot number would otherwise replace a world in
    one keystroke.
    """
    prefix = server.config.command_prefix + "save"
    saves = server.saves
    tr = server.tr
    #: The staged slot, shared by everyone -- as in QBM, one restore at a time.
    staged: dict[str, Any] = {"slot": None, "at": 0.0, "by": ""}
    CONFIRM_WINDOW = 60.0

    async def make(source: CommandSource, ctx: CommandContext):
        comment = ctx.get("comment", "")
        await source.reply(tr("save.saving"))
        try:
            slot = await server.create_snapshot(
                comment, created_by=str(source.player or "console")
            )
        except NoSlotAvailable as exc:
            await source.reply(tr("save.no_slot", error=exc))
            return
        except Exception as exc:
            await source.reply(tr("save.failed", error=exc))
            return
        await source.reply(tr("save.created", slot=slot.describe()))

    async def listing(source: CommandSource):
        rows = saves.all_slots()
        total = saves.total_size() / (1024 * 1024)
        await source.reply(tr("save.header", size=f"{total:.1f}"))
        for index, info in rows:
            if info is None:
                await source.reply(tr("save.slot_empty", slot=index))
                continue
            protection = saves.protection_of(index)
            guard = ""
            if protection:
                remaining = protection - info.age_seconds
                guard = (
                    tr("save.protected", duration=format_duration(remaining))
                    if remaining > 0 else tr("save.protection_expired")
                )
            await source.reply(f"  {info.describe()}{guard}")

        overwrite = saves.get_overwrite()
        if overwrite is not None:
            await source.reply(tr("save.overwrite", time=overwrite.created_at_text))
        await source.reply(tr("save.restore_hint", prefix=prefix))

    async def back(source: CommandSource, ctx: CommandContext):
        slot = ctx.get("slot", 1)
        try:
            info = saves.validate(slot)
        except SaveError as exc:
            await source.reply(str(exc))
            return
        staged.update(slot=slot, at=time.monotonic(), by=str(source))
        await source.reply(tr("save.about_to_restore", slot=info.describe()))
        await source.reply(
            tr("save.confirm_hint", prefix=prefix, seconds=int(CONFIRM_WINDOW))
        )

    async def confirm(source: CommandSource):
        slot = staged["slot"]
        if slot is None:
            await source.reply(tr("save.nothing_staged", prefix=prefix))
            return
        if time.monotonic() - staged["at"] > CONFIRM_WINDOW:
            staged["slot"] = None
            await source.reply(tr("save.confirm_expired"))
            return
        staged["slot"] = None

        try:
            slot_info = await server.rollback(
                slot,
                countdown=server.config.saves.restore_countdown,
                requested_by=str(source.player or "console"),
            )
        except Exception as exc:
            await source.reply(tr("save.restore_failed", error=exc))
            return
        await source.reply(tr("save.restored", slot=slot_info.describe()))

    async def abort(source: CommandSource):
        # Two things can be cancelled: a staged slot, and a countdown that is
        # already running. QBM's abort covers both, so this does too.
        if server.abort_rollback():
            await source.reply(tr("save.cancelling_countdown"))
            return
        if staged["slot"] is not None:
            staged["slot"] = None
            await source.reply(tr("common.cancelled"))
            return
        await source.reply(tr("common.nothing_to_cancel"))

    async def delete(source: CommandSource, ctx: CommandContext):
        try:
            info = saves.delete(ctx["slot"])
        except SaveError as exc:
            await source.reply(str(exc))
            return
        await source.reply(tr("save.deleted", slot=info.describe()))

    async def rename(source: CommandSource, ctx: CommandContext):
        try:
            info = saves.rename(ctx["slot"], ctx["comment"])
        except SaveError as exc:
            await source.reply(str(exc))
            return
        await source.reply(tr("save.renamed", slot=info.describe()))

    # QBM's default permissions: anyone may back up, staff may restore.
    user = PermissionLevel.USER
    helper = PermissionLevel.HELPER

    return (
        Literal(prefix)
        .runs(listing)
        .then(Literal("list").runs(listing))
        .then(
            Literal("make").requires(user).runs(make)
            .then(GreedyText("comment").runs(make))
        )
        .then(
            Literal("back").requires(helper).runs(back)
            .then(Integer("slot").runs(back))
        )
        .then(Literal("confirm").requires(user).runs(confirm))
        .then(Literal("abort").requires(user).runs(abort))
        .then(Literal("del").requires(helper).then(Integer("slot").runs(delete)))
        .then(
            Literal("rename").requires(helper)
            .then(Integer("slot").then(GreedyText("comment").runs(rename)))
        )
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
