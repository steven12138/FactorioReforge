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
    Literal,
    Text,
)
from factorio_reforge.command.source import CommandSource
from factorio_reforge.config import CONFIG_FILE
from factorio_reforge.i18n import PluginTranslator
from factorio_reforge.permission import PermissionLevel
from factorio_reforge.plugin.manager import CORE_VERSION
from factorio_reforge.saves.manager import (
    AUTO,
    MANUAL,
    NoSlotAvailable,
    SaveError,
    format_duration,
    parse_slot,
)

if TYPE_CHECKING:
    from factorio_reforge.core.server import ReforgeServer


#: Plugins listed per page of ``!!FR help``, for readers with no scrollback.
#: The in-game chat box shows roughly this many lines before the top is gone.
PLUGINS_PER_PAGE = 10


class HelpCommands:
    """The help handlers, shared by ``!!FR help`` and its shortcut ``!!help``.

    A class rather than a nest of closures because they are now built twice --
    once as a subcommand of the framework tree and once as a root of their own
    -- and two copies of this logic would be two things to keep in step.
    """

    def __init__(self, server: ReforgeServer):
        self.server = server
        self.tr = server.tr
        #: What to tell people to type. The short form, because it is the one
        #: worth learning and it works everywhere the long one does.
        self.prefix = server.config.command_prefix + "help"
        #: The framework tree, which the core row of the index names.
        self.framework = server.config.command_prefix + "FR"

    def describe_plugin(self, plugin_id: str, meta) -> str:
        """One line about a plugin, in the operator's language when it offers one.

        ``PLUGIN_METADATA["description"]`` is a Python literal and therefore
        always English. It used to be a detail; now it is the widest column of
        the help index, so a plugin's own catalogue gets to override it with a
        ``description`` key. Falling back to the metadata keeps third-party
        plugins working without one.
        """
        scoped = PluginTranslator(self.server.i18n, plugin_id)
        translated = scoped.tr("description")
        if translated and translated != "description":
            return translated
        if meta and meta.description:
            return meta.description
        return meta.name if meta else plugin_id

    def visible_entries(self, source: CommandSource) -> list:
        return [
            entry for entry in self.server.plugins.registry.help_messages
            if source.has_permission(entry.permission)
        ]

    async def help_message(self, source: CommandSource):
        """The bare ``!!FR help``. Kept to one parameter on purpose.

        The dispatcher decides what to pass by counting parameters, so a
        second one with a default would quietly receive the CommandContext.
        """
        await self.show_index(source, 1)

    async def show_index(self, source: CommandSource, page: int = 1):
        """The index: core commands, then one line per plugin.

        One *line* per plugin, not a block. With twenty-one plugins the grouped
        form ran past sixty lines, and a help screen you have to scroll past is
        one nobody reads to the end of -- the plugins near the end of the
        alphabet were effectively undiscoverable.

        Paginated only for players. The console and Telegram have scrollback;
        the in-game chat box does not, so it is the one place where a long
        answer genuinely loses information off the top.
        """
        entries = self.visible_entries(source)
        by_plugin: dict[str, list] = {}
        for entry in entries:
            by_plugin.setdefault(entry.plugin_id, []).append(entry)

        names = sorted(by_plugin)
        paged = source.player is not None
        size = PLUGINS_PER_PAGE if paged else len(names) or 1
        pages = max(1, -(-len(names) // size))
        page = max(1, min(page, pages))
        shown = names[(page - 1) * size: page * size]

        await source.reply(self.tr(
            "help.header_counts", version=CORE_VERSION,
            plugins=len(names), commands=len(entries),
            page=page, pages=pages,
        ))

        if page == 1:
            await source.reply(self.tr("help.core_header"))
            await source.reply(
                f"  {self.framework:<12} {self.tr('help.core_summary')}")
            # Backups are core but live under their own self.prefix, so the loop
            # above cannot reach them; they were missing entirely before.
            await source.reply(
                f"  {self.server.config.command_prefix + 'qb':<12} {self.tr('help.qb')}"
            )
            if names:
                await source.reply(self.tr("help.plugins_header", prefix=self.prefix))

        for plugin_id in shown:
            plugin = self.server.plugins.get(plugin_id)
            meta = plugin.metadata if plugin else None
            # The description, not the display name: "Alerts" next to `alerts`
            # is a column of nothing, while "attacks and in-game alerts" is the
            # line somebody scans for.
            summary = self.describe_plugin(plugin_id, meta)
            commands = " ".join(entry.prefix.split()[0] for entry in by_plugin[plugin_id])
            await source.reply(f"  {plugin_id:<14} {commands:<24} {summary}")

        if page < pages:
            await source.reply(self.tr("help.more", prefix=self.prefix, page=page + 1, pages=pages))

    async def help_lookup(self, source: CommandSource, ctx: CommandContext):
        """``!!FR help <something>``: a page, a plugin, or a search.

        One argument covering three things because they are the same question
        asked three ways, and making people remember which subcommand to use
        for which is worse than guessing correctly on their behalf.
        """
        term = str(ctx["plugin_id"]).strip()
        if term.isdigit():
            await self.show_index(source, int(term))
            return
        if self.server.plugins.get(term) is not None:
            await self.help_plugin(source, ctx)
            return
        await self.help_search(source, term)

    async def help_search(self, source: CommandSource, term: str):
        """Find a command by any part of its name, or a plugin by its own.

        `!!FR help ratio` should find the calculator. Browsing an index is the
        fallback, not the interface.
        """
        needle = term.lower().lstrip(self.server.config.command_prefix)
        matches = [
            entry for entry in self.visible_entries(source)
            if needle in entry.prefix.lower()
            or needle in entry.message.lower()
            or needle in entry.plugin_id.lower()
        ]
        if not matches:
            await source.reply(self.tr("help.no_match", term=term, prefix=self.prefix))
            return
        await source.reply(self.tr("help.matches", term=term, count=len(matches)))
        for entry in matches[:12]:
            await source.reply(f"  {entry.prefix:<28} {entry.message}")
            await source.reply(self.tr("help.from_plugin", id=entry.plugin_id, prefix=self.prefix))

    async def help_plugin(self, source: CommandSource, ctx: CommandContext):
        """Everything one plugin has to say about itself."""
        plugin_id = ctx["plugin_id"]
        plugin = self.server.plugins.get(plugin_id)
        if plugin is None:
            await source.reply(self.tr("plugin.not_found", id=plugin_id))
            return

        meta = plugin.metadata
        await source.reply(self.tr("help.plugin_header", name=meta.name, id=meta.id))
        await source.reply(self.tr("help.plugin_version", version=meta.version,
                              author=meta.author or self.tr("common.unknown")))
        if meta.description:
            await source.reply(f"  {meta.description}")

        entries = [
            entry for entry in self.server.plugins.registry.help_messages
            if entry.plugin_id == plugin_id and source.has_permission(entry.permission)
        ]
        if not entries:
            await source.reply(self.tr("help.no_commands"))
            return
        for entry in entries:
            await source.reply(f"  {entry.prefix:<28} {entry.message}")
            for line in entry.detail:
                await source.reply(f"      {line}")

    def tree(self):
        """``!!help`` as a command root of its own."""
        return (
            Literal(self.prefix)
            .runs(self.help_message)
            .then(Text("plugin_id").runs(self.help_lookup))
        )


def build(server: ReforgeServer):
    """Build the ``!!FR`` tree bound to ``server``."""
    prefix = server.config.command_prefix + "FR"
    tr = server.tr
    help_commands = HelpCommands(server)

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

    admin = PermissionLevel.ADMIN
    owner = PermissionLevel.OWNER

    return (
        Literal(prefix)
        .runs(help_commands.help_message)
        .then(
            Literal("help").runs(help_commands.help_message)
            .then(Text("plugin_id").runs(help_commands.help_lookup))
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


def build_save_commands(
    server: ReforgeServer, name: str = "qb", staged: dict[str, Any] | None = None
):
    """The ``!!qb`` tree, following QuickBackupM's command set and its name.

    ``back`` stages a slot rather than acting on it; ``confirm`` starts an
    abortable countdown and only then touches the world. Two deliberate
    steps, because a mistyped slot number would otherwise replace a world in
    one keystroke.

    Slots are addressed the way they are listed: ``3`` for one someone made,
    ``a3`` for one the schedule made. Built twice, under ``!!qb`` and the older
    ``!!save``, so nobody's habit breaks on the rename.
    """
    prefix = server.config.command_prefix + name
    saves = server.saves
    tr = server.tr
    #: The staged slot, shared by everyone -- as in QBM, one restore at a time.
    #: Passed in when the tree is built more than once, so staging under one
    #: name and confirming under the other is the same restore.
    staged = {"slot": None, "at": 0.0, "by": ""} if staged is None else staged
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

    async def show_ring(source: CommandSource, ring: str):
        for index, info in saves.all_slots(ring):
            ref = parse_slot(f"a{index}" if ring == AUTO else index)
            if info is None:
                await source.reply(tr("save.slot_empty", slot=str(ref)))
                continue
            protection = saves.protection_of(ref)
            guard = ""
            if protection:
                remaining = protection - info.age_seconds
                guard = (
                    tr("save.protected", duration=format_duration(remaining))
                    if remaining > 0 else tr("save.protection_expired")
                )
            await source.reply(f"  {info.describe()}{guard}")

    async def listing(source: CommandSource):
        total = saves.total_size() / (1024 * 1024)
        await source.reply(tr("save.header", size=f"{total:.1f}"))
        await show_ring(source, MANUAL)
        if saves.auto_slot_count:
            await source.reply(tr("save.auto_header"))
            await show_ring(source, AUTO)

        overwrite = saves.get_overwrite()
        if overwrite is not None:
            await source.reply(tr("save.overwrite", time=overwrite.created_at_text))
        await source.reply(tr("save.restore_hint", prefix=prefix))

    async def back(source: CommandSource, ctx: CommandContext):
        try:
            slot = parse_slot(ctx.get("slot", 1))
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
            info = saves.delete(parse_slot(ctx["slot"]))
        except SaveError as exc:
            await source.reply(str(exc))
            return
        await source.reply(tr("save.deleted", slot=info.describe()))

    async def rename(source: CommandSource, ctx: CommandContext):
        try:
            info = saves.rename(parse_slot(ctx["slot"]), ctx["comment"])
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
            .then(Text("slot").runs(back))
        )
        .then(Literal("confirm").requires(user).runs(confirm))
        .then(Literal("abort").requires(user).runs(abort))
        .then(Literal("del").requires(helper).then(Text("slot").runs(delete)))
        .then(
            Literal("rename").requires(helper)
            .then(Text("slot").then(GreedyText("comment").runs(rename)))
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


def build_help_command(server: ReforgeServer):
    """``!!help``, the same thing without having to remember ``!!FR``.

    Help is the one command somebody types when they do not know the commands,
    so making it the longest thing to type is backwards.
    """
    return HelpCommands(server).tree()
