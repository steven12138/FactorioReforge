#!/usr/bin/env python3
"""A stand-in for the Factorio headless binary, for testing ServerProcess.

It mimics the behaviours that matter: the startup banner ending in the
``to(InGame)`` marker, echoing chat the way the real server does, responding to
``/players``, saving-then-exiting on ``/quit`` and on SIGINT, and -- crucially --
staying alive after stdin hits EOF, which is what the real 2.0.77 does.

Env knobs used by the tests:
    FAKE_IGNORE_QUIT=1    ignore /quit, to exercise the SIGINT escalation
    FAKE_IGNORE_SIGINT=1  ignore SIGINT too, to exercise SIGTERM
    FAKE_STARTUP_DELAY=s  delay before reaching InGame
"""

import os
import signal
import sys
import threading
import time

START = time.monotonic()
_alive = threading.Event()
_alive.set()


def emit(line: str) -> None:
    print(line, flush=True)


def engine(msg: str, level: str | None = None, src: str = "MainLoop.cpp:100") -> None:
    prefix = f"{time.monotonic() - START:8.3f}"
    emit(f"{prefix} {level} {src}: {msg}" if level else f"{prefix} {msg}")


def game_event(tag: str, content: str) -> None:
    emit(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{tag}] {content}")


def shutdown(reason: str) -> None:
    engine(f"Quitting: {reason}.")
    engine("Saving map as /fake/saves/probe.zip", "Info", "MainLoop.cpp:437")
    engine("Saving progress: 100.000000%", "Info", "MainLoop.cpp:448")
    engine("Goodbye")
    _alive.clear()


def on_sigint(signum, frame):
    if os.environ.get("FAKE_IGNORE_SIGINT") == "1":
        engine("Received SIGINT, ignoring (test mode)")
        return
    engine("Received SIGINT, shutting down")
    shutdown("signal")


def reader():
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if line == "/quit":
            if os.environ.get("FAKE_IGNORE_QUIT") == "1":
                continue
            shutdown("command")
            return
        elif line == "/players":
            emit("Players (0):")
        elif line == "/version":
            emit("2.0.77")
        elif line.startswith("/"):
            emit(f"Unknown command: {line}")
        else:
            game_event("CHAT", f"<server>: {line}")
    # Real Factorio logs this and keeps going -- so do we.
    engine("Got EOF on stdin; closing", "Error", "InterruptibleStdioStream.cpp:55")


def main() -> int:
    signal.signal(signal.SIGINT, on_sigint)
    engine("Factorio 2.0.77 (build 84539, linux64, headless)")
    engine("Loading map /fake/saves/probe.zip: 863501 bytes.")
    time.sleep(float(os.environ.get("FAKE_STARTUP_DELAY", "0.05")))
    engine("Hosting game at IP ADDR:({0.0.0.0:34199})")
    engine(
        "updateTick(0) changing state from(CreatingGame) to(InGame)",
        "Info", "ServerMultiplayerManager.cpp:808",
    )
    engine("Starting RCON interface at IP ADDR:({0.0.0.0:27019})",
           "Info", "RemoteCommandProcessor.cpp:126")

    threading.Thread(target=reader, daemon=True).start()
    while _alive.is_set():
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    sys.exit(main())
