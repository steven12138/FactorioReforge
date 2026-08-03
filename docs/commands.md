# Command reference

Every `!!` command works from three places — the FactorioReforge console, the
in-game chat box, and (for the ones the bridge exposes) Telegram. Anything that
does *not* start with `!!` typed into the console goes straight to Factorio's
stdin, so `/players` and `/promote alice` work exactly as they always did.

The commands each plugin registers are also discoverable at runtime:

```
!!FR help              every command, grouped by the plugin that provides it
!!FR help warp         one plugin: version, author, what it does, its commands
!!FR plugin list       every plugin with its version and commands
```

## Permission levels

`guest(0) → user(1) → helper(2) → admin(3) → owner(4)`, stored in
`config/permission.yml`. New players get `permission.default_level` from
`config.yml`, which is `user`.

The FactorioReforge console is always `owner`, and not configurably so: whoever
holds that terminal can already stop the process, so pretending otherwise would
be theatre. In Telegram, level comes from the user id lists in the bridge's
config.

A command you may not run is reported as such. A command that does not exist at
all — like `!!tp` when teleporting is disabled — says it does not exist, because
that is the truth.

## Framework — `!!FR`

| Command | Level | Does |
|---|---|---|
| `!!FR help` | guest | Every command, grouped by plugin |
| `!!FR help <plugin>` | guest | One plugin in detail |
| `!!FR status` | user | Server, RCON, plugin and backup state |
| `!!FR plugin list` | admin | Loaded plugins; marks any whose files changed |
| `!!FR plugin reload <id>` | admin | Reload one plugin |
| `!!FR plugin unload <id>` | admin | Unload one plugin |
| `!!FR reload` | admin | Reload every plugin whose files changed |
| `!!FR server start\|stop\|restart` | admin | Factorio's lifecycle |
| `!!FR server kill` | owner | SIGKILL — loses everything since the last save |
| `!!FR permission list` | admin | Who has what |
| `!!FR permission set <player> <level>` | owner | Change someone's level |
| `!!FR lang` | user | Active language and what each catalogue is missing |
| `!!FR lang missing <code>` | user | The specific keys still to translate |
| `!!FR lang set <code>` | admin | Switch language, immediately and persistently |
| `!!FR exit` | owner | Stop the server, then quit FactorioReforge |

## Backups — `!!save`

The slot model and what a restore actually does are in
[Backups](architecture.md#backups-and-restoring).

| Command | Level | Does |
|---|---|---|
| `!!save` / `!!save list` | guest | The slots, with age, size and comment |
| `!!save make [comment]` | user | Back up into slot 1; everything else shifts down |
| `!!save back [slot]` | helper | Stage a restore (slot 1 by default) — does nothing yet |
| `!!save confirm` | user | Perform the staged restore, after a countdown |
| `!!save abort` | user | Cancel, whether staged or already counting down |
| `!!save del <slot>` | helper | Delete one slot |
| `!!save rename <slot> <comment>` | helper | Re-comment a slot |

`!!save back` never restores on its own. It stages, then `!!save confirm`
counts down in chat, and anyone may `!!save abort` during the countdown.

## Server settings — `!!server`

Reads and writes `server-settings.json`. Factorio reads that file once at
startup, so every change tells you a restart is needed.

| Command | Level | Does |
|---|---|---|
| `!!server` / `!!server show` | user | Current settings |
| `!!server name <text>` | admin | Rename the server |
| `!!server description <text>` | admin | Change the description |
| `!!server password [text]` | admin | Set the join password; no argument clears it |
| `!!server maxplayers <n>` | admin | 0 for unlimited |
| `!!server public on\|off` | admin | List on Factorio's public server browser |
| `!!server lan on\|off` | admin | Announce on the local network |
| `!!server autosave <minutes>` | admin | Autosave interval |
| `!!server pause on\|off` | admin | Pause when nobody is online |
| `!!server verify on\|off` | admin | Require factorio.com account verification |
| `!!server commands <value>` | owner | **Refuses `true`** — see below |

`!!server commands true` would let every player run `/c`, which permanently
marks the world as cheated. That is a decision to stop playing the game rather
than a server setting, and not one to reach by typing a word in chat, so it is
refused. Edit the file by hand if you mean it.

## Players and the world

| Command | Level | Does |
|---|---|---|
| `!!here` | user | Announce your position as a clickable map ping, and pin it |
| `!!list` | user | Who is online, with playtime |
| `!!info [player]` | user | Playtime, permission level, position |
| `!!seen <player>` | user | Playtime, and when they were last online |
| `!!stats` | user | Evolution, pollution, research, world uptime |
| `!!tp <player> <target\|x y>` | configurable | Teleport — **disabled by default** |
| `!!top [time\|kills\|built]` | user | Rankings |
| `!!watch` | user | Evolution, pollution, research and rocket status |

`!!tp` is off unless you set `enable_teleport: true` in
`config/server_utils/config.json` — teleporting skips the walking, the trains
and the danger the game is built around. See
[`server_utils`](plugins.md#server_utils).

## Named locations — `!!warp`

Locations, not teleports: **nobody is moved**.

| Command | Level | Does |
|---|---|---|
| `!!warp` / `!!warp list` | user | Every named location |
| `!!warp <name>` | user | Announce one as a clickable map ping |
| `!!warp set <name>` | configurable | Name your current position |
| `!!warp del <name>` | configurable | Remove one |

## Blueprints — `!!bp`

| Command | Level | Does |
|---|---|---|
| `!!bp list` | user | The library |
| `!!bp info <name>` | user | Size, contents, who saved it |
| `!!bp get <name>` | user | Put it in your inventory |
| `!!bp save <name>` | configurable | Blueprint the area around you |
| `!!bp del <name>` | configurable | Remove one |

## Mods — `!!mod`

| Command | Level | Does |
|---|---|---|
| `!!mod search <query>` | user | Search the mod portal |
| `!!mod info <name>` | user | Details and dependencies |
| `!!mod list` | user | What is installed |
| `!!mod install <name> [version]` | admin | Download, install and enable |
| `!!mod remove <name>` | admin | Delete it |
| `!!mod enable\|disable <name>` | admin | Without deleting it |
| `!!mod updates` | admin | What has a newer release |
| `!!mod refresh` | admin | Re-fetch the portal's mod index |

Only releases built for your Factorio version are offered — see
[`mod_manager`](plugins.md#mod_manager).

## Calculator — `!!ratio`

Arithmetic, and production ratios solved from the recipes your server is
actually running. See [`calculator`](plugins.md#calculator).

| Command | Level | What it does |
|---|---|---|
| `==<expression>` | user | Arithmetic in chat: `==1400/7.5` |
| `!!calc <expression>` | user | The same, from the terminal or Telegram |
| `!!ratio [item] [rate]` | user | Machines, inputs, belts and power to build it |
| `!!ratio refresh` | helper | Forget the cached recipe data and re-read it |
| `!!recipe [item]` | user | One recipe: time, ingredients, per-machine rate |
| `!!belt <rate>` | user | How many belts of each tier a rate needs |

With no item, `!!ratio` and `!!recipe` use **the machine you are hovering over**
— its set recipe — or failing that whatever is in your cursor. An icon pasted
from the in-game picker (`[item=iron-plate]`) works as an argument, and so do
spaces, capitals and short names: `!!ratio green circuit 30/m`.

Options go anywhere on the line as `key=value`:

| Option | Effect |
|---|---|
| `30/m`, `5/s`, `90/h` | The rate. Per second if the unit is left off |
| `machine=foundry` | Prefer a machine, ahead of the configured list |
| `prod=20` `speed=50` | Module effects as percentages |
| `modules=speed-module-3*4` | The same, read from the module's real prototype |
| `raw=iron-plate` | Stop expanding here and treat it as bought in |
| `use=advanced-oil-processing` | Pin a recipe where several would do |
| `cost:water=0.5` | Change what a raw input is worth to the solver |

## Production, maps and diagnostics

| Command | Level | Does |
|---|---|---|
| `!!prod [item]` | user | Production rate with a sparkline of its history |
| `!!prod top` | user | What is being produced most |
| `!!prod watch <item>` | admin | Start sampling another item |
| `!!map` | user | Render the world and deliver the image |
| `!!autosnap` | helper | Automatic-backup status |
| `!!autosnap now` | helper | Back up immediately |
| `!!why` | admin | Why the server last exited unexpectedly |
| `!!web` | admin | The web panel's address |

## Telegram

Sent to the bot, not typed in game. Level comes from the id lists in
`config/telegram_bridge/config.json`.

| Command | Level | Does |
|---|---|---|
| `/status` `/players` | viewer | Server state; who is online |
| `/say <message>` | viewer | Speak in game |
| `/save [comment]` `/saves` | admin | Back up; list slots |
| `/rollback <slot>` → `/confirm` | admin | Restore, always with a second step |
| `/restart` `/stopserver` `/startserver` | admin | Lifecycle |
| `/cmd <raw>` | owner | Run anything, with confirmation |
| `/mods` `/modsearch` `/modinfo` | viewer | Browse mods |
| `/modinstall` `/modremove` `/modupdates` | admin | Change mods, with confirmation |

Plugins can add their own Telegram commands without importing `telegram` —
see [Telegram sub-plugins](writing-plugins.md#telegram-sub-plugins).
