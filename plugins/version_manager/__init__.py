"""Install, switch and roll back the Factorio server build itself.

The thing to understand before reading any of this: **a save format upgrade is
a one-way door.** Once 2.0.78 has loaded the world and written it back, 2.0.77
can never open it again -- the binary says so itself, reporting a *map output
version* it will not read past. So going up is ordinary and going down is not a
version change at all, it is a version change plus a restore, and the two have
to happen together or the server comes up on a world it cannot read.

That shapes everything here:

* Downloading is separate from switching. ``install`` needs no downtime and
  takes minutes; ``use`` needs the server down and takes seconds. Fused into
  one command, a failed download would leave the server both stopped and
  broken.
* ``use`` stages and ``confirm`` acts, exactly like ``!!qb back``.
* Before the server stops, the world's version is read off the save and checked
  against what the target binary admits to being able to open. Finding out by
  trying costs a stop, a failed start and a restore.
* The world from before the swap goes into its own fixed backup slot, which
  nothing rotates. It is the only world an older binary can still open.

See :mod:`factorio_reforge.versions` for the measurements this rests on.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from factorio_reforge.command.builder import CommandContext, Literal, Text
from factorio_reforge.core.progress import Progress
from factorio_reforge.permission import PermissionLevel
from factorio_reforge.saves.manager import PREUPGRADE_SLOT, SaveError, parse_slot
from factorio_reforge.versions import (
    Installation,
    VersionError,
    fetch_latest_releases,
    install_version,
    read_binary,
    read_save_version,
)
from factorio_reforge.versions.compat import blockers, check_switch, read_mod_series
from factorio_reforge.versions.download import remove_download_leftovers
from factorio_reforge.versions.savefile import MapVersion

PLUGIN_METADATA = {
    "id": "version_manager",
    "version": "1.0.0",
    "name": "Version Manager",
    "description": "Install, switch and roll back the Factorio server build",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    #: Blank means beside the live install: <working_directory>/../versions.
    "versions_directory": "",
    "build": "headless",
    "distro": "linux64",
    #: Seconds of in-game warning before the server stops for a swap.
    "countdown_seconds": 15,
    #: How long a staged switch stays confirmable.
    "confirm_window_seconds": 120,
}

#: Fired after a successful switch so plugins holding a version -- mod_manager
#: caches one to pick releases with -- can re-read it without a restart.
VERSION_CHANGED = "version.changed"

_state: dict = {}


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    installation = _installation(server, config)

    _state.clear()
    _state.update(
        server=server,
        config=config,
        installation=installation,
        staged=None,
        busy=False,
    )
    remove_download_leftovers(installation)

    prefix = server.config.command_prefix + "version"
    server.register_command(
        Literal(prefix)
        .requires(PermissionLevel.USER)
        .runs(_cmd_status)
        .then(Literal("list").runs(_cmd_list))
        .then(Literal("check").requires(PermissionLevel.HELPER).runs(_cmd_check))
        .then(
            Literal("install")
            .requires(PermissionLevel.ADMIN)
            .then(Text("version").runs(_cmd_install))
        )
        .then(
            Literal("use")
            .requires(PermissionLevel.OWNER)
            .then(
                Text("version")
                .runs(_cmd_use)
                .then(Literal("with-save").then(Text("slot").runs(_cmd_use)))
            )
        )
        .then(Literal("confirm").requires(PermissionLevel.OWNER).runs(_cmd_confirm))
        .then(Literal("abort").requires(PermissionLevel.OWNER).runs(_cmd_abort))
        .then(Literal("adopt").requires(PermissionLevel.OWNER).runs(_cmd_adopt))
        .then(
            Literal("remove")
            .requires(PermissionLevel.OWNER)
            .then(Text("version").runs(_cmd_remove))
        )
    )
    server.register_help_message(
        prefix,
        server.tr("help"),
        PermissionLevel.USER,
        detail=[
            server.tr("help.status", prefix=prefix),
            server.tr("help.check", prefix=prefix),
            server.tr("help.install", prefix=prefix),
            server.tr("help.use", prefix=prefix),
            server.tr("help.with_save", prefix=prefix),
            server.tr("help.adopt", prefix=prefix),
        ],
    )


async def on_unload(server):
    _state.clear()


def _installation(server, config) -> Installation:
    configured = (config.get("versions_directory") or "").strip()
    versions = Path(configured).expanduser() if configured else None
    if versions is not None and not versions.is_absolute():
        versions = server.get_data_folder().parent.parent / versions
    return Installation(server.config.working_dir_path, versions)


# ---------------------------------------------------------------------------
# reading the world
# ---------------------------------------------------------------------------

def _current_save_version(server) -> tuple[MapVersion | None, str]:
    try:
        return read_save_version(server.config.current_save_path), ""
    except VersionError as exc:
        return None, str(exc)


def _slot_save_version(server, slot) -> tuple[MapVersion | None, str]:
    try:
        return read_save_version(server.saves.save_path(parse_slot(slot))), ""
    except (VersionError, SaveError) as exc:
        return None, str(exc)


def _mods_directory(server) -> Path:
    return server.config.working_dir_path / "mods"


async def _online(server) -> list[str]:
    if not server.is_rcon_running():
        return []
    try:
        return await server.get_online_players()
    except Exception:
        # Not knowing who is online must not block a swap; it downgrades one
        # warning, not a safety check.
        return []


# ---------------------------------------------------------------------------
# commands -- reading
# ---------------------------------------------------------------------------

async def _cmd_status(source):
    server = _state["server"]
    tr = server.tr
    installation: Installation = _state["installation"]

    try:
        binary = await read_binary(installation.active_binary)
        await source.reply(tr("status.running", version=binary.describe()))
        await source.reply(tr("status.window", min=str(binary.map_input),
                              max=str(binary.map_output)))
    except VersionError as exc:
        await source.reply(tr("status.no_binary", error=exc))
        binary = None

    save, error = _current_save_version(server)
    if save is not None:
        await source.reply(tr("status.world", version=str(save)))
    else:
        await source.reply(tr("status.world_unknown", error=error))

    if installation.is_managed:
        await source.reply(tr("status.managed",
                              version=installation.active_version,
                              count=len(installation.installed())))
    else:
        await source.reply(tr("status.unmanaged", path=installation.active_path))
        await source.reply(tr("status.adopt_hint",
                              prefix=server.config.command_prefix + "version"))

    preupgrade = server.saves.get_preupgrade()
    if preupgrade is not None:
        await source.reply(tr("status.preupgrade", time=preupgrade.created_at_text,
                              comment=preupgrade.comment))

    staged = _state.get("staged")
    if staged:
        await source.reply(tr("status.staged", version=staged["version"]))


async def _cmd_list(source):
    server = _state["server"]
    tr = server.tr
    installation: Installation = _state["installation"]

    installed = installation.installed()
    if not installed:
        await source.reply(tr("list.none", path=installation.versions_dir))
        return
    active = installation.active_version
    await source.reply(tr("list.header", count=len(installed)))
    for version in installed:
        mark = tr("list.active") if version == active else ""
        await source.reply(f"  {version}{mark}")


async def _cmd_check(source):
    server = _state["server"]
    tr = server.tr
    build = _state["config"].get("build", "headless")

    await source.reply(tr("check.asking"))
    try:
        releases = await fetch_latest_releases()
    except VersionError as exc:
        await source.reply(tr("check.failed", error=exc))
        return

    installation: Installation = _state["installation"]
    installed = set(installation.installed())
    current = installation.active_version or ""
    prefix = server.config.command_prefix + "version"

    for channel in ("stable", "experimental"):
        version = (releases.get(channel) or {}).get(build)
        if not version:
            continue
        if version == current:
            await source.reply(tr("check.current", channel=channel, version=version))
        elif version in installed:
            await source.reply(tr("check.installed", channel=channel, version=version,
                                  prefix=prefix))
        else:
            await source.reply(tr("check.available", channel=channel, version=version,
                                  prefix=prefix))


# ---------------------------------------------------------------------------
# commands -- installing
# ---------------------------------------------------------------------------

async def _cmd_install(source, ctx: CommandContext):
    server = _state["server"]
    tr = server.tr
    config = _state["config"]
    installation: Installation = _state["installation"]
    version = ctx["version"].strip()

    if _state.get("busy"):
        await source.reply(tr("busy"))
        return

    # The download callback runs on a worker thread, so replies have to be
    # handed back to the loop rather than awaited where they happen.
    loop = asyncio.get_running_loop()
    bar = Progress(
        lambda line: loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(source.reply(line))
        ),
        unit=tr("install.unit"),
    )

    def on_progress(received: int, total: int) -> None:
        bar.update(received // (1024 * 1024), (total // (1024 * 1024)) if total else None)

    _state["busy"] = True
    await source.reply(tr("install.starting", version=version))
    try:
        target = await install_version(
            installation, version,
            build=config.get("build", "headless"),
            distro=config.get("distro", "linux64"),
            on_progress=on_progress,
        )
    except VersionError as exc:
        await source.reply(tr("install.failed", version=version, error=exc))
        return
    finally:
        _state["busy"] = False

    await source.reply(tr("install.done", version=version, path=target))
    await source.reply(tr("install.next", prefix=server.config.command_prefix + "version",
                          version=version))


# ---------------------------------------------------------------------------
# commands -- switching
# ---------------------------------------------------------------------------

async def _cmd_use(source, ctx: CommandContext):
    server = _state["server"]
    tr = server.tr
    installation: Installation = _state["installation"]
    version = ctx["version"].strip()
    slot = (ctx.get("slot") or "").strip()

    if not installation.is_managed:
        await source.reply(tr("switch.unmanaged"))
        await source.reply(tr("status.adopt_hint",
                              prefix=server.config.command_prefix + "version"))
        return
    if not installation.is_installed(version):
        await source.reply(tr("switch.not_installed", version=version,
                              prefix=server.config.command_prefix + "version"))
        return

    findings = await _preflight(server, version, slot)
    if findings is None:
        return
    await _report(source, findings)

    if blockers(findings):
        _state["staged"] = None
        return

    _state["staged"] = {
        "version": version, "slot": slot, "at": time.monotonic(), "by": str(source),
    }
    await source.reply(tr("switch.staged", version=version))
    await source.reply(tr("switch.confirm_hint",
                          prefix=server.config.command_prefix + "version",
                          seconds=int(_state["config"].get("confirm_window_seconds", 120))))


async def _preflight(server, version: str, slot: str):
    """Gather the facts and judge them. ``None`` if the target itself is broken."""
    installation: Installation = _state["installation"]
    try:
        target = await read_binary(installation.binary_of(version))
    except VersionError:
        return None

    save, save_error = _current_save_version(server)
    paired, paired_error = (None, "")
    if slot:
        paired, paired_error = _slot_save_version(server, slot)

    return check_switch(
        target=target,
        current_release=installation.active_version,
        save=save,
        save_error=save_error,
        mods=read_mod_series(_mods_directory(server)),
        online=await _online(server),
        paired_save=paired,
        paired_slot=slot,
        paired_error=paired_error,
    )


async def _report(source, findings) -> None:
    tr = _state["server"].tr
    marks = {"block": tr("mark.block"), "warn": tr("mark.warn"), "note": tr("mark.note")}
    for finding in findings:
        message = tr(f"check.{finding.key}", **finding.values)
        await source.reply(f"{marks[finding.severity]} {message}")


async def _cmd_abort(source):
    tr = _state["server"].tr
    if _state.get("staged"):
        _state["staged"] = None
        await source.reply(tr("switch.cancelled"))
        return
    await source.reply(tr("common.nothing_to_cancel"))


async def _cmd_confirm(source):
    server = _state["server"]
    tr = server.tr
    config = _state["config"]
    staged = _state.get("staged")

    if not staged:
        await source.reply(tr("switch.nothing_staged",
                              prefix=server.config.command_prefix + "version"))
        return
    window = float(config.get("confirm_window_seconds", 120))
    if time.monotonic() - staged["at"] > window:
        _state["staged"] = None
        await source.reply(tr("switch.expired"))
        return
    if _state.get("busy"):
        await source.reply(tr("busy"))
        return
    _state["staged"] = None

    # Re-run the checks: minutes may have passed, and the world has been
    # ticking and autosaving the whole time.
    findings = await _preflight(server, staged["version"], staged["slot"])
    if findings is None or blockers(findings):
        await source.reply(tr("switch.recheck_failed"))
        if findings:
            await _report(source, blockers(findings))
        return

    _state["busy"] = True
    try:
        await _switch(source, staged["version"], staged["slot"])
    finally:
        _state["busy"] = False


async def _switch(source, version: str, slot: str) -> None:
    server = _state["server"]
    tr = server.tr
    installation: Installation = _state["installation"]
    previous = installation.active_version
    countdown = float(_state["config"].get("countdown_seconds", 15))
    who = str(getattr(source, "player", None) or "console")

    was_running = server.is_server_running()
    if was_running and countdown > 0:
        for remaining in range(int(countdown), 0, -1):
            if remaining % 5 == 0 or remaining <= 3:
                await server.say(tr("switch.countdown", version=version, seconds=remaining))
            await asyncio.sleep(1)

    if was_running:
        await source.reply(tr("switch.stopping"))
        if not await server.stop():
            await source.reply(tr("switch.would_not_stop"))
            return

    await source.reply(tr("switch.preserving"))
    preserved = server.saves.back_up_current_world(
        who,
        slot=PREUPGRADE_SLOT,
        comment=tr("switch.preserved_comment", version=previous or "?", target=version),
    )
    if preserved is None and server.config.current_save_path.is_file():
        await source.reply(tr("switch.preserve_failed"))
        if was_running:
            await server.start()
        return

    try:
        if slot:
            await server.saves.restore(parse_slot(slot))
            await source.reply(tr("switch.world_restored", slot=slot))
        installation.activate(version)
    except (VersionError, SaveError) as exc:
        await source.reply(tr("switch.failed", error=exc))
        await _recover(source, previous, restore_world=bool(slot))
        if was_running:
            await server.start()
        return

    await source.reply(tr("switch.switched", old=previous or "?", new=version))

    if not was_running:
        await source.reply(tr("switch.start_when_ready"))
        await _finish(server, previous, version)
        return

    if not await server.start():
        await source.reply(tr("switch.did_not_come_up", version=version))
        await _recover(source, previous, restore_world=True)
        if await server.start():
            await source.reply(tr("switch.put_back", version=previous or "?"))
        else:
            await source.reply(tr("switch.put_back_failed", version=previous or "?"))
        return

    await source.reply(tr("switch.done", version=version))
    await _finish(server, previous, version)


async def _recover(source, previous: str | None, *, restore_world: bool) -> None:
    """Undo as much of a failed swap as there is to undo.

    The symlink goes back first because it always can; the world is only put
    back if something replaced it, and a failure here is reported rather than
    hidden -- there is a world in the ``pre-upgrade`` slot either way, and the
    operator needs to know they have to place it themselves.
    """
    server = _state["server"]
    tr = server.tr
    installation: Installation = _state["installation"]

    if previous:
        try:
            installation.activate(previous)
        except VersionError as exc:
            await source.reply(tr("switch.relink_failed", version=previous, error=exc))

    if not restore_world:
        return
    try:
        await server.saves.restore(PREUPGRADE_SLOT)
    except (SaveError, VersionError) as exc:
        await source.reply(tr("switch.world_recover_failed",
                              slot=PREUPGRADE_SLOT, error=exc))


async def _finish(server, previous: str | None, version: str) -> None:
    server.logger.info(server.tr("log.switched", old=previous or "?", new=version))
    await server.dispatch_event(VERSION_CHANGED, version)


# ---------------------------------------------------------------------------
# commands -- layout
# ---------------------------------------------------------------------------

async def _cmd_adopt(source):
    server = _state["server"]
    tr = server.tr
    installation: Installation = _state["installation"]

    if installation.is_managed:
        await source.reply(tr("adopt.already", version=installation.active_version))
        return
    if server.is_server_running():
        await source.reply(tr("adopt.server_running"))
        return

    try:
        binary = await read_binary(installation.active_binary)
    except VersionError as exc:
        await source.reply(tr("adopt.no_binary", error=exc))
        return

    version = binary.release
    await source.reply(tr("adopt.starting", version=version,
                          path=installation.version_dir(version)))
    try:
        await asyncio.to_thread(installation.adopt, version)
    except VersionError as exc:
        await source.reply(tr("adopt.failed", error=exc))
        return

    await source.reply(tr("adopt.done", version=version, shared=installation.shared_dir))
    await source.reply(tr("adopt.next", prefix=server.config.command_prefix + "version"))


async def _cmd_remove(source, ctx: CommandContext):
    server = _state["server"]
    tr = server.tr
    installation: Installation = _state["installation"]
    version = ctx["version"].strip()

    try:
        await asyncio.to_thread(installation.remove, version)
    except VersionError as exc:
        await source.reply(tr("remove.failed", version=version, error=exc))
        return
    await source.reply(tr("remove.done", version=version))
