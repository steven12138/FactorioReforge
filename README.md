<p align="center">
  <img src="docs/banner.svg" alt="FactorioReforge — a process manager and plugin framework for Factorio headless servers" width="100%">
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white">
  <img alt="Factorio 2.0" src="https://img.shields.io/badge/factorio-2.0%20headless-d4761a">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="222 tests" src="https://img.shields.io/badge/tests-222%20passing-brightgreen">
  <img alt="i18n" src="https://img.shields.io/badge/i18n-en%20%C2%B7%20zh__cn-blue">
</p>

<p align="center">
  <b>English</b> · <a href="README_zh.md">简体中文</a>
</p>

<p align="center">
  <a href="docs/TUTORIAL.md"><b>📖 Step-by-step tutorial</b></a> ·
  <a href="#part-1--running-a-factorio-multiplayer-server">Run a server</a> ·
  <a href="#part-2--factorioreforge">Use FactorioReforge</a> ·
  <a href="#writing-a-plugin">Write a plugin</a> ·
  <a href="#bundled-plugins">Plugins</a>
</p>

---

> **First time here?** Start with the **[step-by-step tutorial](docs/TUTORIAL.md)** —
> thirteen sections from a bare machine to Telegram control, every command
> verified on a real server. This README is the reference manual.

---

A process manager and plugin framework for Factorio headless servers, in the
shape of [MCDReforged](https://github.com/MCDReforged/MCDReforged): it owns the
server process, parses its output into structured events, and hands those to
plugins that can register commands and react to what happens in game.

Verified against **Factorio 2.0.77** headless on Linux.

- Part 1 below is how to run a Factorio multiplayer server at all.
- Part 2 is FactorioReforge itself.

---

# Part 1 — Running a Factorio multiplayer server

## Ways to play together

| Approach | Good for | Notes |
|---|---|---|
| Host from the game client | A quick session with friends | Ends when the host quits; nothing can manage it |
| **Headless dedicated server** | Anything long-lived ← this project | No graphics or audio, small memory footprint, runs unattended |
| Public matchmaking | Getting found without port forwarding | `visibility.public = true` registers with Factorio's matching servers |

## Install the headless server

The headless build is a separate download from the game client. Keep it in its
own directory — do not point it at `~/.factorio`.

```bash
mkdir -p ~/project/FactorioReforge/server && cd ~/project/FactorioReforge/server
curl -L -o factorio-headless.tar.xz "https://factorio.com/get-download/stable/headless/linux64"
tar -xJf factorio-headless.tar.xz
./factorio/bin/x64/factorio --version
```

It unpacks self-contained (`config-path.cfg` sets
`use-system-read-write-data-directories=false`), so saves, mods and config all
live under `factorio/`:

```
factorio/
├── bin/x64/factorio
├── data/                 # base game data; also the .example.json configs
├── saves/                # .zip saves — what a restore operates on
├── mods/
└── config/config.ini
```

Players must have the same mod and DLC set as the server or they cannot connect.

## Create a map

```bash
cd ~/project/FactorioReforge/server/factorio
./bin/x64/factorio --create ./saves/reforge.zip
# optionally: --map-gen-settings ./map-gen-settings.json --map-settings ./map-settings.json
```

## server-settings.json

```bash
cp data/server-settings.example.json ./server-settings.json
```

The settings that actually matter:

| Key | Why you care |
|---|---|
| `visibility.public` / `visibility.lan` | Public needs `username` + `token` from your factorio.com account (the token is in `~/.factorio/player-data.json`) |
| `game_password` | Simplest access control |
| `require_user_verification` | Checks players against factorio.com |
| `allow_commands` | `true` / `false` / `admins-only`. Allowing cheat commands marks the save permanently |
| `autosave_interval` / `autosave_slots` | Rotating `_autosave1..N.zip` |
| `auto_pause` | Saves CPU with nobody online, but the world stops advancing — timed plugins should account for it |
| `non_blocking_saving` | Saves without stalling the game. Worth turning on |

Companion files, each a JSON array of player names:

```bash
echo '["your_factorio_name"]' > server-adminlist.json
echo '[]' > server-whitelist.json
echo '[]' > server-banlist.json
```

## Start it

```bash
cd ~/project/FactorioReforge/server/factorio
./bin/x64/factorio \
  --start-server ./saves/reforge.zip \
  --server-settings ./server-settings.json \
  --server-adminlist ./server-adminlist.json \
  --server-banlist  ./server-banlist.json \
  --port 34197 \
  --rcon-port 27015 --rcon-password 'CHANGE_ME'
```

| Flag | Meaning |
|---|---|
| `--start-server FILE` | Load this save |
| `--start-server-load-latest` | Load the newest save — **do not use with FactorioReforge**, see backups below |
| `--start-server-load-scenario [MOD/]NAME` | Start from a scenario |
| `--console-log FILE` | Mirror console output, chat included, to a file |
| `--port N` / `--bind ADDR[:PORT]` | Game port, default 34197/**UDP** |
| `--rcon-port N` / `--rcon-password PW` / `--rcon-bind ADDR:PORT` | Remote console (TCP) |
| `--mod-directory PATH` | Alternate mod directory |

## Networking

- Game traffic is **34197/UDP**, not TCP. Forward and open that.
- RCON is **27015/TCP** and the protocol is plaintext — bind it to `127.0.0.1` and
  never expose it.
- LAN: `visibility.lan = true` and clients discover it automatically.
- Direct connect: **Multiplayer → Connect to address** → `IP:34197`.

```bash
sudo ufw allow 34197/udp     # if you run a firewall
```

## The server console

**Whatever you type on stdin goes into the game as the server's chat line.**
Plain text broadcasts; a leading `/` runs a command.

`/players` `/admins` `/version` `/time` `/seed` `/promote` `/demote` `/kick`
`/ban` `/unban` `/mute` `/whitelist add|remove` `/server-save`
`/quit` (saves, then exits) `/c` (cheat Lua — permanently flags the save)
`/sc` (silent-command Lua — does not flag it)

Output comes in four shapes, which is why the parser looks the way it does:

```
   0.578 Info ServerMultiplayerManager.cpp:808: ... to(InGame)   engine log, with level
   0.577 Hosting game at IP ADDR:({0.0.0.0:34197})               engine log, timestamp only
2026-08-02 02:16:35 [CHAT] Alice: hello                          game event
Players (0):                                                     command output, no prefix
```

## Saves and restoring

Autosaves rotate through `saves/_autosave1.zip`…`_autosaveN.zip`. FactorioReforge
does not reuse them: it asks the server to write its own backup files, so an
autosave cycle can never overwrite a backup you meant to keep.

**Factorio cannot swap the loaded map at runtime.** Restoring means: stop the
server, replace the save file, start it again. Everything in Part 2's backup
support is built around that constraint.

---

# Part 2 — FactorioReforge

## What it adds

- Owns the Factorio process: start, graceful stop, crash detection, optional auto-restart
- Parses stdout into structured events and dispatches them to plugins
- `!!`-prefixed commands from the console **and** from in-game chat, with a five-level permission model
- Slot-based backups and an orchestrated restore that will not leave you without a world
- Hot-reloadable plugins
- English and Simplified Chinese throughout
- Thirteen bundled plugins: Telegram control, mod installs, map rendering,
  crash diagnosis, a blueprint library, production charts and more

## Install

```bash
cd ~/project/FactorioReforge
python -m venv .venv && . .venv/bin/activate
pip install -e ".[console,telegram,dev]"
```

## Configure

```bash
python -m factorio_reforge init      # writes config.yml plus plugins/ config/ logs/ snapshots/
```

Then edit `config.yml`: point `working_directory` and `start_command` at the
headless install from Part 1, and set `rcon.password` to match the one in
`start_command`.

Two things are checked at startup and refused rather than silently misbehaving:

- `start_command` must not use `--start-server-load-latest`. Restoring replaces
  `saves.current_save`, but autosaves are newer, so the server would come back on
  the wrong map.
- The file named by `--start-server` must be the same file as
  `saves.current_save`, or a restore would write somewhere the server never reads.

## Run

```bash
python -m factorio_reforge
```

Server output is echoed to your terminal. Type into the same terminal: `!!`
lines are FactorioReforge commands, anything else is passed to Factorio's stdin.

## Commands

```
!!FR help                        list commands
!!FR status                      server, RCON, plugin and snapshot state
!!FR plugin list                 loaded plugins (marks ones whose file changed)
!!FR plugin reload <id>          reload one plugin
!!FR plugin unload <id>
!!FR reload                      reload every plugin whose file changed
!!FR server start|stop|restart   Factorio lifecycle
!!FR server kill                 SIGKILL; loses everything since the last save
!!FR permission list
!!FR permission set <player> <guest|user|helper|admin|owner>
!!FR exit                        stop the server and quit

!!save                           list backup slots
!!save make [comment]            back up into slot 1
!!save back [slot]               stage a restore (default slot 1)
!!save confirm                   perform it, after a countdown
!!save abort                     cancel, staged or counting down
!!save del <slot>
!!save rename <slot> <comment>

!!here                           announce your position and pin it on the map
!!info [player]                  playtime, permissions, position
!!list                           who is online, with playtime
!!seen <player>                  playtime and when they were last online
!!stats                          evolution, pollution, research, world uptime
!!tp <player> <target|x y>       teleport — off by default, see below
!!autosnap [now]                 automatic snapshot status

!!mod search <query>             find mods on the portal
!!mod info <name>                details and dependencies
!!mod list                       what is installed
!!mod install <name> [version]   download and enable (admin)
!!mod remove|enable|disable <n>  (admin)
!!mod updates                    what has a newer release (admin)

!!warp [name] / set / del        named map locations — nobody is teleported
!!bp list / save / get / del     shared blueprint library
!!prod [item] / top              production rates with sparklines
!!top [time|kills|built]         rankings
!!watch                          evolution, pollution, research, rockets
!!why                            why the server last exited (admin)
!!web                            web panel address (admin)
!!map                            render the map and send it
!!FR lang                        translation status
```

Permissions: `guest(0) user(1) helper(2) admin(3) owner(4)`, stored in
`config/permission.yml`. The FactorioReforge console is always `owner` — whoever
holds that terminal can already stop the process.

## How the two channels are used

| Channel | Carries | Why |
|---|---|---|
| **stdin** | chat, admin commands, `/quit` | Always available, no extra port, returns nothing |
| **RCON** | player list, Lua evaluation, private messages | The only way to read a result back |

`server.say()` goes over stdin; `server.get_online_players()` goes over RCON and
raises if RCON is unavailable rather than pretending to have worked.

### Structured queries

RCON returns a string, so anything read back would normally need scraping.
Instead every query is wrapped in `helpers.table_to_json`, so you get real data:

```python
stats = await server.get_server_stats()
# {'tick': 18569, 'evolution': 0.00123, 'pollution': 0.0,
#  'research': None, 'players_online': 0, 'surface': 'nauvis', ...}

for p in await server.get_online_player_details():
    print(p["name"], p["online_time"], p["position"])

await server.add_map_marker({"x": 0, "y": 0}, "base", icon={"type": "virtual", "name": "signal-info"})
await server.teleport_player("alice", {"x": 100, "y": 200})

value = await server.lua_json("game.forces.player.get_entity_count('lab')")
```

`lua_json` takes a Lua *expression* and returns parsed Python. Lua errors come
back as exceptions with the Lua message, not as text starting with "Cannot
execute command".

Both failure modes — RCON down (`RconError`) and Lua failed (`LuaError`) —
derive from `factorio_reforge.core.errors.QueryError`, so plugin code catches
one thing:

```python
from factorio_reforge.core.errors import QueryError
try:
    stats = await server.get_server_stats()
except QueryError as exc:
    await source.reply(f"Could not look that up: {exc}")
```

Player names are interpolated with `lua.lua_string()`, which escapes non-ASCII
as decimal byte escapes — Factorio runs Lua 5.2, which has no `\u` escape, so
`json.dumps` would produce source that does not compile.

Verified against 2.0.77. Watch out for API that moved since 1.1:
`game.table_to_json` → `helpers.table_to_json`; `force.get_evolution_factor()`
now takes a surface; `force.item_production_statistics` →
`force.get_item_production_statistics(surface)`.

## Backups and restoring

Modelled on [QuickBackupM](https://github.com/TISUnion/QuickBackupM), which has
been doing this on Minecraft servers for years. Its logic is copied rather than
reinvented.

**Slots.** A backup always goes to **slot 1**; the others shift down one. The
slot sacrificed to make room is the first empty one, or failing that the
highest-numbered slot past its `delete_protection`. If every slot is still
protected the backup is **refused** rather than destroying something someone
asked to keep. `saves.slot_protection` in `config.yml` is a list of seconds —
its length is the number of slots, and the defaults keep the two oldest safe
from a burst of backups.

**Restoring**, via `!!save back <slot>` then `!!save confirm`:

1. Verify the slot holds a valid zip
2. Count down in chat, one second at a time, abortable with `!!save abort`
3. Stop the server and wait for the process to actually exit
4. **Copy the current world into the fixed `overwrite` slot** — QBM's undo for
   restoring the wrong thing
5. Replace `current_save` via a temp file and rename, so an interrupted copy
   cannot truncate it
6. Start the server again
7. On failure, put the `overwrite` world back and say so

Refusing to proceed when step 4 fails is deliberate: without a way back, a
restore is a one-way door.

### Two things Factorio does better than Minecraft here

`/server-save <name>` writes a **separate, complete** save and leaves the live
one alone, so a backup is written straight into its slot. No copy, and no more
overwriting the world in order to back it up — which is what a bare
`/server-save` was doing before. And a world is one zip rather than a live
directory, so QBM's `save-off` / `save-all flush` dance is unnecessary.

## Writing a plugin

Drop a `.py` file — or a directory with `__init__.py` — into `plugins/`.

```python
from factorio_reforge.command.builder import Literal, GreedyText
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "greeter",
    "version": "1.0.0",
    "name": "Greeter",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

def on_load(server, prev):
    server.register_command(
        Literal("!!hello")
        .requires(PermissionLevel.USER)
        .runs(lambda source: source.reply("hi"))
    )

async def on_player_joined(server, player, info):
    await server.say(f"Welcome, {player}!")

async def on_unload(server):
    ...   # cancel tasks, close sockets
```

Events: `on_info` `on_user_info` `on_player_joined` `on_player_left`
`on_player_death` `on_server_start_pre` `on_server_start` `on_server_startup`
`on_server_stop` `on_server_crash` `on_rcon_connected` `on_rcon_lost`
`on_snapshot_created` `on_rollback_started` `on_rollback_finished`
`on_server_stop_pre` `on_reforge_start` `on_reforge_stop` `on_load` `on_unload`.

`on_server_stop` fires **after** the process has exited, with its return code —
anything that touches files Factorio held (`mod-list.json` above all) must wait
for that. `on_server_stop_pre` is the one that runs while the server is still up.

You can also register explicitly, with a priority, or by decorator:

```python
server.register_event_listener("reforge.player_joined", callback, priority=50)

from factorio_reforge.plugin.events import event_listener
@event_listener("reforge.player_joined", priority=50)
async def welcome(server, player, info): ...
```

Callbacks may be sync or `async def`, and may declare fewer parameters than the
event carries. A listener that raises is logged and skipped — one broken plugin
does not stop the others.

Per-plugin storage lives in `config/<plugin_id>/`:

```python
config = server.load_config_simple("config.json", {"enabled": True})
server.save_config_simple(config)
```

Missing keys are filled in from the defaults, so adding a setting in a new
version does not force operators to edit their file by hand.

## Bundled plugins

**`telegram_bridge`** — relays chat both ways and exposes `/status` `/players`
`/say` `/save` `/saves` `/rollback` `/confirm` `/restart` `/stopserver`
`/startserver` `/cmd`. Configure `config/telegram_bridge/config.json`: put your
BotFather token in `token`, then send the bot a message and read the chat id out
of the log to fill `allowed_chat_ids`. Destructive commands need
`admin_user_ids`; `/cmd`, which runs anything, needs `owner_user_ids` and asks
for confirmation. `/rollback` always requires a second `/confirm`.

**`auto_snapshot`** — snapshots on an interval and when the last player leaves.
Skips the timer when nobody is online, since with `auto_pause` the world has not
moved and the snapshots would be identical.

**`server_utils`** — `!!here` `!!info` `!!list` `!!seen` `!!stats` `!!tp`, ported
from the MCDReforged plugins people miss most. `!!here` sends a clickable
`[gps=]` tag, which pings the position on everyone's map, *and* pins a chart tag
so the spot stays marked — see [Rich text](#rich-text).

`!!tp` is **off by default**. Teleporting skips the walking, the trains and the
danger the game is built around, so whether a server wants it is the operator's
call, not a default. Set `enable_teleport: true` in
`config/server_utils/config.json` to register the command, and
`teleport_permission` (`admin` or `user`) to decide who gets it. When it is off
the command does not exist at all, which is a stronger guard than registering it
and refusing at call time.

**`join_motd`** — greets players on join with a message built from live data:
`{player} {online} {total} {uptime} {day} {evolution} {pollution} {research}
{snapshots} {last_snapshot}`.

**`mod_manager`** — searches, installs, updates and removes mods from the
[mod portal](https://mods.factorio.com), from chat or from Telegram.

Credentials come from `config/mod_manager/config.json`, falling back to the
`service-username` and `service-token` in your `~/.factorio/player-data.json`.
Browsing needs no credentials; downloading needs an account that owns the game.
The token is never logged or echoed.

Three things it handles that are easy to get wrong:

- **Version filtering.** It asks the binary `--version` at load and only offers
  releases built for that Factorio. Skipping this is not cosmetic: installing
  flib 0.17.2 (built for 2.1) onto a 2.0.77 server made the server exit with
  code 1 on the next start.
- **Factorio overwrites `mod-list.json`.** A running server holds the list in
  memory and writes its own version out when it stops, discarding anything
  changed underneath it. The plugin records its intent separately and reapplies
  it on `on_server_stop`, once the process is actually gone.
- **Required dependencies only.** `?` and `(?)` entries are skipped — installing
  every optional dependency of a large overhaul mod would pull in dozens of
  unrelated mods.

Searching filters a cached copy of the full mod list (~22,500 entries, 13 MB,
~14 s to fetch, refreshed on a TTL) because the portal has no text-search
endpoint. Exact and prefix matches outrank substring hits, with download count
breaking ties.

### Telegram sub-plugins

`telegram_bridge` is also a **service** other plugins register with, so a plugin
can be reachable from Telegram without importing `telegram`:

```python
def on_load(server, prev):
    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is not None:
        bridge.register_command(
            "my_plugin", "hello", handler, level="admin", help="say hello"
        )
    # The bridge re-announces itself after a reload; re-register there too.
    server.register_event_listener("telegram.ready", lambda s: on_load(s, None))

async def handler(ctx):
    if not await ctx.confirm("Really do the thing?"):
        return
    await ctx.reply(f"done, {ctx.user_name}")
```

`ctx` carries `args`, `text`, `user_id`, `user_name`, `level`, `is_admin`,
`is_owner`, plus `reply()` (which splits messages over Telegram's 4096-character
limit) and `confirm()` (inline Yes/Cancel buttons, `False` on timeout).
Levels are `viewer` / `admin` / `owner`, resolved from the chat and user id
lists in the bridge's config. Registrations are keyed by owning plugin, so
unloading a plugin takes its commands with it.

`mod_manager` uses this for `/mods` `/modsearch` `/modinfo` `/modinstall`
`/modremove` `/modupdates` — `/modinstall` confirms, then offers to restart.

### The rest of the bundled plugins

**`crash_doctor`** — keeps a rolling buffer of output and, when the server exits
unexpectedly, matches it against real failure signatures and names the cause plus
the command that fixes it. On the actual incompatible-mod failure from
development it reports:

```
Server exited with code 1: the mod 'flib' could not be loaded
  Incompatible Factorio version (current: 2.0, required: 2.1); Dependency base >= 2.1.0 is not satisfied
  Try: !!mod remove flib
```

Two rules make the matching work: newest failure first, so a stale error in the
buffer does not shadow the fresh one; and within one failure block, the header
naming the culprit beats the indented detail lines describing its symptoms.
`!!why` replays the last diagnosis.

**`warp`** — named locations, announced as clickable `[gps=]` tags and pinned as
chart tags. **Nobody is teleported** — this is the information half of `!!tp`
without the balance half. Admins set them, everyone can look them up.

**`blueprints`** — a server-side library. `!!bp save <name>` blueprints the area
around you, `!!bp get <name>` puts it in someone else's inventory. Entirely
server-side through a scratch inventory, so no client needs anything installed.
Strings are validated on the way in, so a malformed one is rejected at save time
rather than failing when someone asks for it.

**`production`** — samples `get_flow_count` on a timer to build history that
outlives a session, since Factorio's own production graph is per-client.
Renders as a Unicode sparkline in chat and as an SVG in the web panel; no
plotting library either way.

**`world_watch`** — evolution and pollution alerts, plus research and rocket
milestones. One plugin because both are the same mechanism: poll, compare
against last seen, announce transitions. Alerts fire once per threshold
crossing, not once per poll. State is persisted, so a restart does not replay
every milestone the world has ever passed.

**`leaderboard`** — `!!top` playtime rankings (exact — Factorio tracks
`online_time` per player), plus force-wide kill and production totals. Items
crafted and distance walked are deliberately absent: Factorio does not track
them per player, and a made-up number on a leaderboard is worse than no
leaderboard.

**`web_panel`** — a read-only status page on `127.0.0.1:8080`, with JSON at
`/api` and the latest map at `/map.png`. Read-only on purpose: no stop button,
no restore, no console. A page with no auth and no write path cannot be abused
into doing damage. Control from outside the machine goes through Telegram,
which authenticates.

**`map_render`** — `!!map` draws an overview of the world.

Factorio **cannot screenshot a headless server**: `game.take_screenshot` exists
there and accepts the call without complaint, but writes no file, because there
is no renderer in the process. So the map is not captured, it is drawn — one
character per tile comes back from Lua and the picture is composed here, with
terrain, every tree, every ore tile and every built entity at its real position.
A whole 409-chunk world is 421 KB and about half a second at one pixel per tile.

Reading the save directly was considered and rejected: a Factorio save is an
undocumented binary blob, unlike Minecraft's NBT regions that tools like unmined
parse, so there is nothing to read without reverse engineering the format.

The sampling step is chosen from the world size against `max_dimension`, so a
megabase degrades to a coarser map rather than refusing or producing a
hundred-megapixel PNG. Maps reach Telegram as **documents**, not photos —
Telegram recompresses photos, and one pixel per tile is exactly the detail that
destroys.

### Rich text

Factorio chat renders inline tags, and `[gps=x,y,surface]` is **clickable** — it
pings that position on everyone's map. `lua.gps()`, `lua.item_tag()`,
`lua.technology_tag()` and `lua.colored()` build them:

```python
await server.game_print(f"{player} is at {lua.gps(x, y, surface)}")
```

This is what makes `!!here` and `!!warp` genuinely useful rather than a way to
print coordinates. Chart tags complement it: a gps tag says "look here now", a
chart tag says "this place has a name".

## Languages

Everything a person reads goes through a translator. Set `language` in
`config.yml`; `en` and `zh_cn` ship, and anything missing falls back to English
so a partial translation stays usable.

```
!!FR lang                  active language, and what each catalogue is missing
!!FR lang missing zh_cn    the specific keys still to translate
```

To add a language, copy `factorio_reforge/lang/en.yml` to `<code>.yml` beside
it and translate the values. A plugin can ship its own `lang/` directory; its
keys are namespaced under the plugin id, so `server.tr("failed")` inside a
plugin finds `<plugin_id>.failed` and falls through to the core catalogue for
shared strings.

A missing key renders as the key itself rather than as blank text — a visible
`save.restore.confirm` in chat says exactly what to add.

## Tests

```bash
python -m pytest tests/ -q
```

Parser tests run against output sampled from a real server; process tests drive
`tests/fake_factorio.py`, a stand-in that reproduces the real binary's
behaviours — including surviving stdin EOF, which is why FactorioReforge never
closes that pipe.

`scripts/probe_stdout.py` re-runs the original measurement of how a real server
buffers its output. `docs/M0-findings.md` records what it found.

## Layout

```
factorio_reforge/
├── core/      process, handler, info, reactor, rcon, console, server
├── plugin/    manager, registry, interface, events, metadata, builtin commands
├── command/   command tree builder, dispatch, command sources
├── permission/
├── saves/     snapshots and rollback
└── config.py
plugins/       your plugins (telegram_bridge, auto_snapshot ship here)
config/        config.yml, permission.yml, per-plugin config
snapshots/     snapshot zips + index.json
```
