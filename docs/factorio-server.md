# Running a Factorio multiplayer server

This page is about Factorio headless servers on their own — no FactorioReforge
involved. If you already run one, skip to [Configuration](configuration.md).
If you want the guided version, the [tutorial](TUTORIAL.md) covers the same
ground with FactorioReforge on top.

## Ways to play together

| Approach | Good for | Notes |
|---|---|---|
| Host from the game client | A quick session with friends | Ends when the host quits; nothing can manage it |
| **A headless server** | Anything long-lived | No graphics, no audio, runs from a command line |
| Public listing | Being found by strangers | `visibility.public` registers you with Factorio's matching server |

Only a headless server can be managed by anything, which is what the rest of
this project is about.

## Install the headless server

The headless build is a **separate download** from the game client — no account
needed, and it does not contain DLC data.

```bash
mkdir -p server && cd server
curl -L -o factorio-headless.tar.xz "https://factorio.com/get-download/stable/headless/linux64"
tar -xJf factorio-headless.tar.xz
./factorio/bin/x64/factorio --version
```

Keep it away from `~/.factorio/`, which belongs to your game client. Mixing them
means a client update can change what your server runs.

```
factorio/
├── bin/x64/factorio               the binary
├── data/                          base game data, plus server-settings.example.json
├── saves/                         worlds, one .zip each
├── mods/                          server-side mods
└── config/config.ini
```

Players must have the **same mods and the same DLC** as the server, or they
cannot connect.

## Create a map

```bash
cd factorio
./bin/x64/factorio --create ./saves/reforge.zip
```

Optionally with `--map-gen-settings ./map-gen-settings.json` and
`--map-settings ./map-settings.json`, both of which have examples in `data/`.

## server-settings.json

```bash
cp data/server-settings.example.json ./server-settings.json
```

The fields that actually matter:

```jsonc
{
  "name": "My Server",
  "description": "",
  "max_players": 0,                    // 0 = unlimited

  "visibility": { "public": false, "lan": true },
  "username": "",                      // required when public; from factorio.com
  "token": "",                         // in ~/.factorio/player-data.json

  "game_password": "",
  "require_user_verification": true,   // checks accounts against factorio.com

  "allow_commands": "admins-only",     // true | false | admins-only
  "autosave_interval": 10,             // minutes
  "autosave_slots": 5,
  "auto_pause": true,                  // pause when nobody is online
  "non_blocking_saving": true          // save without stalling the game
}
```

**`allow_commands: true` lets every player run `/c`**, which permanently marks
the save as cheated and disables achievements for good. `admins-only` is the
sane default. FactorioReforge refuses to set this to `true` from chat and warns
at startup when it is already on.

**`non_blocking_saving: true`** is worth setting on any server people actually
play on. Without it, everyone freezes for the length of every save.

Three list files sit beside it, each a JSON array of player names:

```bash
echo '["your_factorio_name"]' > server-adminlist.json
echo '[]' > server-whitelist.json
echo '[]' > server-banlist.json
```

## Start it

```bash
./bin/x64/factorio \
  --start-server ./saves/reforge.zip \
  --server-settings ./server-settings.json \
  --server-adminlist ./server-adminlist.json \
  --server-banlist  ./server-banlist.json \
  --port 34197 \
  --rcon-bind 127.0.0.1:27015 --rcon-password 'CHANGE_ME'
```

| Option | Does |
|---|---|
| `--start-server FILE` | Load one specific world |
| `--start-server-load-latest` | Load whichever save is newest — see the warning below |
| `--start-server-load-scenario [MOD/]NAME` | Start from a scenario |
| `--server-settings` / `--server-adminlist` / `--server-whitelist` / `--server-banlist` | The files above |
| `--port N` | Game port, **UDP**, default 34197 |
| `--rcon-bind ADDR:PORT` | RCON listener — always give it an address |
| `--rcon-port N` | RCON on **all interfaces**; prefer `--rcon-bind` |
| `--console-log FILE` | A second copy of the console output, chat included |
| `--mod-directory PATH` | Use mods from somewhere else |

> **`--start-server-load-latest` and backups do not mix.** Restoring writes your
> world file, but an autosave written since is *newer*, so the server comes back
> on the wrong map. FactorioReforge refuses to start with this flag.

> **Use `--rcon-bind 127.0.0.1:27015`, not `--rcon-port 27015`.** RCON is
> plaintext and unencrypted, and reaching the port *is* controlling the server —
> `--rcon-port` binds every interface, including your public one. FactorioReforge
> refuses to start with `--rcon-port`, or with a `--rcon-bind` address that is
> not local.

## Networking

- The game port is **34197/UDP** — not TCP. Forward it and open your firewall
  (`sudo ufw allow 34197/udp`) for public play.
- RCON is **27015/TCP**, and belongs on localhost only.
- LAN: `visibility.lan` makes the server appear in the multiplayer browser on
  the same network, with no configuration on either side.
- Public: `visibility.public` plus `username` and `token` lists you on
  Factorio's matching server, which also arranges NAT punch-through.
- Direct: **Multiplayer → Connect to address → `IP:34197`**.

## The console

**Standard input is the in-game chat box.** Anything you type is said by
`<server>`; anything starting with `/` is a command run as the server.

| | |
|---|---|
| `/players` `/admins` `/version` `/time` `/seed` | Ask about the server |
| `/promote` `/demote` `/kick` `/ban` `/unban` `/mute` `/whitelist add\|remove` | Manage players |
| `/server-save [name]` | Save now; with a name, save to a *different* file |
| `/quit` | Save and shut down cleanly |
| `/sc <lua>` | Run Lua silently, no cheat flag |
| `/c <lua>` | Run Lua **and mark the save cheated, permanently** |

Standard output is two formats mixed together, which matters if you ever parse
it:

```
   0.001 2026-08-02 14:02:11; Factorio 2.0.77 (build 84115, linux64, headless)
   1.234 Info ServerMultiplayerManager.cpp:791: updateTick(4) changing state from(CreatingGame) to(InGame)
2026-08-02 14:02:31 [JOIN] Alice joined the game
2026-08-02 14:02:48 [CHAT] Alice: hello
2026-08-02 14:03:02 [DEATH] Bob was killed by small-biter
```

Engine lines are `<seconds since start> <Level> <file>:<line>: <text>`. Game
events are `<date> <time> [TAG] <text>`, with tags including `JOIN` `LEAVE`
`CHAT` `SHOUT` `DEATH` `KICK` `BAN` `COMMAND` `WARNING`. There are two more
shapes than that; [Factorio notes](factorio-notes.md) has all four as measured.

## Saves and restoring

Autosaves cycle through `saves/_autosave1.zip` … `_autosave5.zip`, overwriting
the oldest each time. They are not backups: five saves at ten-minute intervals
means the mistake you want to undo is gone in under an hour.

**Factorio cannot swap worlds while running.** Restoring is: stop the server,
put the file in place, start it again. That physical constraint is why
FactorioReforge's restore is an orchestrated sequence rather than a file copy —
see [Backups](architecture.md#backups-and-restoring).

`/server-save <name>` is the useful one for making backups by hand: it writes a
**separate, complete** save and leaves the live world alone.
