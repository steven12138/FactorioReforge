"""ReforgeServer -- assembles everything and owns the rollback orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sys
import time
from pathlib import Path

from factorio_reforge.command.manager import CommandManager
from factorio_reforge.config import Config
from factorio_reforge.core.handler import FactorioHandler
from factorio_reforge.core.info import Info
from factorio_reforge.core.loglens import LogLens, Severity
from factorio_reforge.core.process import ServerProcess
from factorio_reforge.core.rcon import RconClient, RconError, RconManager
from factorio_reforge.core.reactor import InfoReactor
from factorio_reforge.core.terminal import ColourFormatter, Palette, supports_colour
from factorio_reforge.i18n import LANG_DIR_NAME, Translator
from factorio_reforge.permission import PermissionManager
from factorio_reforge.plugin import events as ev
from factorio_reforge.plugin.manager import PluginManager
from factorio_reforge.saves.manager import (
    OVERWRITE_SLOT,
    SaveError,
    SaveManager,
    Slot,
    SlotConfig,
)


class RollbackError(Exception):
    pass


class ReforgeServer:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("reforge")
        #: Factorio's own output. A separate logger so the source column tells
        #: you at a glance whether a line came from the game or from us.
        self.server_logger = logging.getLogger("factorio")
        #: Reads Factorio's startup output and reports on it afterwards.
        self.loglens = LogLens()

        self.i18n = Translator(config.language)
        self.i18n.load_directory(Path(__file__).resolve().parent.parent / LANG_DIR_NAME)
        self.i18n.set_language(config.language)

        self.handler = FactorioHandler()
        self.process = ServerProcess(
            config.command_argv,
            config.working_dir_path,
            self._on_server_line,
            logger=self.logger,
            tr=self.tr,
            quit_timeout=config.quit_timeout,
            sigint_timeout=config.sigint_timeout,
            sigterm_timeout=config.sigterm_timeout,
            encoding=config.encoding,
        )
        self.permissions = PermissionManager(
            config.resolve("config") / "permission.yml", config.default_permission_level
        )
        self.commands = CommandManager(
            config.command_prefix, self.logger, interface_for=self._plugin_interface
        )
        self.saves = SaveManager(
            config.current_save_path,
            config.snapshot_dir_path,
            slots=[SlotConfig(seconds) for seconds in config.saves.slot_protection],
            logger=self.logger,
            tr=self.tr,
        )

        from factorio_reforge.plugin.interface import ServerInterface

        self.interface = ServerInterface(self)
        self.plugins = PluginManager(self, config.plugin_dir_paths, self.logger)
        self.reactor = InfoReactor(self, self.logger)

        self.rcon: RconManager | None = None
        if config.rcon.enabled:
            self.rcon = RconManager(
                RconClient(
                    config.rcon.host, config.rcon.port, config.rcon.password,
                    connect_timeout=config.rcon.connect_timeout, logger=self.logger,
                    tr=self.tr,
                ),
                retry_interval=config.rcon.retry_interval,
                logger=self.logger,
                tr=self.tr,
                on_connect=lambda: self.plugins.dispatch(ev.RCON_CONNECTED),
                on_lost=lambda: self.plugins.dispatch(ev.RCON_LOST),
            )

        self._save_completed = asyncio.Event()
        #: Set when shutdown *begins*; guards against re-entry.
        self._exiting = asyncio.Event()
        #: Set when shutdown has *finished*; what wait_for_exit waits on.
        self._exited = asyncio.Event()
        self._rollback_in_progress = False
        self._abort_rollback = asyncio.Event()
        self._expect_stop = False
        self._crash_watch: asyncio.Task | None = None
        self._startup_report: asyncio.Task | None = None
        self.started_at = time.monotonic()

    def tr(self, key: str, /, *args, **kwargs) -> str:
        """Translate a core string."""
        return self.i18n.translate(key, *args, **kwargs)

    def _plugin_interface(self, plugin_id: str):
        """Look up a plugin's own ServerInterface by id."""
        plugin = self.plugins.get(plugin_id)
        return plugin.interface if plugin else None

    # -- console output ------------------------------------------------------

    def echo(self, line: str, info: Info | None = None) -> None:
        """Emit a line of Factorio output through the same logger we use.

        Previously this was a bare ``print``, which gave the console two
        unrelated formats side by side and -- worse -- kept Factorio's output
        out of ``logs/reforge.log`` entirely, so a post-mortem had nothing to
        read. Routing it through a ``factorio`` logger unifies the layout, puts
        the source in the same column, and gets the server's own words into the
        log file.
        """
        level = logging.INFO
        if info is not None:
            if info.level == "Error":
                level = logging.ERROR
            elif info.level == "Warning":
                level = logging.WARNING
        # The text is Factorio's, verbatim. Anything we noticed about it is
        # reported separately once startup finishes -- see LogLens.
        self.server_logger.log(level, line, extra={"server_info": info})

    # -- wiring --------------------------------------------------------------

    async def _on_server_line(self, raw: str) -> None:
        info = self.handler.parse_server_stdout(raw)
        await self.reactor.react(info)

    async def feed_console(self, text: str) -> None:
        """Entry point for the operator's terminal."""
        await self.reactor.react(self.handler.parse_console_input(text))

    async def feed_info(self, info: Info) -> None:
        """Entry point for plugin-injected lines, e.g. a Telegram message."""
        await self.reactor.react(info)

    def on_rcon_port_open(self) -> None:
        if self.rcon is not None:
            self.rcon.start()

    def on_save_completed(self) -> None:
        self._save_completed.set()

    # -- lifecycle -----------------------------------------------------------

    async def boot(self) -> None:
        self.permissions.load()
        self.saves.load_index()
        loaded, failed = await self.plugins.load_all()
        self.logger.info(
            self.tr("log.plugins_loaded_with_failures", count=len(loaded),
                    failed=len(failed), names=", ".join(failed))
            if failed else self.tr("log.plugins_loaded", count=len(loaded))
        )
        await self.plugins.dispatch(ev.REFORGE_START)

    def schedule_startup_report(self, delay: float = 2.0) -> None:
        """Report shortly after startup, not exactly at it.

        Several lines worth reporting -- the RCON bind above all -- are printed
        *after* the ``to(InGame)`` marker, so reporting on the marker itself
        misses them. A short wait costs nothing and catches the tail.
        """
        if self._startup_report is not None:
            self._startup_report.cancel()
        self._startup_report = asyncio.create_task(self._report_after(delay))

    async def _report_after(self, delay: float) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(delay)
            self.report_startup()

    def report_startup(self) -> None:
        """Print what the startup output revealed, after the fact.

        Separate from Factorio's own lines on purpose: those stay verbatim so
        they still match the game's log and anything posted on the forums.
        """
        summary = self.loglens.summary(self.tr)
        if summary is None:
            return
        self.logger.info(summary)
        for severity, line in self.loglens.report(self.tr):
            if severity is Severity.PROBLEM:
                self.logger.error("  %s", line)
            elif severity is Severity.NOTICE:
                self.logger.info("  %s", line)
            else:
                self.logger.info("  %s", line)

    async def start_server(self) -> bool:
        if self.process.is_running:
            self.logger.warning(self.tr("log.server_already_running"))
            return False
        await self.plugins.dispatch(ev.SERVER_START_PRE)
        self.loglens.reset()
        self._expect_stop = False
        try:
            await self.process.start()
        except Exception as exc:
            self.logger.error(self.tr("log.server_start_failed", error=exc))
            return False
        await self.plugins.dispatch(ev.SERVER_START)
        self._crash_watch = asyncio.create_task(self._watch_for_crash())
        return True

    async def stop_server(self) -> bool:
        if not self.process.is_running:
            return True
        self._expect_stop = True
        await self.plugins.dispatch(ev.SERVER_STOP_PRE)
        stopped = await self.process.stop()
        code = self.process.return_code
        await self.process.cleanup()
        if self.rcon is not None:
            await self.rcon.stop()
        # Only now are the files Factorio held actually released.
        await self.plugins.dispatch(ev.SERVER_STOP, code)
        return stopped

    async def restart_server(self) -> bool:
        await self.stop_server()
        return await self.start_server()

    async def _watch_for_crash(self) -> None:
        """Notice the server dying on its own, and optionally bring it back."""
        await self.process.wait_until_stopped()
        if self._expect_stop or self._exiting.is_set() or self._rollback_in_progress:
            return
        code = self.process.return_code
        self.logger.error(self.tr("log.server_crashed", code=code))
        await self.plugins.dispatch(ev.SERVER_CRASH, code)
        await self.process.cleanup()
        # A crash still frees the files, so listeners that clean up after the
        # server must hear about it the same way a clean stop is reported.
        await self.plugins.dispatch(ev.SERVER_STOP, code)
        if self.config.auto_restart_on_crash and not self._exiting.is_set():
            self.logger.info(self.tr("log.restarting_in",
                                     seconds=int(self.config.crash_restart_delay)))
            await asyncio.sleep(self.config.crash_restart_delay)
            if not self._exiting.is_set():
                await self.start_server()

    async def shutdown(self, *, stop_server: bool = True) -> None:
        """Stop the server and tear FactorioReforge down.

        A second caller waits for the first to finish rather than returning
        straight away: two Ctrl-C presses should not let the program exit while
        the first shutdown is still saving the map.
        """
        if self._exiting.is_set():
            await self._exited.wait()
            return
        self._exiting.set()
        self.logger.info(self.tr("log.shutting_down"))
        try:
            for task in (self._crash_watch, self._startup_report):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if stop_server:
                await self.stop_server()
            await self.plugins.dispatch(ev.REFORGE_STOP)
            await self.plugins.unload_all()
            if self.rcon is not None:
                await self.rcon.stop()
        finally:
            # Only now is it safe for main() to return. Setting this at the top
            # -- as _exiting is -- let asyncio.run() cancel the shutdown while
            # it was still stopping the server, so Ctrl-C left Factorio running.
            self._exited.set()

    async def wait_for_exit(self) -> None:
        """Block until a shutdown has run to completion."""
        await self._exited.wait()

    @property
    def is_exiting(self) -> bool:
        return self._exiting.is_set()

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.started_at

    # -- saves ---------------------------------------------------------------

    async def flush_save(self) -> bool:
        """``/server-save`` with no name: overwrites the live save. False on timeout."""
        return await self._server_save(None) is not None

    async def write_backup_save(self, target: Path) -> None:
        """Have the running server write a complete save at ``target``.

        ``/server-save <name>`` writes a **separate** file and leaves the live
        save alone -- measured on 2.0.77 -- so a backup no longer has to
        overwrite the world it is backing up and then copy it. The name goes
        through Factorio's console, so it is restricted to characters that
        cannot be misread as arguments.
        """
        stem = f"reforge-backup-{int(time.time())}"
        produced = await self._server_save(stem)
        if produced is None:
            raise SaveError("the server did not confirm the save in time")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same filesystem, so this is a rename rather than a second full write.
        shutil.move(str(produced), str(target))

    async def _server_save(self, stem: str | None) -> Path | None:
        """Run ``/server-save [name]`` and wait for the completion marker."""
        if not self.process.is_running:
            return None
        self._save_completed.clear()
        await self.process.write(f"/server-save {stem}" if stem else "/server-save")
        try:
            await asyncio.wait_for(self._save_completed.wait(), self.config.saves.save_timeout)
        except TimeoutError:
            self.logger.warning(self.tr("log.save_timeout",
                                        seconds=int(self.config.saves.save_timeout)))
            return None
        return self.config.save_dir_path / (f"{stem}.zip" if stem else self.current_save_name)

    @property
    def current_save_name(self) -> str:
        return self.config.current_save_path.name

    async def create_snapshot(
        self, comment: str = "", *, created_by: str = "unknown", automatic: bool = False
    ) -> Slot:
        players: list[str] = []
        if self.process.is_running:
            with contextlib.suppress(RconError):
                players = await self.interface.get_online_players()

        slot = await self.saves.create(
            comment,
            created_by=created_by,
            players_online=players,
            automatic=automatic,
            write_save=self.write_backup_save if self.process.is_running else None,
        )
        await self.plugins.dispatch(ev.SNAPSHOT_CREATED, slot)
        return slot

    def abort_rollback(self) -> bool:
        """Cancel a countdown that is already running. QBM's ``!!qb abort``."""
        if not self._rollback_in_progress:
            return False
        self._abort_rollback.set()
        return True

    async def rollback(
        self, slot: int, *, countdown: float = 10.0, requested_by: str = "unknown"
    ) -> Slot:
        """Restore a slot, in QBM's order: countdown, stop, preserve, swap, start.

        Preserving the current world *after* the server has stopped, and before
        anything is overwritten, is what makes restoring the wrong slot
        survivable. If that step fails there is no way back, so the restore is
        refused rather than attempted.
        """
        if self._rollback_in_progress:
            raise RollbackError("a restore is already running")

        info = self.saves.validate(slot)

        self._rollback_in_progress = True
        self._abort_rollback.clear()
        was_running = self.process.is_running
        preserved: Slot | None = None
        try:
            await self.plugins.dispatch(ev.ROLLBACK_STARTED, info, requested_by)

            if was_running and countdown > 0:
                if not await self._announce_countdown(info, countdown):
                    raise RollbackError("cancelled during the countdown")

            if was_running:
                self._expect_stop = True
                if not await self.process.stop():
                    raise RollbackError("the server would not stop; restore aborted")
                await self.process.cleanup()
                if self.rcon is not None:
                    await self.rcon.stop()

            # QBM's "backup current world to avoid idiot", with the server down
            # so the file is not moving underneath us.
            self.logger.info(self.tr("log.restore_preserving"))
            preserved = self.saves.back_up_current_world(requested_by)
            if preserved is None and self.config.current_save_path.is_file():
                raise RollbackError(
                    "could not preserve the current world, so the restore was refused"
                )

            try:
                await self.saves.restore(slot)
            except SaveError as exc:
                raise RollbackError(f"restoring slot {slot} failed: {exc}") from exc

            if was_running:
                if not await self.start_server():
                    await self._recover(preserved)
                    raise RollbackError(
                        "the server did not come back up; the previous world was put back"
                    )

            self.logger.info(self.tr("log.restore_done", slot=info.describe()))
            await self.plugins.dispatch(ev.ROLLBACK_FINISHED, info, True)
            return info
        except Exception:
            await self.plugins.dispatch(ev.ROLLBACK_FINISHED, info, False)
            raise
        finally:
            self._rollback_in_progress = False

    async def _announce_countdown(self, info: Slot, seconds: float) -> bool:
        """Count down in chat, one second at a time. False if aborted.

        Per-second rather than in chunks because that is the window in which
        someone realises they picked the wrong slot.
        """
        for remaining in range(int(seconds), 0, -1):
            if self._abort_rollback.is_set():
                self.logger.info(self.tr("log.restore_countdown_cancelled"))
                with contextlib.suppress(RuntimeError):
                    await self.process.write(self.tr("save.countdown_cancelled"))
                return False
            with contextlib.suppress(RuntimeError):
                await self.process.write(self.tr(
                    "save.countdown", slot=info.id, time=info.created_at_text,
                    seconds=remaining, prefix=self.config.command_prefix + "save",
                ))
            try:
                await asyncio.wait_for(self._abort_rollback.wait(), timeout=1.0)
            except TimeoutError:
                continue
        return not self._abort_rollback.is_set()

    async def _recover(self, preserved: Slot | None) -> None:
        if preserved is None:
            self.logger.error("Nothing was preserved, so there is nothing to put back")
            return
        self.logger.warning(self.tr("log.restore_recovering"))
        try:
            await self.saves.restore(OVERWRITE_SLOT)
            await self.start_server()
        except Exception:
            self.logger.exception(
                "Recovery failed. The pre-restore world is intact at %s -- restore it by hand",
                self.saves.save_path(OVERWRITE_SLOT),
            )


def build_logger(config: Config) -> logging.Logger:
    """One console handler and one file handler, shared by every source.

    "reforge", "factorio" and every "plugin.<id>" go through the same pair, so
    the console shows one format and the log file contains everything -- the
    server's own output included, which a bare print kept out of it.
    """
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    palette = Palette(supports_colour(sys.stdout) and config.colour != "never")
    if config.colour == "always":
        palette.enabled = True

    stream = logging.StreamHandler()
    stream.setFormatter(ColourFormatter(palette))

    log_dir: Path = config.log_dir_path
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "reforge.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s")
    )

    for name in ("reforge", "factorio", "plugin"):
        target = logging.getLogger(name)
        target.setLevel(level)
        target.handlers.clear()
        target.propagate = False
        target.addHandler(stream)
        target.addHandler(file_handler)

    return logging.getLogger("reforge")
