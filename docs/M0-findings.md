# M0 — what measuring a real server actually revealed

Environment: Factorio **2.0.77** (build 84539, linux64, headless), Arch Linux,
Python 3.14.6. Raw samples in `factorio_output_samples.txt`; the probe that
produced them is `scripts/probe_stdout.py`.

**中文: [M0-findings_zh.md](M0-findings_zh.md)**

Three of the design's starting assumptions turned out to be wrong. They are
recorded here because each one changed the implementation, and because two of
them contradict what the documentation says.

---

## 1. stdout buffering is not a problem ✅

The plan listed "a C++ program will fully buffer stdout into a pipe" as the
highest risk. It does not. With the most naive possible setup,
`asyncio.create_subprocess_exec(stdout=PIPE)`:

```
0.01s  first line arrives
0.57s  changing state ... to(InGame)
6.02s  Players (0):        <- /players written at 6.00s, answered within two frames
9.02s  [CHAT] <server>: hello from probe
```

Factorio flushes per line itself. **No pty, no `stdbuf -oL`.** `core/process.py`
uses a plain asyncio pipe and the pty fallback was never needed.

## 2. EOF on stdin does not stop the server ⚠️ (opposite of the assumption)

The plan said "EOF on stdin kills Factorio, so closing the pipe is a fallback
way to stop it". On 2.0.77:

```
6.01s  Error InterruptibleStdioStream.cpp:55: Got EOF on stdin; closing
26.0s  >>> STILL ALIVE 20s after stdin EOF   <- the server is still running
```

It logs one error and carries on. That is *worse* than the assumption: closing
stdin neither stops the server nor leaves you any way to talk to it.

**Correction:** stdin stays open for the whole lifetime and is never closed.
Shutdown escalates `/quit` → `SIGINT` → `SIGTERM` → `SIGKILL`. SIGINT is
graceful and saves first:

```
16.011 Received SIGINT, shutting down
16.011 Quitting: signal.
16.011 Info MainLoop.cpp:437: Saving map as .../probe.zip
16.030 Info MainLoop.cpp:448: Saving progress: 100.000000%
16.528 Goodbye
```

## 3. stdout has **four** shapes, not two ⚠️

The two-regex plan was not enough:

| # | Shape | Example |
|---|---|---|
| A | Engine log, with level and source location | `   0.578 Info ServerMultiplayerManager.cpp:808: updateTick(926) changing state from(CreatingGame) to(InGame)` |
| B | Engine log, **elapsed seconds only**, no level or source | `   0.577 Hosting game at IP ADDR:({0.0.0.0:34199})`<br>`   0.543 Loading map /.../probe.zip: 863501 bytes.`<br>`  16.011 Received SIGINT, shutting down` |
| C | Game event, dated and tagged | `2026-08-02 02:16:35 [CHAT] <server>: hello from probe` |
| D | **Bare command output, no prefix at all** | `Players (0):` / `2.0.77` / `7 seconds` |

Shape D is what a stdin command prints back. It is indistinguishable in form
from ordinary text, so the parser has to try C → A → B and treat "nothing
matched" as D (`COMMAND_RESPONSE`) rather than as a failure.

Shape B is what breaks a regex that requires a level and a source location: the
`Info xxx.cpp:NN:` part of A is optional, so it has to be an optional group.

## 4. Confirmed anchor strings

- Startup finished: `changing state from(CreatingGame) to(InGame)`
- Listening: `Hosting game at IP ADDR:({0.0.0.0:34199})`
- RCON ready: `Starting RCON interface at IP ADDR:(...)`
- Shutdown done: `Goodbye`
- Our own chat coming back: `[CHAT] <server>: ...` — the Telegram bridge's loop
  guard depends on this

Save completion has **two** shapes and both must be recognised, or snapshotting
waits out its whole timeout:

- `/server-save` goes through AppManager: `Info AppManager.cpp:419: Saving finished`
- The save taken while shutting down goes through MainLoop:
  `Info MainLoop.cpp:448: Saving progress: 100.000000%`

## 5. Later findings, same category

Not part of the original M0, but they belong here — all are cases where the
documented behaviour and the real behaviour differ:

- **`game.table_to_json` was removed in 2.0**; it is `helpers.table_to_json`.
  Every structured query depends on it.
- **`force.get_evolution_factor()` now takes a surface**, and
  `force.item_production_statistics` became
  `force.get_item_production_statistics(surface)`.
- **`[gps=x,y,surface]` rich text is clickable** — it pings that position on
  everyone's map. The initial assumption that "Factorio has no clickable chat"
  was wrong, and `!!here` and `!!warp` are built on this.
- **The Source RCON sentinel trick does not work here.** Factorio never answers
  an empty command, so waiting for the sentinel's echo hangs forever. The
  working approach is: read the first packet carrying your request id, then
  briefly keep reading for continuation packets.
- **`add_chart_tag` succeeds on uncharted positions**, although the docs say the
  chunk must be charted first. Callers still have to handle `nil`.
- **Factorio overwrites `mod-list.json` from memory when it exits**, discarding
  anything changed while it ran. That is why `mod_manager` records its intent
  separately and reapplies it after the process is actually gone.

## 6. Not verified automatically

`[JOIN]` / `[LEAVE]` / `[DEATH]` / `[KICK]` need a real client to connect, so
they were not sampled. They share shape C with the verified `[CHAT]`, so the
parser handles them with one `[TAG] content` regex and an open-ended tag set —
an unknown tag does not raise, it degrades to `GENERAL_INFO` and warns once.
Worth resampling once real players have joined, and backfilling the unit tests.
