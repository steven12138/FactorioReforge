"""ReforgeServer -- assembles everything and owns the rollback orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Optional

from factorio_reforge.command.manager import CommandManager
from factorio_reforge.config import Config
from factorio_reforge.core.handler import FactorioHandler
from factorio_reforge.core.info import Info
from factorio_reforge.core.process import ServerProcess
from factorio_reforge.core.rcon import RconClient, RconError, RconManager
from factorio_reforge.core.reactor import InfoReactor
from factorio_reforge.permission import PermissionManager
from factorio_reforge.plugin import events as ev
from factorio_reforge.plugin.manager import PluginManager
from factorio_reforge.saves.manager import SaveError, SaveManager, Snapshot


class RollbackError(Exception):
    pass


class ReforgeServer:
    def __init__(self, config: Config, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or logging.getLogger("reforge")

        self.handler = FactorioHandler()
        self.process = ServerProcess(
            config.command_argv,
            config.working_dir_path,
            self._on_server_line,
            logger=self.logger,
            quit_timeout=config.quit_timeout,
            sigint_timeout=config.sigint_timeout,
            sigterm_timeout=config.sigterm_timeout,
            encoding=config.encoding,
        )
        self.permissions = PermissionManager(
            config.resolve("config") / "permission.yml", config.default_permission_level
        )
        self.commands = CommandManager(config.command_prefix, self.logger)
        self.saves = SaveManager(
            config.current_save_path,
            config.snapshot_dir_path,
            max_snapshots=config.saves.max_snapshots,
            max_age_days=config.saves.max_snapshot_age_days,
            logger=self.logger,
        )

        from factorio_reforge.plugin.interface import ServerInterface

        self.interface = ServerInterface(self)
        self.plugins = PluginManager(self, config.plugin_dir_paths, self.logger)
        self.reactor = InfoReactor(self, self.logger)

        self.rcon: Optional[RconManager] = None
        if config.rcon.enabled:
            self.rcon = RconManager(
                RconClient(
                    config.rcon.host, config.rcon.port, config.rcon.password,
                    connect_timeout=config.rcon.connect_timeout, logger=self.logger,
                ),
                retry_interval=config.rcon.retry_interval,
                logger=self.logger,
                on_connect=lambda: self.plugins.dispatch(ev.RCON_CONNECTED),
                on_lost=lambda: self.plugins.dispatch(ev.RCON_LOST),
            )

        self._save_completed = asyncio.Event()
        self._exiting = asyncio.Event()
        self._rollback_in_progress = False
        self._expect_stop = False
        self._crash_watch: Optional[asyncio.Task] = None
        self.started_at = time.monotonic()

    # -- console output ------------------------------------------------------

    def echo(self, line: str) -> None:
        """Print a server line. Split out so the console can own the terminal."""
        print(line, flush=True)

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
            "Loaded %d plugin(s)%s", len(loaded),
            f", {len(failed)} failed: {', '.join(failed)}" if failed else "",
        )
        await self.plugins.dispatch(ev.REFORGE_START)

    async def start_server(self) -> bool:
        if self.process.is_running:
            self.logger.warning("Server is already running")
            return False
        await self.plugins.dispatch(ev.SERVER_START_PRE)
        self._expect_stop = False
        try:
            await self.process.start()
        except Exception as exc:
            self.logger.error("Could not start the server: %s", exc)
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
        self.logger.error("Server exited unexpectedly (code %s)", code)
        await self.plugins.dispatch(ev.SERVER_CRASH, code)
        await self.process.cleanup()
        # A crash still frees the files, so listeners that clean up after the
        # server must hear about it the same way a clean stop is reported.
        await self.plugins.dispatch(ev.SERVER_STOP, code)
        if self.config.auto_restart_on_crash and not self._exiting.is_set():
            self.logger.info("Restarting in %.0fs", self.config.crash_restart_delay)
            await asyncio.sleep(self.config.crash_restart_delay)
            if not self._exiting.is_set():
                await self.start_server()

    async def shutdown(self, *, stop_server: bool = True) -> None:
        if self._exiting.is_set():
            return
        self._exiting.set()
        self.logger.info("Shutting down FactorioReforge")
        if self._crash_watch is not None:
            self._crash_watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._crash_watch
        if stop_server:
            await self.stop_server()
        await self.plugins.dispatch(ev.REFORGE_STOP)
        await self.plugins.unload_all()
        if self.rcon is not None:
            await self.rcon.stop()

    async def wait_for_exit(self) -> None:
        await self._exiting.wait()

    @property
    def is_exiting(self) -> bool:
        return self._exiting.is_set()

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.started_at

    # -- saves ---------------------------------------------------------------

    async def flush_save(self) -> bool:
        """``/server-save`` and wait for the completion line. False on timeout.

        Waiting on the real marker rather than sleeping is what makes a snapshot
        contain the current world instead of whatever autosave last wrote.
        """
        if not self.process.is_running:
            return False
        self._save_completed.clear()
        await self.process.write("/server-save")
        try:
            await asyncio.wait_for(self._save_completed.wait(), self.config.saves.save_timeout)
            return True
        except asyncio.TimeoutError:
            self.logger.warning(
                "No save-completion message within %.0fs; snapshotting the file as it stands",
                self.config.saves.save_timeout,
            )
            return False

    async def create_snapshot(
        self, comment: str = "", *, created_by: str = "unknown", automatic: bool = False
    ) -> Snapshot:
        players: list[str] = []
        if self.process.is_running:
            await self.flush_save()
            with contextlib.suppress(RconError):
                players = await self.interface.get_online_players()
        snapshot = await self.saves.create(
            comment, created_by=created_by, players_online=players, automatic=automatic
        )
        self.saves.rotate()
        await self.plugins.dispatch(ev.SNAPSHOT_CREATED, snapshot)
        return snapshot

    async def rollback(
        self, snapshot_id: int, *, countdown: float = 10.0, requested_by: str = "unknown"
    ) -> Snapshot:
        """Restore a snapshot: announce, back up the present, swap, restart.

        Step 2 is the important one -- taking a safety snapshot of the current
        world before overwriting it means rolling back to the wrong point is
        recoverable rather than terminal.
        """
        if self._rollback_in_progress:
            raise RollbackError("a rollback is already running")

        snapshot = self.saves.get(snapshot_id)
        if snapshot is None:
            raise RollbackError(f"no snapshot with id {snapshot_id}")
        if not self.saves.path_of(snapshot).is_file():
            raise RollbackError(f"snapshot #{snapshot_id} file is missing")

        self._rollback_in_progress = True
        was_running = self.process.is_running
        safety: Optional[Snapshot] = None
        try:
            await self.plugins.dispatch(ev.ROLLBACK_STARTED, snapshot, requested_by)

            if was_running and countdown > 0:
                await self._announce_countdown(snapshot, countdown)

            if was_running:
                self.logger.info("Taking a safety snapshot before rolling back")
                try:
                    safety = await self.create_snapshot(
                        f"before rollback to #{snapshot_id}",
                        created_by=requested_by, automatic=True,
                    )
                except SaveError as exc:
                    # No way back if this fails -- refuse rather than gamble.
                    raise RollbackError(
                        f"could not take a safety snapshot, aborting rollback: {exc}"
                    ) from exc

                self._expect_stop = True
                if not await self.process.stop():
                    raise RollbackError("the server would not stop; rollback aborted")
                await self.process.cleanup()
                if self.rcon is not None:
                    await self.rcon.stop()

            try:
                await self.saves.restore_file(snapshot)
            except SaveError as exc:
                raise RollbackError(f"restoring the snapshot failed: {exc}") from exc

            if was_running:
                if not await self.start_server():
                    await self._recover(safety)
                    raise RollbackError(
                        "the server did not come back up; the previous world was restored"
                    )

            self.logger.info("Rolled back to %s", snapshot.describe())
            await self.plugins.dispatch(ev.ROLLBACK_FINISHED, snapshot, True)
            return snapshot
        except Exception:
            await self.plugins.dispatch(ev.ROLLBACK_FINISHED, snapshot, False)
            raise
        finally:
            self._rollback_in_progress = False

    async def _announce_countdown(self, snapshot: Snapshot, seconds: float) -> None:
        await self.process.write(
            f"[FactorioReforge] Rolling back to {snapshot.created_at_text} "
            f"in {int(seconds)} seconds. The server will restart."
        )
        remaining = seconds
        while remaining > 0:
            step = 5 if remaining > 5 else remaining
            await asyncio.sleep(step)
            remaining -= step
            if remaining > 0 and self.process.is_running:
                with contextlib.suppress(RuntimeError):
                    await self.process.write(f"[FactorioReforge] Rollback in {int(remaining)}s")

    async def _recover(self, safety: Optional[Snapshot]) -> None:
        if safety is None:
            self.logger.error("No safety snapshot to fall back on")
            return
        self.logger.warning("Restoring the pre-rollback world from %s", safety.filename)
        try:
            await self.saves.restore_file(safety)
            await self.start_server()
        except Exception:
            self.logger.exception(
                "Recovery failed. The pre-rollback world is intact at %s -- restore it by hand",
                self.saves.path_of(safety),
            )


def build_logger(config: Config) -> logging.Logger:
    logger = logging.getLogger("reforge")
    logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", "%H:%M:%S")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    log_dir: Path = config.log_dir_path
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "reforge.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    )
    logger.addHandler(file_handler)

    logging.getLogger("plugin").setLevel(logger.level)
    for handler in logger.handlers:
        logging.getLogger("plugin").addHandler(handler)
    return logger
