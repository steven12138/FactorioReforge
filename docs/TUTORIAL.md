# FactorioReforge — step-by-step tutorial

From nothing to a running, managed, remotely-controllable Factorio server.

Every command here was run on a real machine (Arch Linux, Factorio 2.0.77,
Python 3.14). If something in your output differs from what is shown, that is
worth stopping on rather than pushing past.

**中文版本：[TUTORIAL_zh.md](TUTORIAL_zh.md)**

---

## Contents

1. [What you need first](#1-what-you-need-first)
2. [Install everything with one script](#2-install-everything-with-one-script)
3. [First start](#3-first-start)
4. [Connect from the game](#4-connect-from-the-game)
5. [Make yourself an admin](#5-make-yourself-an-admin)
6. [Snapshots and rolling back](#6-snapshots-and-rolling-back)
7. [Installing mods](#7-installing-mods)
8. [Control it from Telegram](#8-control-it-from-telegram)
9. [The web panel](#9-the-web-panel)
10. [Opening the server to the internet](#10-opening-the-server-to-the-internet)
11. [Running it unattended](#11-running-it-unattended)
12. [Writing your first plugin](#12-writing-your-first-plugin)
13. [When something breaks](#13-when-something-breaks)

---

## 1. What you need first

| Requirement | Why | Check |
|---|---|---|
| Linux x86-64 | The headless build is Linux-only here | `uname -m` → `x86_64` |
| Python 3.11+ | The framework | `python3 --version` |
| `curl`, `tar`, `xz` | Downloading and unpacking the server | `curl --version` |
| ~2 GB free disk | Server, saves, snapshots | `df -h .` |
| A factorio.com account | Only needed to install mods or go public | — |

You do **not** need to own Factorio to run the headless server. You do need an
account that owns it to download mods from the portal.

On Debian/Ubuntu, if `python3 -m venv` complains:

```bash
sudo apt install python3-venv python3-pip curl xz-utils
```

---

## 2. Install everything with one script

```bash
git clone git@github.com:steven12138/FactorioReforge.git
cd FactorioReforge
./scripts/install.sh
```

That single script does all of this:

1. Checks your Python is 3.11 or newer, and that `venv` works
2. Downloads the Factorio headless server (~21 MB) into `server/`
3. Writes `server/factorio/server-settings.json` and the admin/white/ban lists
4. Creates a fresh map at `server/factorio/saves/reforge.zip`
5. Builds `.venv` and installs FactorioReforge with its optional extras
6. **Generates a random RCON password** and writes a matching `config.yml`
7. Validates the config and refuses to finish if it is wrong

Expected output, abbreviated:

```
==> Checking prerequisites
    Python: Python 3.14.6 at /usr/bin/python3
==> Downloading Factorio headless (stable)
==> Extracting
    Version: 2.0.77 (build 84539, linux64, headless)
==> Setting up server configuration
    generated a random RCON password
    wrote server-settings.json
==> Creating a map (this takes a moment)
    created saves/reforge.zip
==> Building the Python environment
    installed FactorioReforge and its optional extras
==> Writing FactorioReforge configuration
    wrote config.yml
==> Validating the configuration
    config.yml is valid

Setup complete.
```

Useful flags:

```bash
./scripts/install.sh --yes                 # never prompt
./scripts/install.sh --version 2.0.77      # pin a Factorio version
./scripts/install.sh --no-server           # you already have a headless install
./scripts/install.sh --port 34500          # different game port
./scripts/install.sh --force               # redo everything, overwriting
```

Re-running without `--force` is safe: it keeps whatever is already there and
never overwrites your `config.yml` without asking.

### If you prefer to do it by hand

The script is not magic; the manual equivalent is in the
[README](../README.md#part-1--running-a-factorio-multiplayer-server).

---

## 3. First start

```bash
./scripts/run.sh
```

You should see the plugins load, then the server come up:

```
[INFO] [reforge] Loaded 12 plugin(s)
[INFO] [reforge] Starting server: ./bin/x64/factorio --start-server ... --rcon-password <redacted>
[INFO] [reforge] Server started, pid=34246
[INFO] [reforge] Server startup complete
[INFO] [reforge] RCON connected to 127.0.0.1:27015

  FactorioReforge 0.1.0
  Type !!FR help for commands. Anything else goes to the Factorio console.
```

Two lines matter most:

- **`Server startup complete`** — the map is loaded, players can connect.
- **`RCON connected`** — the query channel is up. Until this appears, commands
  that read data back (`!!stats`, `!!list`) will say RCON is not connected.

Now type into the same terminal:

```
!!FR status
```

```
FactorioReforge 0.1.0 - up 12s
Server: running (pid 34246, up 12s)
RCON: connected
Plugins: 12 loaded
Snapshots: 0
Online (0): -
```

**The rule for this terminal:** lines starting with `!!` are FactorioReforge
commands. Everything else is passed straight to Factorio, exactly as if you had
typed it into the game's chat. So `/players` works, and plain text broadcasts to
everyone in game.

Stop with `Ctrl-C` (which saves, then exits) or `!!FR exit`.

---

## 4. Connect from the game

In the Factorio client: **Multiplayer → Connect to address**

| Where the client is | Address |
|---|---|
| Same machine | `127.0.0.1:34197` |
| Same LAN | `<server LAN ip>:34197` |
| Over the internet | see [section 10](#10-opening-the-server-to-the-internet) |

Once you join you will see FactorioReforge notice it:

```
2026-08-02 10:05:08 [JOIN] YourName joined the game
```

and `join_motd` will greet you with live server statistics.

Now try the in-game commands. Type in the game chat:

```
!!here
```

Everyone sees a **clickable coordinate** that pings your position on their map,
and a marker is pinned there permanently.

```
!!list       who is online
!!stats      evolution, pollution, research
!!info       your playtime and permissions
```

---

## 5. Make yourself an admin

There are **two separate permission systems**, and confusing them is the most
common early mistake.

| System | Governs | Set with |
|---|---|---|
| Factorio's own admin list | `/kick`, `/ban`, cheat commands | `server/factorio/server-adminlist.json` |
| FactorioReforge permissions | `!!` commands | `config/permission.yml` or `!!FR permission set` |

**Factorio admin** — edit the file and restart:

```bash
echo '["YourFactorioName"]' > server/factorio/server-adminlist.json
```

**FactorioReforge admin** — from the FactorioReforge console (which is always
`owner`):

```
!!FR permission set YourFactorioName admin
```

Levels are `guest(0) user(1) helper(2) admin(3) owner(4)`. New players default
to `user`. To check:

```
!!FR permission list
```

---

## 6. Snapshots and rolling back

This is the feature most worth understanding before you need it.

### Take a snapshot

```
!!save make before the big refactor
```

```
Saving and snapshotting...
Created #1 2026-08-02 10:12:03 by console (24.8 MiB) - before the big refactor
```

It asks the server to write the map to disk, **waits for the completion
message**, then copies the file. That wait is why the snapshot contains the
current world instead of whatever autosave last happened to write.

### List them

```
!!save list
```

### Roll back

Rolling back is two steps on purpose:

```
!!save back 1
```

```
About to roll back to #1 2026-08-02 10:12:03 by console (24.8 MiB) - before the big refactor
This stops the server and replaces the current world. Type '!!save confirm' within 60s to proceed.
```

```
!!save confirm
```

What then happens, in order:

1. Verify the snapshot is a valid zip
2. Broadcast a countdown in game
3. **Snapshot the current world first** — so rolling back to the wrong point is
   recoverable
4. Stop the server and wait for the process to actually exit
5. Replace the save via a temp file and rename
6. Start the server again
7. If it does not come back up, restore the step-3 snapshot and say so

If step 3 fails, the whole rollback is refused. A rollback with no way back is a
one-way door, and this deliberately will not walk through one.

### Automatic snapshots

`auto_snapshot` runs every 30 minutes by default, and once more when the last
player leaves. Edit `config/auto_snapshot/config.json`, then:

```
!!FR plugin reload auto_snapshot
```

Retention lives in `config.yml` under `saves:` — `max_snapshots` and
`max_snapshot_age_days`. **Only automatic snapshots are rotated away**; if you
typed a comment, you meant to keep it.

---

## 7. Installing mods

```
!!mod search krastorio
```

```
Searching the portal for 'krastorio'...
8 result(s):
  Krastorio 2 (Krastorio2) v2.1.2 by raiguard - 385,068 downloads
  Krastorio 2 Assets (Krastorio2Assets) v2.1.0 by raiguard - 405,910 downloads
  ...
```

The first search of the day fetches the full mod index (~22,500 mods, 13 MB,
about 14 seconds) and caches it. Later searches are instant.

```
!!mod info Krastorio2      details and dependencies
!!mod install flib         download, resolve dependencies, enable
!!mod list                 what is installed
!!mod updates              what has a newer release
!!mod remove flib
```

### Credentials

Downloading needs a factorio.com account that owns the game. The plugin reads
`service-username` and `service-token` from your `~/.factorio/player-data.json`
automatically. If FactorioReforge runs as a different user, set them in
`config/mod_manager/config.json` instead.

### Three things it protects you from

**Version mismatch.** It asks the binary its version and only offers releases
built for it. This is not cosmetic: installing flib 0.17.2 (built for 2.1) onto
a 2.0.77 server makes the server exit with code 1 on the next start.

**Factorio overwriting your changes.** A running server holds the mod list in
memory and rewrites `mod-list.json` when it stops, discarding anything changed
underneath it. The plugin records its intent separately and reapplies it once
the process is actually gone.

**Optional-dependency avalanche.** Only required dependencies are installed —
`?` and `(?)` entries are skipped, or a large overhaul mod would drag in dozens
of unrelated mods.

### After installing

```
!!FR server restart
```

**Mods only load at startup, and every player needs the same mod set.**
Installing a mod on a live public server locks out everyone who does not have
it. Announce it first.

---

## 8. Control it from Telegram

### Create the bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, pick a name and a username
3. Copy the token it gives you — it looks like `123456789:AAF...`

### Configure

Edit `config/telegram_bridge/config.json`:

```json
{
  "enabled": true,
  "token": "123456789:AAF-your-token-here",
  "allowed_chat_ids": [],
  "admin_user_ids": [],
  "owner_user_ids": [],
  "forward_chat": true,
  "forward_join_leave": true
}
```

Reload it:

```
!!FR plugin reload telegram_bridge
```

### Find your chat id

Send the bot any message. It will ignore you — deliberately, since answering an
unknown chat confirms the bot exists — but it logs the id:

```
[INFO] telegram_bridge ignored a message from chat 123456789; add it to allowed_chat_ids
```

Put that number in `allowed_chat_ids`, and your own user id in `admin_user_ids`
(for a private chat they are the same number). Reload again.

### Use it

```
/status        server state, online players, evolution
/players
/say hello     send a message into the game
/save          snapshot
/saves         list snapshots
/rollback 3    asks for confirmation with buttons
/restart
/mods          installed mods
/modsearch bob
/modinstall flib
```

Chat is relayed both ways: what players say reaches Telegram, and what you type
in Telegram appears in game as `[TG] YourName: ...`.

You also get pushed alerts you did not ask for, which are the point:

- 🔥 the server exited unexpectedly — **with a diagnosis of why**
- ⚠️ evolution crossed a threshold
- 🚀 a rocket launched
- ♻️ a rollback finished

### Permission levels

| Level | Who | Can do |
|---|---|---|
| `viewer` | anyone in an allowed chat | `/status` `/players` `/say` `/saves` |
| `admin` | `admin_user_ids` | `/save` `/rollback` `/restart` `/modinstall` |
| `owner` | `owner_user_ids` | `/cmd` — runs any command at all |

Everything destructive asks for confirmation with inline buttons.

---

## 9. The web panel

Already running at **http://127.0.0.1:8080**, with JSON at `/api`.

```
!!web
```

It shows server state, online players, world statistics, recent snapshots, the
blueprint library, production charts, and a tail of the server log.

**It is read-only on purpose.** No stop button, no rollback, no console. A page
with no authentication and no write path cannot be abused into doing damage.

To reach it from another machine, put a reverse proxy with authentication in
front of it. Setting `host` to `0.0.0.0` in `config/web_panel/config.json`
publishes player names and world state to anyone who can reach the port.

---

## 10. Opening the server to the internet

### 1. Decide how players find you

Edit `server/factorio/server-settings.json`:

```jsonc
{
  "visibility": { "public": true, "lan": true },
  "username": "your_factorio_com_username",
  "token": "your_token_from_player-data.json",
  "game_password": "",
  "require_user_verification": true,
  "max_players": 0
}
```

`public: true` lists you in the in-game server browser and needs the account
fields. Leave it `false` and hand out your IP if you would rather not be listed.

### 2. Forward the port

**34197/UDP** — not TCP. This is the single most common mistake.

```bash
sudo ufw allow 34197/udp
```

Then forward `34197/udp` on your router to the server machine.

### 3. Do not expose RCON

**27015/TCP must stay on localhost.** The RCON protocol is plaintext, and
anyone who reaches it owns your server. The default config binds it to
`127.0.0.1`; leave it there.

### 4. Restart and verify

```
!!FR server restart
```

Ask someone outside your network to try. If they cannot connect, check UDP (not
TCP) forwarding first.

---

## 11. Running it unattended

FactorioReforge runs in the foreground and expects a terminal. For 24/7
operation, pick one:

### tmux — simplest

```bash
tmux new -s factorio
./scripts/run.sh
# Ctrl-B then D to detach; tmux attach -t factorio to come back
```

### systemd — survives reboots

`~/.config/systemd/user/factorio-reforge.service`:

```ini
[Unit]
Description=FactorioReforge
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/FactorioReforge
ExecStart=%h/FactorioReforge/.venv/bin/python -m factorio_reforge
Restart=on-failure
RestartSec=15
# FactorioReforge stops the server gracefully on SIGTERM; give it room to save.
KillSignal=SIGTERM
TimeoutStopSec=120
StandardInput=null

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now factorio-reforge
systemctl --user status factorio-reforge
journalctl --user -u factorio-reforge -f
loginctl enable-linger "$USER"     # keep it running when you log out
```

**`StandardInput=null` means you have no console.** Everything you would have
typed there has to come from Telegram, or from in-game chat as an admin. If you
want a console too, use tmux instead — or run under systemd *and* rely on
Telegram, which is what the bridge is for.

Turn on crash recovery in `config.yml` either way:

```yaml
auto_restart_on_crash: true
crash_restart_delay: 10.0
```

---

## 12. Writing your first plugin

Create `plugins/hello.py`:

```python
from factorio_reforge.command.builder import Literal
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "hello",
    "version": "1.0.0",
    "name": "Hello",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}


def on_load(server, prev):
    server.register_command(
        Literal("!!hello")
        .requires(PermissionLevel.USER)
        .runs(lambda source: source.reply("hello yourself"))
    )
    server.register_help_message("!!hello", "say hello")


async def on_player_joined(server, player, info):
    await server.say(f"Welcome, {player}!")
```

Load it without restarting anything:

```
!!FR reload
```

```
Reloaded: hello
```

Try `!!hello`. Now edit the file and run `!!FR reload` again — the change takes
effect immediately. (Bytecode caching is bypassed for plugins, so an edit within
the same second that does not change the file length still reloads correctly.)

### Doing something more useful

```python
from factorio_reforge.core.errors import QueryError


async def on_player_joined(server, player, info):
    try:
        stats = await server.get_server_stats()
    except QueryError as exc:
        server.logger.warning("Could not read the world: %s", exc)
        return
    await server.tell(
        player,
        f"Evolution is at {stats['evolution'] * 100:.1f}% — mind the biters."
    )
```

`QueryError` covers both "RCON is down" and "the Lua failed", so plugin code
catches one thing.

The full API is in the [README](../README.md#writing-a-plugin), and the twelve
bundled plugins in `plugins/` are all readable working examples — `warp.py` is
the smallest one that does something real.

---

## 13. When something breaks

### The server exited and you do not know why

```
!!why
```

`crash_doctor` keeps a rolling buffer of output and matches it against real
failure signatures:

```
Last unexpected exit: code 1
  Cause: the mod 'flib' could not be loaded
  Detail: Incompatible Factorio version (current: 2.0, required: 2.1)
  Try: !!mod remove flib
```

If nothing matched, it prints the last lines of output instead of guessing.

### Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `RCON: not connected` right after start | RCON only listens once the map is loaded | Wait a second; if it persists, check that `rcon.password` in `config.yml` matches `--rcon-password` in `start_command` |
| `Could not look that up: RCON is not connected` | A query command ran before RCON came up | Same as above |
| Server exits with code 1 on start | Usually an incompatible mod | `!!why` |
| `Address already in use` | An old Factorio is still running | `pkill -f 'bin/x64/factorio'` |
| Players cannot connect | Port forwarded as TCP | Forward **34197/UDP** |
| Players get "mods do not match" | Their mod set differs | They need the same mods and versions |
| Rollback restored the wrong map | `--start-server-load-latest` in `start_command` | Use `--start-server <path>`; FactorioReforge refuses to start with the former |
| Telegram bot ignores you | Your chat id is not allowed | Check the log for the id, add it to `allowed_chat_ids` |

### Logs

```bash
tail -f logs/reforge.log
```

The RCON password is redacted in log output, so these are safe to paste when
asking for help. Your `config.yml` is **not** — it contains the password in
plaintext.

### Starting over

```bash
rm -rf .venv config.yml config/ logs/ snapshots/
./scripts/install.sh
```

That keeps `server/` — your world and mods — and rebuilds everything else. To
throw away the world too, delete `server/` as well.

---

## Where to go next

- [README](../README.md) — the full reference
- [M0-findings.md](M0-findings.md) — what measuring a real server actually
  revealed, including three things the documentation gets wrong
- `plugins/` — twelve working plugins to read and copy
