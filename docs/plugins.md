# Bundled plugins

Twenty-one plugins ship in `plugins/`, each a package that owns its code, its
configuration and its translations. All of them can be reloaded, unloaded or
deleted without touching the framework — none is special.

Their commands are listed in [Commands](commands.md); this page is what each one
*is*, and how to configure it. To write your own, see
[Writing plugins](writing-plugins.md).

```
!!FR plugin list       what is loaded, with versions and commands
!!FR help <plugin>     one plugin in detail
!!FR plugin reload <id>
```

Configuration lives in `config/<plugin_id>/config.json`, created with defaults
on first load. Missing keys are filled in from the defaults, so a new setting in
a new version does not force you to edit the file by hand.

---

## Save management

Backups are part of the framework rather than a plugin — `!!qb` is always
there. The slot model, and what a restore actually does, are in
[Backups](architecture.md#backups-and-restoring).

## auto_snapshot

Backs up on an interval, and when the last player leaves.

Its backups go into the **automatic ring** — `a1`, `a2`, listed separately by
`!!qb` — so a schedule can never spend a slot someone made on purpose. Restore
one with `!!qb back a2`.

The timer also skips when nobody is online. With `auto_pause` on, an empty
server's world has not moved, so those backups would be byte-for-byte repeats.

```jsonc
{
  "interval_minutes": 30,
  "on_last_player_left": true,
  "skip_when_empty": true
}
```

## telegram_bridge

Relays chat both ways and gives you the server on your phone.

```jsonc
{
  "token": "",                  // from @BotFather
  "allowed_chat_ids": [],       // nothing works until this is filled in
  "admin_user_ids": [],         // may back up, restore, restart
  "owner_user_ids": [],         // may /cmd
  "forward_chat": true,
  "forward_join_leave": true,
  "forward_death": true
}
```

Getting your chat id: fill in `token`, reload, send the bot a message, and read
the id out of the log — it is rejected and logged, which is exactly what you
need to fill in `allowed_chat_ids`.

`/rollback` always needs a separate `/confirm`. `/cmd`, which runs anything at
all, is `owner` only and confirms first. Unauthorised chats are dropped
silently rather than told they were rejected.

The bridge is also a **service** other plugins register with, so a plugin can be
driven from Telegram without importing `telegram` —
see [Telegram sub-plugins](writing-plugins.md#telegram-sub-plugins).

## mod_manager

Searches, installs, updates and removes mods from the
[mod portal](https://mods.factorio.com), from chat or from Telegram
(`/mods` `/modsearch` `/modinfo` `/modinstall` `/modremove` `/modupdates`).

Credentials come from `config/mod_manager/config.json`, falling back to the
`service-username` and `service-token` in your `~/.factorio/player-data.json`.
Browsing needs no credentials; downloading needs an account that owns the game.
The token is never logged or echoed.

Three things it handles that are easy to get wrong:

**Version filtering.** It asks the binary for its `--version` at load and only
offers releases built for that Factorio. This is not cosmetic: installing flib
0.17.2 (built for 2.1) onto a 2.0.77 server made the server exit with code 1 on
the next start.

**Factorio overwrites `mod-list.json`.** A running server holds the mod list in
memory and writes its own copy out when it stops, discarding anything changed
underneath it. So the plugin records its intent separately and reapplies it on
`on_server_stop`, once the process is actually gone.

**Required dependencies only.** `?` and `(?)` entries are skipped — installing
every optional dependency of a large overhaul mod would pull in dozens of
unrelated ones.

Searching filters a cached copy of the full mod list (~22,500 entries, 13 MB,
~14 s to fetch, refreshed on a TTL) because the portal has no text-search
endpoint. Exact and prefix matches outrank substring hits, with download count
breaking ties.

## map_render

`!!map` draws the world and delivers the image — to the web panel, to Telegram,
or to a file.

Factorio **cannot screenshot a headless server**. `game.take_screenshot` exists
there and accepts the call without complaint, but writes no file, because there
is no renderer in the process. So the map is not captured, it is *drawn*: one
character per tile comes back from Lua and the picture is composed here, with
terrain, every tree, every ore tile and every built entity at its real position.
A whole 409-chunk world is 421 KB and about half a second at one pixel per tile.
The PNG encoder is written against `zlib` and `struct`, so there is no image
library to install.

Reading the save file directly was considered and rejected: a Factorio save is
an undocumented binary blob, unlike Minecraft's NBT regions that tools like
unmined parse, so there is nothing to read without reverse engineering it.

The sampling step is chosen from the world size against `max_dimension`, so a
megabase degrades to a coarser map rather than refusing or producing a
hundred-megapixel PNG.

```jsonc
{
  "max_dimension": 2048,        // pixels; sampling step derives from this
  "send_to_telegram": true
}
```

Maps reach Telegram as **documents**, not photos — Telegram recompresses
photos, and one pixel per tile is exactly the detail that destroys.

## crash_doctor

Keeps a rolling buffer of output and, when the server exits unexpectedly,
matches it against real failure signatures to name the cause *and* the command
that fixes it. On the actual incompatible-mod failure from development:

```
Server exited with code 1: the mod 'flib' could not be loaded
  Incompatible Factorio version (current: 2.0, required: 2.1); Dependency base >= 2.1.0 is not satisfied
  Try: !!mod remove flib
```

Two rules make the matching work. Newest failure first, so a stale error still
in the buffer does not shadow the fresh one. And within one failure block, the
header naming the culprit beats the indented detail lines describing its
symptoms — otherwise every mod failure reports as "incompatible version" without
saying which mod.

It recognises mod load failures, unsatisfied dependencies, a port already in
use, a corrupt save, running out of memory, and a second instance holding the
lock file. `!!why` replays the last diagnosis.

## server_admin

`!!server` reads and edits `server-settings.json` from chat — name, description,
password, player limit, visibility, autosave, pause, verification.

Writes go through a temp file and a rename, because a truncated
`server-settings.json` stops the server from starting at all. Factorio reads the
file once at startup, so every change says a restart is needed rather than
implying it took effect.

`!!server commands true` is refused; see [Commands](commands.md#server-settings--server).

## server_utils

`!!here` `!!info` `!!list` `!!seen` `!!stats` `!!tp`, ported from the
MCDReforged plugins people miss most.

`!!here` sends a clickable `[gps=]` tag, which pings the position on everyone's
map, *and* pins a chart tag so the spot stays marked. See
[Rich text](writing-plugins.md#rich-text).

```jsonc
{
  "enable_teleport": false,
  "teleport_permission": "admin"    // admin | user
}
```

**`!!tp` is off by default.** Teleporting skips the walking, the trains and the
danger the game is built around, so whether a server wants it is the operator's
call, not a default. When it is off the command is never registered at all,
which is a stronger guard than registering it and refusing at call time.

## warp

Named locations, announced as clickable `[gps=]` tags and pinned as chart tags.
**Nobody is teleported** — this is the information half of `!!tp` without the
balance half, which is why it is on by default when `!!tp` is not.

```jsonc
{ "manage_permission": "admin" }    // who may set and delete
```

## blueprints

A server-side shared library. `!!bp save <name>` blueprints the area around you,
`!!bp get <name>` puts it in someone else's inventory.

Entirely server-side, through a scratch inventory, so no client needs anything
installed. Strings are validated on the way in, so a malformed blueprint is
rejected at save time rather than failing later when someone asks for it.

```jsonc
{ "radius": 32, "manage_permission": "user" }
```

## calculator

`==1400/7.5` for arithmetic, and `!!ratio` for the question people actually open
a browser tab to answer: what does it take to build this.

```
!!ratio electronic-circuit 5/s
electronic-circuit at 5/s -- 8.33 machines, 1.25 MW
  3.33 x assembling-machine-3  electronic-circuit  5/s
  5.00 x assembling-machine-3  copper-cable        15/s
  needs: copper-plate 7.5/s, iron-plate 5/s
  output is 0.33 x transport-belt (15/s)
```

**The recipes come from your server, not from this repository.** Every other
Factorio calculator ships a data dump scraped from one version, which is why
they all have a version dropdown and none of them knows about your mods. This
one reads `prototypes.recipe` over RCON, so the numbers are the ones your server
will run. The cost is that `!!ratio` needs the server up; `==` arithmetic does
not.

**The maths is the same as the good calculators do it.** Recipes become a matrix
— rows items, columns recipes, positive for a product and negative for an
ingredient — and the rates are a linear program, solved with a simplex in exact
rational arithmetic. A tree walk is not enough and this is not a hypothetical:
advanced oil processing, heavy cracking and light cracking all produce
overlapping fluids, so how much of each to run is not a property of any one
recipe, and Kovarex consumes the thing it produces, which a walk recurses on
forever. Both fall out of the same solve. See
[Ratios and the solver](architecture.md#ratios-and-the-solver).

**Naming the item is the worst part of every other calculator**, and this one is
running inside the game, so it mostly does not have to be typed:

| What you do | What it plans |
|---|---|
| hover over an assembler, `!!ratio` | the recipe set in that machine |
| hold an item, `!!ratio` | the item in your cursor |
| `!!ratio [item=iron-gear-wheel]` | the icon from the in-game picker |
| `!!ratio green circuit 30/m` | spaces, capitals and short names |

```jsonc
{
  "expression_prefix": "==",
  "announce_expression_results": true,   // everyone saw the question
  "machines": ["assembling-machine-3", "electric-furnace", "chemical-plant",
               "oil-refinery", "centrifuge", "rocket-silo"],
  "belt": "transport-belt",
  "default_rate": "1/s",
  "only_researched": true,               // plan with what this save has unlocked
  "raw_items": ["steam"],                // stop the walk here
  "raw_costs": {"water": 0.01},
  "exclude_categories": ["recycling", "recycling-or-hand-crafting", "parameters"],
  "exclude_patterns": ["-barrel"],
  "max_steps": 14
}
```

**`only_researched` is the setting that makes the answers usable**, and it was
added because of what a live Space Age server did without it. Asked for
electronic circuits, the solver answered *casting from molten iron in a
foundry*, which is genuinely cheaper per ore — and unreachable until you have
been to Vulcanus. Asked for petroleum it built a Gleba bioflux chain to make
sulfur. Restricting to `force.recipes[name].enabled` turns "theoretically
optimal" into "what you can go and build". Add `all=1` to a question to plan
with everything; a plan is reported as assuming unresearched recipes when it
had to fall back.

The other three defaults are all lessons from the same run:

- **`exclude_categories`** — 310 of the 659 recipes on that server are
  recycling, and each lists what it shreds as a product. Left in, the cheapest
  way to make a circuit is to recycle scrap.
- **`raw_items: ["steam"]`** — steam comes from a boiler, and a boiler is not a
  recipe. The only recipe that makes steam is acid neutralisation, so the solver
  cheerfully built a sulfuric acid chain to get steam as a byproduct.
- **`raw_costs`** — the one number that is a judgement rather than a fact from
  the game, and unavoidable: cracking heavy oil and pumping more crude both
  produce petroleum, and which is better depends on your map. Charging water the
  same as crude oil makes the solver refuse to crack at all.

Arithmetic is evaluated by walking the parsed syntax tree against a whitelist of
node types. `eval` is never called on player input — not sandboxed, not with
`__builtins__` stripped, not at all — because the input comes from anyone who
can type in chat. What is left is size rather than access, so `9**9**9` is
refused before it runs rather than after.

Beacons are deliberately not modelled. 2.0 gives beacons a diminishing `profile`
by count, and a beacon number that is quietly wrong is worse than one the
calculator never claimed to know; `speed=` takes a figure you worked out
yourself.

## ups_watch

`!!ups` — the update rate, and `!!ups why` for what is eating it.

Factorio does not fail by crashing, it fails by getting slower: 60 UPS becomes
55, then 40, and by the time someone says "the game feels laggy" it has been
declining for a week. There is no UPS API, so this samples `game.tick` against
the wall clock — the difference over a known interval *is* the update rate.

Two rules make it something people read rather than mute:

- **A paused server is not a slow one.** Measured on 2.0.77, an idle server with
  `auto_pause` reads **0.5 ticks/s**. A sample taken with nobody connected is
  discarded rather than announced as a collapse at four in the morning.
- **The window is judged by its median, not its mean.** An autosave or a
  chunk-generation burst dips exactly one sample; 60, 60, 30, 60, 60 averages to
  54 and would be reported as a slow factory, while its median is 60.

```jsonc
{
  "sample_interval_seconds": 60,
  "warn_below_ups": 55.0,
  "critical_below_ups": 45.0,
  "window": 5,
  "history_length": 240
}
```

## alerts

`!!alerts` — attacks, and the game's own alerts.

Two detectors, because Factorio's alert system belongs to *players*:
`player.get_alerts` is per-player and only meaningful while someone is
connected. That covers the case where somebody is already looking at the screen
and misses the one worth waking up for.

- **Alerts** are read from connected players and relayed, de-duplicated by
  chunk — the same turret reports at slightly different coordinates each time.
- **Losses** come from counting the force's structures by type on a timer.
  Nothing destroys forty walls except an attack, a count is cheap, and it works
  with the server empty and no companion mod. It cannot say what attacked; it
  can say "you lost 40 walls and 6 turrets", which is the sentence that gets
  somebody to log in.

Belts and other things players remove constantly are not counted, and a single
disappearance is not reported — that is somebody rebuilding.

```jsonc
{
  "poll_interval_seconds": 60,
  "loss_threshold": 5,
  "watch_types": ["wall", "gate", "ammo-turret", "..."],
  "ignore_alerts": ["no_material_for_construction"]
}
```

## trains

`!!trains` for the network, `!!trains stuck` for the one holding it up.

On any base past a handful of trains, "something is not arriving" is a weekly
question with two usual answers: a train with no path, or one that has been
sitting at a station long enough that it is not coming back. Both are in
`train.state`; neither is visible without walking the rails.

No-path is wrong immediately and reported immediately. Waiting states are normal
briefly and reported only after `stuck_after_minutes`, and the clock resets when
a train changes state — a train that moved is not stuck even if it stops again.

`force.get_trains()` was removed in 2.0 — measured, it raises *"LuaForce doesn't
contain key get_trains"*. This uses `game.train_manager.get_trains{}`.

```jsonc
{ "poll_interval_seconds": 120, "stuck_after_minutes": 15, "max_reported": 5 }
```

## power

`!!power` — accumulator charge and the supply gap.

A Factorio power failure is quiet: accumulators drain, machines slow rather than
stop, and the first visible symptom is research taking longer than it should.
Charge at dawn is the whole story on a solar base, so that is what is watched,
summed in Lua against the prototype's `buffer_capacity` (5 MJ, measured) so a
megabase costs one query rather than one per accumulator.

Thresholds report on the way down and again on the way back up, once each — a
base hovering at 29% is not news every two minutes.

```jsonc
{ "poll_interval_seconds": 120, "charge_thresholds": [0.3, 0.1] }
```

## research

`!!research` shows what the labs are on; `add`, `cancel` and `search` change it.

The queue is trivially editable through the API and impossible to reach from a
mine or a phone, which is the entire gap this fills. A technology whose
prerequisites are missing is refused *by the game* and the refusal is relayed
verbatim, rather than predicted here and predicted wrong.

`research_queue_enabled` does not exist on 2.0.77 — it was a 1.1 property.

```jsonc
{ "manage_permission": "helper", "announce_changes": true }
```

## vote

`!!vote start <question>`, then `!!vote yes` / `!!vote no`.

Restarting, restoring and turning the lights off are things one admin can do and
several players have to live with. The counting rules are the plugin:

- **Only players online when it started may vote.** Someone who joined midway
  did not hear the question, and letting them in lets a vote be won by inviting
  friends.
- **It ends as soon as the outcome is settled**, rather than running out a timer
  whose result is already decided — that is how people learn to ignore votes.
- **Silence is a no.** A quorum of "most of the people here" only means
  something if not answering counts as not agreeing.

A finished vote emits `vote.finished` and does nothing else. Wiring "passed" to
an actual restart is a separate and deliberate choice, not a default.

```jsonc
{ "duration_seconds": 120, "majority": 0.5, "minimum_voters": 2 }
```

## mail

`!!mail <player> <message>` — leave a message for someone who is offline.

Servers spread across timezones lose most of what people say to each other. The
mailbox is the oldest fix there is and costs almost nothing here, since the join
event and per-plugin storage already exist.

Delivery waits a few seconds after a join: a player who has just connected is
looking at a loading screen, and a message printed then scrolls past behind the
join spam. Messages to someone who *is* online are delivered at once — waiting
for them to reconnect to hear something said while they were standing there
would be absurd. A full mailbox drops its oldest message rather than refusing
the newest, because a full mailbox is usually someone who has not logged in for
a month.

```jsonc
{ "deliver_after_seconds": 8, "max_per_player": 20, "max_length": 200 }
```

## production

Samples `get_flow_count` on a timer to build production history that outlives a
session — Factorio's own production graph is per-client and starts empty every
time you connect.

Renders as a Unicode sparkline in chat and as an SVG in the web panel; no
plotting library either way.

```jsonc
{
  "items": ["iron-plate", "copper-plate", "electronic-circuit"],
  "sample_interval_minutes": 5,
  "history_length": 288
}
```

## world_watch

Evolution and pollution alerts, plus research and rocket milestones. One plugin
because both are the same mechanism: poll, compare against last seen, announce
transitions.

Alerts fire once per **threshold crossing**, not once per poll — evolution
sitting at 51% is not news every five minutes. State is persisted, so a restart
does not replay every milestone the world has ever passed.

```jsonc
{
  "evolution_thresholds": [0.25, 0.5, 0.75, 0.9],
  "pollution_thresholds": [10000, 50000],
  "announce_research": true,
  "announce_rockets": true,
  "poll_interval_minutes": 5
}
```

## leaderboard

`!!top` playtime rankings — exact, because Factorio tracks `online_time` per
player — plus force-wide kill and production totals.

Items crafted and distance walked are deliberately absent: Factorio does not
track them per player, and a made-up number on a leaderboard is worse than no
leaderboard.

## join_motd

Greets players on join with a message built from live data.

```jsonc
{
  "message": "Welcome {player}! {online}/{total} online, day {day}, evolution {evolution}"
}
```

Placeholders: `{player} {online} {total} {uptime} {day} {evolution} {pollution}
{research} {snapshots} {last_snapshot}`.

## web_panel

A read-only status page on `127.0.0.1:8080`, with JSON at `/api`, the latest map
at `/map.png` and production charts.

Read-only on purpose: no stop button, no restore, no console. A page with no
authentication and no write path cannot be abused into doing damage. Control
from outside the machine goes through Telegram, which authenticates.

```jsonc
{ "host": "127.0.0.1", "port": 8080 }
```

Keep the host on localhost and reach it over an SSH tunnel
(`ssh -L 8080:127.0.0.1:8080 you@server`). Binding `0.0.0.0` publishes your
world map and player list to anyone who finds the port.
