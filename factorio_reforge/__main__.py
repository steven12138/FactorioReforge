"""Entry point: ``python -m factorio_reforge [init|run]``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from pathlib import Path

from factorio_reforge.config import CONFIG_FILE, Config, ConfigError
from factorio_reforge.core.console import ConsoleReader
from factorio_reforge.core.server import ReforgeServer, build_logger
from factorio_reforge.plugin import builtin
from factorio_reforge.plugin.manager import CORE_VERSION

BANNER = rf"""
  FactorioReforge {CORE_VERSION}
  Type {{prefix}}FR help for commands. Anything else goes to the Factorio console.
"""


def cmd_init(root: Path) -> int:
    """Write config.yml and the directory layout, without overwriting anything."""
    config_path = root / CONFIG_FILE
    if config_path.exists():
        print(f"{config_path} already exists; leaving it alone.")
    else:
        config = Config()
        config.root = root
        config.dump(config_path)
        print(f"Wrote {config_path}")

    for name in ("plugins", "config", "logs", "snapshots"):
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        print(f"Ready: {directory}")

    print(
        "\nNext:\n"
        f"  1. Edit {config_path}: point working_directory and start_command at your\n"
        "     Factorio headless install, and set rcon.password.\n"
        "  2. Make sure saves.current_save names the same file as --start-server.\n"
        "  3. Run: python -m factorio_reforge"
    )
    return 0


async def run(config: Config) -> int:
    logger = build_logger(config)
    server = ReforgeServer(config, logger)

    server.commands.register("@core", builtin.build(server))
    server.commands.register("@core", builtin.build_save_commands(server))

    await server.boot()
    print(BANNER.format(prefix=config.command_prefix))

    loop = asyncio.get_running_loop()
    stopping = False

    def request_shutdown() -> None:
        """Stop the server, then exit. Called by a signal or by the console.

        A second request escalates to SIGKILL, because the usual reason for
        pressing Ctrl-C twice is that the first shutdown appears stuck.
        """
        nonlocal stopping
        if stopping:
            logger.warning("Second interrupt -- killing the server now")
            asyncio.create_task(server.interface.kill())
            return
        stopping = True
        logger.info("Stopping the server, then exiting")
        asyncio.create_task(server.shutdown(stop_server=True))

    # The console gets this too. On an interactive terminal prompt_toolkit runs
    # in raw mode and consumes Ctrl-C itself, so the signal handler below never
    # fires and this is the only path that notices.
    console = ConsoleReader(
        server.feed_console, on_interrupt=request_shutdown, logger=logger
    )
    console.start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, request_shutdown)

    if not await server.start_server():
        logger.error("The server did not start; FactorioReforge is exiting")
        await server.shutdown(stop_server=False)
        await console.stop()
        return 1

    await server.wait_for_exit()
    await console.stop()
    logger.info("Goodbye")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factorio_reforge")
    parser.add_argument(
        "command", nargs="?", default="run", choices=["run", "init"],
        help="'init' writes a default config.yml; 'run' (the default) starts the server",
    )
    parser.add_argument(
        "--config", default=CONFIG_FILE, help=f"path to the config file (default: {CONFIG_FILE})"
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    if args.command == "init":
        return cmd_init(config_path.parent if config_path.name == CONFIG_FILE else config_path)

    try:
        config = Config.load(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(run(config))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
