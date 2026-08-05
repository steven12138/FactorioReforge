# Architecture

How FactorioReforge works inside, and why. Most of these decisions were forced
by something measured on a real server rather than chosen from a design
document — [Factorio notes](factorio-notes.md) records the measurements
themselves.

```
factorio_reforge/
├── core/            process, handler, info, reactor, rcon, console, loglens, server
├── plugin/          manager, registry, interface, events, metadata, builtin
├── command/         command tree builder, dispatch, command sources
├── permission/      five levels, persisted to config/permission.yml
├── saves/           slots, backup, restore
├── versions/        which Factorio build runs, and swapping it
└── config.py
```

The shape follows [MCDReforged](https://github.com/MCDReforged/MCDReforged)
closely enough that its concepts transfer, and the backup model is
[QuickBackupM](https://github.com/TISUnion/QuickBackupM)'s outright. Both have
years of running servers behind them; the parts that are different here are the
parts where Factorio is different.

## The two channels

| Channel | Carries | Why |
|---|---|---|
| **stdin** | Chat, admin commands, `/quit` | Always available, needs no port, returns nothing |
| **RCON** | Player list, Lua evaluation, private messages | The only way to read a result back |

`server.say()` goes over stdin. `server.get_online_players()` goes over RCON and
raises `RconError` if RCON is unavailable, rather than pretending to have
worked. Nothing silently degrades from one to the other, because a chat message
that vanished and a player list that came back empty look identical to the
caller and are not the same problem.

### Structured queries

RCON hands back a string, so reading anything out would normally mean scraping.
Instead every query is wrapped in `helpers.table_to_json` on the Lua side, and
plugins get real Python data. That one decision is why `get_server_stats`,
`get_online_player_details`, `lua_json` and the production sampler are all a few
lines each instead of a pile of regexes that break on the next patch.

Names are interpolated with `lua.lua_string()`, which escapes non-ASCII as
decimal byte escapes. Factorio runs Lua 5.2, which has no `\u` escape, so
`json.dumps` would emit source that does not compile.

The API moved in 2.0 in ways worth knowing: `game.table_to_json` →
`helpers.table_to_json`, `force.get_evolution_factor()` now takes a surface, and
`force.item_production_statistics` → `force.get_item_production_statistics(surface)`.

## Owning the process

Factorio's stdout is a pipe read with plain asyncio — measured first, because a
C++ program writing to a pipe is usually fully buffered, which would have meant
events arriving in kilobyte-sized bursts and a `pty` to work around it. It turns
out not to be: lines arrive immediately. The measurement is in
[Factorio notes](factorio-notes.md), and `scripts/probe_stdout.py` re-runs it.

**stdin is never closed.** The other measured surprise: EOF on stdin does *not*
stop a Factorio server, so closing the pipe is not a shutdown mechanism, and
keeping it open costs nothing.

Shutdown escalates, and waits at each step: `/quit` → SIGINT → SIGTERM →
SIGKILL. Ctrl-C takes the same path, which is why it takes a moment — Factorio
is saving. `on_server_stop` fires only once the process has actually exited,
carrying its return code, because plugins that touch files the server held open
(`mod-list.json`) are wrong to run any earlier.

## Parsing output

Factorio produces **four** shapes of line, not the two the wiki suggests:

```
   0.001 2026-08-02 14:02:11; Factorio 2.0.77 (build 84115, linux64, headless)
   1.234 Info ServerMultiplayerManager.cpp:791: updateTick(4) changing state ...
2026-08-02 14:02:31 [JOIN] Alice joined the game
Online players (1):
```

An engine line with a level, an engine line with only elapsed seconds, a
`[TAG]` game event, and a bare response to something typed. Each becomes an
`Info` with `source`, `content`, `tag`, `player`, `is_user` and an action flag.

A line that matches nothing is passed through as `GENERAL_INFO` with a warning
rather than being dropped — a format change in a future patch should degrade the
features that depend on it, not take the server manager down.

`[CHAT] <server>: ...` is recognised as FactorioReforge's own voice and
discarded. Without that, the Telegram bridge relays its own relays forever.

## Events pushed out of the game

Most of what a plugin wants to know is polled, because RCON only answers
questions it is asked. **Some of it does not have to be.** Measured on 2.0.77:
`script` is available inside a `/sc` command, `script.on_event` registers a
handler that really fires, and `print()` from inside that handler reaches
stdout — which is already being parsed. So an event can travel game → stdout →
plugin in one tick, with no mod and no `/c`.

A plugin opts in with `server.request_lua_event("on_research_finished")` and
receives `on_lua_event(server, payload)`. Research completion went from up to
two minutes late to the tick it happened.

Three things this has to get right, all of them found by measuring:

* **Chain, never replace.** `script.on_event` overwrites, and a plain freeplay
  save already has handlers on `on_research_finished` and `on_player_created`.
  The previous handler is captured with `script.get_event_handler` and called
  first — before our payload, so a fault on our side cannot cost the game its
  own behaviour.
* **Install once per server start.** Handlers do not survive a save/load, so
  they are reinstalled when RCON reconnects; but installing twice in one session
  makes our own wrapper the "previous" handler and prints everything twice. The
  count lives on this side, because the game cannot tell the difference.
* **The bridge is deliberately narrow.** `on_entity_died` fires thousands of
  times a minute on a defended base, so what crosses it is a short list with a
  declared payload rather than anything a plugin asks for.

Bridged lines are dispatched and dropped before the echo: they are machine
traffic, and JSON in the operator's console is not an improvement.

Polling stays where it earns its place — it is the only thing that catches what
happened while FactorioReforge was down, so `world_watch` keeps its slow sweep
under the push and both write to the same seen-set.

## Command dispatch

Commands run in their **own task**, never on the read loop.

This is not a performance decision. A handler like `!!qb make` waits for
Factorio to print "Saving finished" — and that line can only arrive through the
stdout pump. Run the handler inline on the pump and it waits for a line only it
could read: the console stops responding, the game appears frozen, and the whole
thing unblocks a hundred and twenty seconds later on a timeout. A regression
test reproduces exactly that shape.

Parsing and event dispatch stay inline, so line ordering is preserved.

## Backups and restoring

The slot model is QuickBackupM's, copied rather than reinvented.

**Slots.** A backup always goes to **slot 1**; everything else shifts down one.
The slot sacrificed to make room is the first empty one, or failing that the
highest-numbered slot past its `delete_protection`. If every slot is still
protected, the backup is **refused** rather than destroying something somebody
asked to keep. `saves.slot_protection` is a list of seconds whose *length* is
the number of slots.

**Two rings.** Automatic backups shift only automatic slots, and are addressed
as `a1`, `a2`. This is the one place the model departs from QBM, and it is not a
preference: a timer running every half hour walks the entire history out of the
building overnight, and what it pushes off the end is the backup someone took
before doing something risky — the only reason the feature exists.
`saves.auto_slot_protection` sizes that ring the same way.

**Restoring**, via `!!qb back <slot>` then `!!qb confirm`:

1. Verify the slot holds a valid zip
2. Count down in chat, one second at a time, abortable with `!!qb abort`
3. Stop the server and wait for the process to actually exit
4. **Copy the current world into the fixed `overwrite` slot** — QBM's undo for
   restoring the wrong thing
5. Replace `current_save` through a temp file and a rename, so an interrupted
   copy cannot truncate the world
6. Start the server again
7. On failure, put the `overwrite` world back and say so

Refusing to proceed when step 4 fails is deliberate. Without a way back, a
restore is a one-way door.

### Two things Factorio does better than Minecraft here

`/server-save <name>` writes a **separate, complete** save and leaves the live
world alone, so a backup goes straight into its slot — no copy, and no
overwriting the world in order to back it up, which is what a bare
`/server-save` was doing in an earlier version of this.

And a world is one zip rather than a live directory, so QBM's `save-off` /
`save-all flush` dance has no equivalent here and is simply absent.

## Changing the Factorio version

A save format upgrade is a one-way door: once the new build has written the
world, the old one cannot open it. That makes swapping the server binary a
restore problem rather than a download problem, and `versions/` is shaped
accordingly — stage, back up, swap, verify, put it back.

Two things are read rather than assumed, which is what lets the answer be known
before the server stops:

* **The binary states its own window.** `--version` prints a *map input* and a
  *map output* version, so whether a build can open a given world is arithmetic
  and not a table of rules that goes stale.
* **The save states its own version**, as four little-endian `uint16` at the
  start of `level-init.dat`.

Installed versions are separate directories with a symlink pointing at the live
one, because rollback has to work with the server down and possibly the network
with it; a symlink flip is the only swap that cannot fail halfway. The world is
symlinked back into each tree, since Factorio resolves its data paths from the
executable and would otherwise write saves *inside* the version that is about to
be rolled back.

The world from before a swap goes to a fixed `pre-upgrade` slot, a second
`overwrite` in every respect except that a restore does not spend it — it has
to outlive one, being the only world an older binary can still open. That slot
is also what makes a downgrade expressible at all: the binary and the world go
back together, in one operation, or not at all.

## Ratios and the solver

`!!ratio` is the one piece of arithmetic here complicated enough to be worth
describing, and the design is not original: Kirk McDonald's calculator,
FactorioLab and YAFC all converged on the same shape, so `calculator/solver.py`
does too rather than inventing a fourth answer.

Recipes become a **matrix**: rows are items, columns are recipes, a cell is how
much of that item one craft nets — positive for a product, negative for an
ingredient. The rates you want are the solution to `A x >= b`.

A recursive walk down the ingredient tree is enough for most of the graph, and
that is only because most items have exactly one recipe. Two shapes break it,
both of them in vanilla:

* **Several recipes producing overlapping items.** Advanced oil processing makes
  heavy, light and petroleum together; heavy cracking turns heavy into light;
  light cracking turns light into petroleum. How much of each to run is not a
  property of any one recipe, so a walk has to guess.
* **A recipe that consumes what it produces.** Kovarex lists uranium-238 as both
  an ingredient and a product, and a walk recurses on it forever.

Both are the same solve. Whether to crack is decided by minimising cost, and the
cycle nets out in the coefficients — an item is treated as raw when no recipe
*nets* any of it, which is what stops the solver from believing Kovarex makes its
own uranium-238.

**Exact arithmetic, throughout.** The ratios in this game are fractions: 3/2
gears per belt, 7/12 of a machine. In floating point that becomes
0.5833333333333334, and a machine count comes out as 2.9999999999999996 —
technically right and visibly wrong. Everything is `fractions.Fraction` until
the moment it is printed.

**Bland's rule, not the fastest pivot.** Degenerate vertices are the normal case
here rather than a corner case: two recipes producing the same item in the same
proportion is a tie, and Factorio's graph is full of them. Bland's rule is the
one pivot choice that provably cannot cycle. The problems are tens of variables,
so what it costs is not measurable and what it guarantees is termination.

The data does not live in this repository. It is read from the running game
through the same RCON channel as everything else, which makes the answer correct
for the server's version and its mods, and makes a stale table impossible. What
it costs is that the server has to be up. See
[`calculator`](plugins.md#calculator).

## Plugins

Discovery, dependency-ordered loading, and hot reload. On reload the registry is
cleared first, so commands, event listeners, help entries and translations from
the previous version cannot survive into the new one.

Reloading uses a loader that bypasses the bytecode cache. Without it, Python
happily served the *previous* compiled version when a file changed inside the
same second — a reload that silently did nothing, which is worse than one that
fails.

The plugin-facing API is one object, `ServerInterface`, so there is exactly one
surface to keep stable.

## The console

One format for every line, whatever produced it:

```
14:02:11 INF reforge        Loaded 13 plugin(s)
14:02:14 INF factorio       Hosting game at IP ADDR:({0.0.0.0:34197})
14:02:14 INF mod_manager    only offering mods built for Factorio 2.0.77
```

The source column says where a line came from — `reforge`, `factorio`, or a
plugin id. Factorio's output used to go through a bare `print`, which put two
unrelated formats side by side and, worse, kept the server's own words out of
`logs/reforge.log` entirely.

**Factorio's lines are verbatim.** Not reworded, not annotated, not re-levelled.
What you see matches the game's own log and anything you might paste into a bug
report.

Colour is applied only when stdout is a terminal that wants it; the file handler
never gets it, so `logs/reforge.log` stays greppable. With `prompt_toolkit`
installed, log lines never interrupt what you are typing.

### The startup report

Because Factorio's lines stay untouched, anything worth *saying* about them is
said separately, a couple of seconds after the server comes up:

```
Startup check: 0 problem(s), 2 notice(s), 3 routine
  listening for players on 0.0.0.0:34197 (UDP)
  RCON is bound to 127.0.0.1:27015, which is not reachable from outside
  audio is off -- normal on a headless server
  no Steam cloud player data -- normal on a headless server
  no personal blueprint library (blueprint-storage-2.dat); a headless server has
  no local player, so it never creates one. Harmless, and it appears on every
  start. Do not create the file by hand -- an empty one is read as corrupt.
```

Several routine Factorio lines say "not found" — the blueprint-storage fallback,
the absent cloud data — and without somewhere to say so, every operator
investigates each of them once.

The blueprint advice is that specific because it answers the next question:
measured on 2.0.77, a headless server never writes `blueprint-storage-2.dat`
even after a clean shutdown, and creating an empty one to silence the message
produces `Loading local blueprint storage failed: Couldn't read from input
file` — worse than the message it was meant to remove.

Problems come first, and a clean start says nothing at all. The report waits a
moment rather than firing on the "in game" marker, because some of the lines
worth reporting — the RCON bind above all — are printed after it.

## Tests

```bash
python -m pytest tests/ -q        # 649 tests
```

Parser tests run against output sampled from a real server, in
`docs/factorio_output_samples.txt`. Process tests drive `tests/fake_factorio.py`,
a stand-in that reproduces the real binary's behaviours — including surviving
stdin EOF, which is why FactorioReforge never closes that pipe.

Some tests exist to keep a promise rather than to catch a bug: that no source
file anywhere issues `/c`, that every bundled plugin ships both languages with
matching placeholders, that no translation catalogue contains a YAML boolean
key, and that none defines the same key twice — YAML keeps the last one and says
nothing, which cost `crash_doctor` its advice line for the whole life of the
plugin.
