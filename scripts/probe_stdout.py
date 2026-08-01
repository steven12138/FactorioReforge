#!/usr/bin/env python3
"""M0 experiment: does Factorio headless flush stdout when it is a pipe?

Factorio is a C++ program, so libc will switch stdout to full buffering when it
is not a tty. If that happens, [JOIN]/[CHAT] lines sit in a 4-8 KiB buffer and
the whole event-driven design of FactorioReforge falls apart.

Three transports are tried, cheapest first:

  pipe   -- plain asyncio pipe
  stdbuf -- wrap in `stdbuf -oL -eL` to force line buffering
  pty    -- give the child a pseudo-terminal so it believes it is interactive

For each one we record how long after spawn each line arrives. The transport is
good enough if the "changing state ... to(InGame)" line shows up promptly
instead of appearing in one big burst at shutdown.

Usage:
    python scripts/probe_stdout.py [--transport pipe|stdbuf|pty|all] [--seconds 40]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pty
import shutil
import signal
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FACTORIO_DIR = REPO / "server" / "factorio"
BINARY = FACTORIO_DIR / "bin" / "x64" / "factorio"
SAVE = FACTORIO_DIR / "saves" / "probe.zip"
SAMPLES = REPO / "docs" / "factorio_output_samples.txt"


def server_args() -> list[str]:
    return [
        str(BINARY),
        "--start-server", str(SAVE),
        "--server-settings", str(FACTORIO_DIR / "server-settings.json"),
        "--port", "34199",
        "--rcon-port", "27019",
        "--rcon-password", "probe",
    ]


class Recorder:
    """Collects (elapsed_seconds, line) pairs and reports arrival gaps."""

    def __init__(self, transport: str) -> None:
        self.transport = transport
        self.t0 = time.monotonic()
        self.rows: list[tuple[float, str]] = []

    def add(self, line: str) -> None:
        dt = time.monotonic() - self.t0
        self.rows.append((dt, line))
        print(f"  [{self.transport:6s}] {dt:6.2f}s | {line}")

    def verdict(self) -> str:
        if not self.rows:
            return "NO OUTPUT AT ALL"
        # Full buffering shows up as a long silence followed by a burst: many
        # lines sharing (almost) the same arrival timestamp.
        first = self.rows[0][0]
        bursts: dict[float, int] = {}
        for dt, _ in self.rows:
            bursts[round(dt, 1)] = bursts.get(round(dt, 1), 0) + 1
        biggest = max(bursts.values())
        ingame = [dt for dt, line in self.rows if "to(InGame)" in line]
        parts = [
            f"{len(self.rows)} lines",
            f"first at {first:.2f}s",
            f"largest same-instant burst {biggest}",
        ]
        if ingame:
            parts.append(f"InGame at {ingame[0]:.2f}s")
        else:
            parts.append("InGame NEVER SEEN")
        ok = bool(ingame) and biggest < len(self.rows) * 0.8
        return ("OK   " if ok else "BAD  ") + " | " + ", ".join(parts)


async def _drain(reader: asyncio.StreamReader, rec: Recorder) -> None:
    while True:
        raw = await reader.readline()
        if not raw:
            return
        rec.add(raw.decode("utf-8", errors="replace").rstrip("\r\n"))


async def probe_pipe(rec: Recorder, seconds: float, use_stdbuf: bool) -> None:
    argv = server_args()
    if use_stdbuf:
        if not shutil.which("stdbuf"):
            print("  stdbuf not available, skipping")
            return
        argv = ["stdbuf", "-oL", "-eL", *argv]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(FACTORIO_DIR),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    pump = asyncio.create_task(_drain(proc.stdout, rec))
    await _exercise(proc.stdin, seconds)
    await _shutdown(proc, pump)


async def probe_pty(rec: Recorder, seconds: float) -> None:
    """Hand the child a pty so libc picks line buffering."""
    master, slave = pty.openpty()
    proc = await asyncio.create_subprocess_exec(
        *server_args(),
        cwd=str(FACTORIO_DIR),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
    )
    os.close(slave)

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    master_file = os.fdopen(master, "rb", buffering=0)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), master_file)
    writer_transport, writer_proto = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, os.fdopen(os.dup(master), "wb", buffering=0)
    )
    writer = asyncio.StreamWriter(writer_transport, writer_proto, None, loop)

    pump = asyncio.create_task(_drain(reader, rec))
    await _exercise(writer, seconds)
    await _shutdown(proc, pump)


async def _exercise(stdin, seconds: float) -> None:
    """Poke the server over stdin so it has a reason to print things."""
    await asyncio.sleep(seconds * 0.5)
    for cmd in ("/players", "/version", "/time", "hello from probe"):
        stdin.write((cmd + "\n").encode())
        await stdin.drain()
        await asyncio.sleep(1.0)
    await asyncio.sleep(seconds * 0.5)


async def _shutdown(proc, pump: asyncio.Task) -> None:
    try:
        proc.send_signal(signal.SIGINT)
        await asyncio.wait_for(proc.wait(), timeout=30)
    except (TimeoutError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    await asyncio.sleep(0.5)
    pump.cancel()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="all", choices=["pipe", "stdbuf", "pty", "all"])
    ap.add_argument("--seconds", type=float, default=20.0)
    args = ap.parse_args()

    if not BINARY.exists():
        print(f"factorio binary not found at {BINARY}", file=sys.stderr)
        return 1
    if not SAVE.exists():
        print(f"probe save not found at {SAVE}; create it with --create", file=sys.stderr)
        return 1

    transports = ["pipe", "stdbuf", "pty"] if args.transport == "all" else [args.transport]
    results: list[tuple[str, Recorder]] = []

    for name in transports:
        print(f"\n=== transport: {name} ===")
        rec = Recorder(name)
        if name == "pty":
            await probe_pty(rec, args.seconds)
        else:
            await probe_pipe(rec, args.seconds, use_stdbuf=(name == "stdbuf"))
        results.append((name, rec))

    print("\n=== verdict ===")
    for name, rec in results:
        print(f"{name:6s}: {rec.verdict()}")

    SAMPLES.parent.mkdir(parents=True, exist_ok=True)
    with SAMPLES.open("w", encoding="utf-8") as f:
        for name, rec in results:
            f.write(f"### transport={name}\n{rec.verdict()}\n")
            for dt, line in rec.rows:
                f.write(f"{dt:8.3f} | {line}\n")
            f.write("\n")
    print(f"\nsamples written to {SAMPLES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
