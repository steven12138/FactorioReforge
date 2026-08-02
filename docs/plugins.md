# Bundled plugins

Thirteen plugins ship in `plugins/`, each a package that owns its code, its
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

Backups are part of the framework rather than a plugin — `!!save` is always
there. The slot model, and what a restore actually does, are in
[Backups](architecture.md#backups-and-restoring).

## auto_snapshot

Backs up on an interval, and when the last player leaves.

The timer skips when nobody is online. With `auto_pause` on, an empty server's
world has not moved, so those backups would be byte-for-byte repeats filling
slots that hold real history.

```jsonc
{
  "interval_minutes": 60,
  "on_last_player_leave": true,
  "comment": "auto"
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
