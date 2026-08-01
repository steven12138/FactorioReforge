"""Search, install, update and remove mods from the Factorio mod portal.

Works from the console, from in-game chat, and from Telegram -- the Telegram
side registers through ``telegram_bridge``'s service, so this plugin never
imports python-telegram-bot.

Two things this plugin will not let you forget, because both bite hard:

* **Mods only load at startup.** Nothing here takes effect until the server
  restarts, so every mutating command says so.
* **The client mod set must match the server's.** Installing a mod on a live
  public server locks out every player who does not have it. Installs are
  therefore admin-only and confirmed.

Credentials come from the config, falling back to the ``player-data.json`` of
whoever runs FactorioReforge. The token is a secret: it is never logged, never
echoed, and never put anywhere but the download query string.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from factorio_reforge.command.builder import GreedyText, Literal, Text
from factorio_reforge.mods.manager import ModError, ModManager
from factorio_reforge.mods.portal import (
    AuthRequired,
    ModPortal,
    PortalError,
    read_player_data_credentials,
)
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "mod_manager",
    "version": "1.0.0",
    "name": "Mod Manager",
    "description": "Install and update mods from the portal, from chat or Telegram",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
    # Soft dependency: Telegram commands are registered only if the bridge is
    # loaded. Declaring it hard would make this plugin unusable without Telegram.
}

DEFAULT_CONFIG = {
    #: Leave blank to read them from player_data_path.
    "username": "",
    "token": "",
    "player_data_path": "~/.factorio/player-data.json",
    #: Only offer releases built for this Factorio version. Blank = auto-detect
    #: from the running server.
    "factorio_version": "",
    "install_dependencies": True,
    "index_ttl_hours": 6,
    #: Offer to restart the server after a change, since nothing applies until
    #: it does.
    "prompt_restart": True,
    "search_results": 8,
}

_state: dict = {}


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)

    username = config.get("username") or ""
    token = config.get("token") or ""
    if not (username and token):
        path = Path(config.get("player_data_path", "")).expanduser()
        username, token = read_player_data_credentials(path)
        if username and token:
            server.logger.info("Using mod portal credentials for %s from %s", username, path)

    portal = ModPortal(
        server.get_data_folder() / "cache",
        username=username,
        token=token,
        index_ttl_hours=config.get("index_ttl_hours", 6),
        logger=server.logger,
    )
    mods = ModManager(_mods_directory(server), portal, logger=server.logger)

    _state.clear()
    _state.update(server=server, config=config, portal=portal, mods=mods, factorio_version="")

    if not portal.has_credentials:
        server.logger.warning(
            "mod_manager has no portal credentials; search and info work, installing does not. "
            "Set username/token in %s", server.get_data_folder() / "config.json",
        )

    _register_commands(server)
    _register_telegram(server)
    asyncio.create_task(_learn_version(server))
    # "telegram.ready" is a custom event id, so it needs explicit registration
    # rather than the on_<event> naming convention. It fires whenever the bridge
    # starts polling, which is how our commands survive a bridge reload.
    server.register_event_listener("telegram.ready", on_telegram_ready)


async def _learn_version(server):
    version = await _detect_factorio_version(server)
    if version:
        _state["factorio_version"] = version
        server.logger.info("mod_manager will only offer mods built for Factorio %s", version)
    else:
        server.logger.warning(
            "mod_manager could not determine the Factorio version; it cannot filter "
            "releases for compatibility, and installing the newest build of a mod may "
            "stop the server from starting. Set factorio_version in its config."
        )


async def on_unload(server):
    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is not None:
        bridge.unregister_plugin("mod_manager")
    _state.clear()


async def on_telegram_ready(server):
    """The bridge restarted; put our commands back."""
    _register_telegram(server)


async def on_server_stop(server, code=None):
    """Put our mod list back after Factorio rewrites it on exit.

    Factorio holds mod-list.json in memory and writes its own version out when
    it stops, discarding anything changed while it ran. This fires once the
    process is actually gone, which is what makes "install a mod, then restart"
    work -- doing it any earlier just gets overwritten again.
    """
    mods = _state.get("mods")
    if mods is None:
        return
    try:
        changed = await asyncio.to_thread(mods.reapply_intent)
    except ModError as exc:
        server.logger.error("Could not restore the mod list: %s", exc)
        return
    if changed:
        server.logger.info("Mod list restored: %s", " ".join(changed))


async def _detect_factorio_version(server) -> str:
    """Ask the binary its version, rather than the running server.

    Reading it over RCON was wrong twice over: RCON is not connected yet when
    the startup event fires, and it needs the server to be up at all. Asking
    ``factorio --version`` works before the first start and cannot come back
    empty at the moment it matters -- which it did, and the result was
    installing a mod built for 2.1 onto a 2.0.77 server, which then refused to
    boot.
    """
    config = _state.get("config") or {}
    if config.get("factorio_version"):
        return config["factorio_version"]

    core = server._server  # noqa: SLF001
    argv = core.config.command_argv
    binary = Path(argv[0])
    if not binary.is_absolute():
        binary = core.config.working_dir_path / binary
    if not binary.is_file():
        return ""

    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary), "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (TimeoutError, OSError):
        return ""

    match = re.search(r"Version:\s*(\d+\.\d+\.\d+)", out.decode("utf-8", errors="replace"))
    return match.group(1) if match else ""


async def on_server_crash(server, code):
    """A crash right after a mod change is almost certainly the mod change.

    Factorio exits with a non-zero code when a mod will not load, and the reason
    scrolls past in the startup log. Saying so here turns a confusing crash loop
    into an obvious next step.
    """
    if not _state.get("recent_change"):
        return
    server.logger.error(
        "The server exited with code %s shortly after a mod change (%s). "
        "An incompatible mod will stop the server from starting -- check the log "
        "above for 'Failed to load mod', then use !!mod remove <name>.",
        code, _state["recent_change"],
    )


def _mods_directory(server) -> Path:
    """The mods directory of the headless install we are managing."""
    core = server._server  # noqa: SLF001 -- config lives on the core
    argv = core.config.command_argv
    if "--mod-directory" in argv:
        candidate = Path(argv[argv.index("--mod-directory") + 1])
        if not candidate.is_absolute():
            candidate = core.config.working_dir_path / candidate
        return candidate
    return core.config.working_dir_path / "mods"


def _target_version() -> str:
    return _state.get("factorio_version") or ""


# ---------------------------------------------------------------------------
# in-game / console commands
# ---------------------------------------------------------------------------

def _register_commands(server):
    admin = PermissionLevel.ADMIN
    server.register_command(
        Literal("!!mod")
        .requires(PermissionLevel.USER)
        .runs(_cmd_help)
        .then(Literal("search").then(GreedyText("query").runs(_cmd_search)))
        .then(Literal("info").then(Text("name").runs(_cmd_info)))
        .then(Literal("list").runs(_cmd_list))
        .then(
            Literal("install").requires(admin)
            .then(
                Text("name").runs(_cmd_install)
                .then(Text("version").runs(_cmd_install))
            )
        )
        .then(Literal("remove").requires(admin).then(Text("name").runs(_cmd_remove)))
        .then(Literal("enable").requires(admin).then(Text("name").runs(_cmd_enable)))
        .then(Literal("disable").requires(admin).then(Text("name").runs(_cmd_disable)))
        .then(Literal("updates").requires(admin).runs(_cmd_updates))
        .then(Literal("refresh").requires(admin).runs(_cmd_refresh))
    )
    server.register_help_message("!!mod", server.tr("help"), PermissionLevel.USER)


async def _cmd_help(source):
    tr = source.server.tr
    for index in range(8):
        await source.reply(tr(f"usage.{index}"))


async def _cmd_search(source, ctx):
    portal = _state["portal"]
    query = ctx["query"]
    await source.reply(source.server.tr("search.searching", query=query))
    try:
        results = await portal.search(
            query,
            limit=_state["config"].get("search_results", 8),
            factorio_version=_target_version(),
        )
    except PortalError as exc:
        await source.reply(source.server.tr("search.failed", error=exc))
        return
    if not results:
        await source.reply(source.server.tr("search.nothing"))
        return
    await source.reply(source.server.tr("search.header", count=len(results)))
    for mod in results:
        await source.reply(f"  {mod.describe()}")


async def _cmd_info(source, ctx):
    name = ctx["name"]
    try:
        data = await _state["portal"].get_mod(name, full=True)
        releases = await _state["portal"].get_releases(name)
    except PortalError as exc:
        await source.reply(str(exc))
        return

    tr = source.server.tr
    await source.reply(tr("info.title", title=data.get("title", name), name=name,
                          owner=data.get("owner", "?")))
    if data.get("summary"):
        await source.reply(f"  {data['summary']}")
    await source.reply(tr("info.downloads", count=f"{data.get('downloads_count', 0):,}"))
    if releases:
        latest = releases[-1]
        await source.reply(tr("info.latest", version=latest.version,
                              factorio=latest.factorio_version))
        required = latest.required_dependencies()
        if required:
            await source.reply(tr("info.requires", deps=", ".join(
                f"{n}{(' ' + s) if s else ''}" for n, s in required)))
    installed = _state["mods"].get_installed(name)
    if installed:
        await source.reply(tr("info.installed", mod=installed.describe()))


async def _cmd_list(source):
    mods = _state["mods"].list_installed()
    extra = [mod for mod in mods if not mod.builtin]
    await source.reply(source.server.tr("list.header", count=len(extra)))
    for mod in extra:
        await source.reply(f"  {mod.describe()}")
    if not extra:
        await source.reply(source.server.tr("list.none"))


async def _cmd_install(source, ctx):
    name = ctx["name"]
    version = ctx.get("version")
    await source.reply(source.server.tr(
        "install.starting", name=name, version=f" v{version}" if version else ""))
    try:
        installed = await _state["mods"].install(
            name,
            version=version,
            factorio_version=_target_version(),
            with_dependencies=_state["config"].get("install_dependencies", True),
        )
    except (AuthRequired, PortalError, ModError) as exc:
        await source.reply(source.server.tr("install.failed", error=exc))
        return

    _state["recent_change"] = f"installed {name}"
    for mod in installed:
        await source.reply(source.server.tr("install.installed", mod=mod.describe()))
    await _warn_restart(source)


async def _cmd_remove(source, ctx):
    try:
        removed = await _state["mods"].remove(ctx["name"])
    except ModError as exc:
        await source.reply(str(exc))
        return
    if not removed:
        await source.reply(source.server.tr("remove.not_installed", name=ctx["name"]))
        return
    await source.reply(source.server.tr("remove.done", name=ctx["name"]))
    await _warn_restart(source)


async def _cmd_enable(source, ctx):
    await _toggle(source, ctx["name"], True)


async def _cmd_disable(source, ctx):
    await _toggle(source, ctx["name"], False)


async def _toggle(source, name, enabled):
    try:
        ok = await _state["mods"].set_enabled(name, enabled)
    except ModError as exc:
        await source.reply(str(exc))
        return
    if not ok:
        await source.reply(source.server.tr("remove.not_installed", name=name))
        return
    await source.reply(source.server.tr(
        "toggle.enabled" if enabled else "toggle.disabled", name=name))
    await _warn_restart(source)


async def _cmd_updates(source):
    await source.reply(source.server.tr("updates.checking"))
    try:
        updates = await _state["mods"].check_updates(_target_version())
    except PortalError as exc:
        await source.reply(source.server.tr("updates.failed", error=exc))
        return
    if not updates:
        await source.reply(source.server.tr("updates.none"))
        return
    await source.reply(source.server.tr("updates.header", count=len(updates)))
    for mod, release in updates:
        await source.reply(source.server.tr(
            "updates.entry", name=mod.name, current=mod.version, latest=release.version))
    await source.reply(source.server.tr("updates.hint"))


async def _cmd_refresh(source):
    await source.reply(source.server.tr("refresh.starting"))
    try:
        index = await _state["portal"].get_index(force=True)
    except PortalError as exc:
        await source.reply(source.server.tr("refresh.failed", error=exc))
        return
    await source.reply(source.server.tr("refresh.done", count=len(index)))


async def _warn_restart(source):
    await source.reply(source.server.tr("restart_needed"))
    if source.server.is_server_running():
        # Be explicit rather than let it look like nothing happened: the file on
        # disk will be overwritten by the running server and put back by us.
        await source.reply(source.server.tr("restart_note"))


# ---------------------------------------------------------------------------
# Telegram side -- registered through the bridge's service, no telegram imports
# ---------------------------------------------------------------------------

def _register_telegram(server):
    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is None:
        server.logger.debug("telegram_bridge is not loaded; skipping Telegram commands")
        return
    try:
        bridge.register_command(
            "mod_manager", "mods", _tg_list, level="viewer", help="installed mods"
        )
        bridge.register_command(
            "mod_manager", "modsearch", _tg_search, level="viewer", help="search the portal"
        )
        bridge.register_command(
            "mod_manager", "modinfo", _tg_info, level="viewer", help="details for one mod"
        )
        bridge.register_command(
            "mod_manager", "modinstall", _tg_install, level="admin", help="install a mod"
        )
        bridge.register_command(
            "mod_manager", "modremove", _tg_remove, level="admin", help="remove a mod"
        )
        bridge.register_command(
            "mod_manager", "modupdates", _tg_updates, level="admin", help="check for updates"
        )
    except RuntimeError as exc:
        server.logger.debug("Could not register Telegram commands: %s", exc)


async def _tg_list(ctx):
    mods = [mod for mod in _state["mods"].list_installed() if not mod.builtin]
    if not mods:
        await ctx.reply("No mods installed beyond the base game.")
        return
    await ctx.reply(f"{len(mods)} mod(s):\n" + "\n".join(f"  {m.describe()}" for m in mods))


async def _tg_search(ctx):
    if not ctx.text:
        await ctx.reply("Usage: /modsearch <query>")
        return
    await ctx.reply(f"Searching for {ctx.text!r}...")
    try:
        results = await _state["portal"].search(
            ctx.text,
            limit=_state["config"].get("search_results", 8),
            factorio_version=_target_version(),
        )
    except PortalError as exc:
        await ctx.reply(f"Search failed: {exc}")
        return
    if not results:
        await ctx.reply("Nothing matched.")
        return
    await ctx.reply(
        "\n".join(f"{m.describe()}\n  /modinstall {m.name}" for m in results)
    )


async def _tg_info(ctx):
    if not ctx.args:
        await ctx.reply("Usage: /modinfo <name>")
        return
    name = ctx.args[0]
    try:
        data = await _state["portal"].get_mod(name, full=True)
        releases = await _state["portal"].get_releases(name)
    except PortalError as exc:
        await ctx.reply(str(exc))
        return

    lines = [
        f"{data.get('title', name)} ({name})",
        f"by {data.get('owner', '?')} - {data.get('downloads_count', 0):,} downloads",
        data.get("summary", ""),
    ]
    if releases:
        latest = releases[-1]
        lines.append(f"Latest: v{latest.version} for Factorio {latest.factorio_version}")
        required = latest.required_dependencies()
        if required:
            lines.append("Requires: " + ", ".join(n for n, _ in required))
    await ctx.reply("\n".join(line for line in lines if line))


async def _tg_install(ctx):
    if not ctx.args:
        await ctx.reply("Usage: /modinstall <name> [version]")
        return
    name = ctx.args[0]
    version = ctx.args[1] if len(ctx.args) > 1 else None

    try:
        release = await (
            _state["portal"].get_release(name, version) if version
            else _state["portal"].get_release(name)
        )
    except PortalError as exc:
        await ctx.reply(str(exc))
        return

    required = release.required_dependencies()
    question = (
        f"Install {name} v{release.version}?\n"
        + (f"Also pulls in: {', '.join(n for n, _ in required)}\n" if required else "")
        + "\nThe server must restart, and every player will need this mod to reconnect."
    )
    if not await ctx.confirm(question):
        await ctx.reply("Cancelled.")
        return

    await ctx.reply("Installing...")
    try:
        installed = await _state["mods"].install(
            name,
            version=version,
            factorio_version=_target_version(),
            with_dependencies=_state["config"].get("install_dependencies", True),
        )
    except (AuthRequired, PortalError, ModError) as exc:
        await ctx.reply(f"Install failed: {exc}")
        return

    await ctx.reply("Installed:\n" + "\n".join(f"  {m.describe()}" for m in installed))

    if _state["config"].get("prompt_restart", True):
        if await ctx.confirm("Restart the server now to apply it?"):
            await ctx.reply("Restarting...")
            await _state["server"].restart()
            await ctx.reply("Restart issued.")
        else:
            await ctx.reply("Not restarting; the mod is inactive until you do.")


async def _tg_remove(ctx):
    if not ctx.args:
        await ctx.reply("Usage: /modremove <name>")
        return
    name = ctx.args[0]
    if not await ctx.confirm(f"Remove {name}? The server must restart to apply it."):
        await ctx.reply("Cancelled.")
        return
    try:
        removed = await _state["mods"].remove(name)
    except ModError as exc:
        await ctx.reply(str(exc))
        return
    await ctx.reply(f"Removed {name}." if removed else f"{name} is not installed.")


async def _tg_updates(ctx):
    await ctx.reply("Checking for updates...")
    try:
        updates = await _state["mods"].check_updates(_target_version())
    except PortalError as exc:
        await ctx.reply(f"Update check failed: {exc}")
        return
    if not updates:
        await ctx.reply("Everything is up to date.")
        return
    await ctx.reply(
        "\n".join(
            f"{mod.name}: v{mod.version} -> v{release.version}\n  /modinstall {mod.name}"
            for mod, release in updates
        )
    )
