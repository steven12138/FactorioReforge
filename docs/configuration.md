# Configuration

Everything about FactorioReforge itself lives in `config.yml` at the repo root.
Per-plugin settings live in `config/<plugin_id>/`, documented with each plugin
in [Plugins](plugins.md).

`./scripts/install.sh` writes a working `config.yml` for you. This page is for
changing it afterwards, or for pointing FactorioReforge at a server you already
have.

## Creating one

```bash
python -m factorio_reforge init
```

Writes `config.yml` plus the `plugins/`, `config/`, `logs/` and `snapshots/`
directories. It never overwrites an existing config.

## config.yml

```yaml
# What to run, and where.
working_directory: server/factorio
start_command: >-
  ./bin/x64/factorio --start-server ./saves/reforge.zip
  --server-settings ./server-settings.json
  --server-adminlist ./server-adminlist.json
  --server-banlist ./server-banlist.json
  --port 34197
  --rcon-bind 127.0.0.1:27015 --rcon-password s3cret

language: en                  # en | zh_cn
colour: auto                  # auto | always | never

rcon:
  enabled: true
  host: 127.0.0.1
  port: 27015
  password: s3cret            # must match --rcon-password above

server:
  auto_restart: false         # restart after an unexpected exit
  stop_timeout: 120           # seconds to wait for /quit before escalating

saves:
  current_save: server/factorio/saves/reforge.zip
  snapshot_dir: snapshots
  slot_protection: [0, 0, 0, 10800, 259200]
  auto_slot_protection: [0, 0, 0, 0, 0]

permission:
  default_level: user

plugin:
  directories: [plugins]
```

### working_directory and start_command

`start_command` runs with `working_directory` as its current directory, which is
why the paths in it are relative. Point both at your headless install. The
command is what you would have typed yourself — see
[Running a Factorio server](factorio-server.md#start-it).

### rcon

RCON is how anything with a *result* is read back: the player list, Lua
expressions, private messages. `password` has to match the `--rcon-password` in
`start_command`; nothing else can connect for you.

With `enabled: false`, plugins that need a result raise `RconError` rather than
returning something invented. Chat, commands and backups still work — they go
over stdin.

### rcon.password

Written in two places: `rcon.password`, which is what FactorioReforge connects
with, and `--rcon-password` inside `start_command`, which is what Factorio
listens with. **They must match**, and startup refuses the config if they do not
— when they drift the only symptom is that RCON never comes up, every query
fails and half the plugins go quiet with nothing pointing at the cause.

A password must not begin with a dash. `argv` reaches Factorio unchanged and its
argument parser reads a leading dash as another flag, so the server starts,
runs, and has no RCON. `install.sh` generates alphanumeric passwords for exactly
this reason.

### saves.auto_slot_protection

The same list, for the ring automatic backups use. Automatic and manual backups
shift **separately**, so a timer running every half hour cannot walk a backup
someone took before a risky change off the end of the list overnight. Automatic
slots are addressed with an `a`: `!!qb back a2`. No protection by default —
nothing in that ring was asked for by a person.

### saves.slot_protection

A list of seconds, one per backup slot. **Its length is the number of slots.**
Each entry is how long a backup in that slot is protected from being deleted to
make room. The defaults — `[0, 0, 0, 10800, 259200]` — keep slot 4 for three
hours and slot 5 for three days, so a burst of backups cannot wipe out
yesterday's world. The full model is in
[Backups](architecture.md#backups-and-restoring).

### colour

`auto` colours the console only when stdout is a terminal that wants it, so
piping into `grep` or a log collector stays clean. `NO_COLOR` and `TERM=dumb`
also turn it off. `logs/reforge.log` is never coloured.

### language

`en` and `zh_cn` ship. Switch at runtime with `!!FR lang set zh_cn`, which
rewrites this one line and takes effect immediately, logs included. Adding a
language is in [Writing plugins](writing-plugins.md#translations).

## What it refuses to start with

Four checks run before anything is launched. Each one is a failure that is
silent and expensive if it is allowed through, so it is a hard stop with a
message, not a warning.

**`--start-server-load-latest` in `start_command`.** Restoring replaces
`saves.current_save`, but an autosave written since is newer, so the server
would come back on the wrong map — and look like the restore silently failed.

**`--start-server` pointing somewhere other than `saves.current_save`.** A
restore would write a file the server never reads. Same symptom, different
cause, so both are checked.

**`--rcon-port`, or a `--rcon-bind` address that is not local.** RCON is
plaintext, and reaching the port is controlling the server. `--rcon-port` binds
every interface. If you genuinely need remote RCON, tunnel it over SSH rather
than exposing it.

**An RCON password that does not match `start_command`.** Otherwise the first
symptom is a plugin failing much later, for no visible reason.

Settings removed in a newer version are reported by name at startup — with what
replaced them — rather than being ignored in silence.

## Environment

| Variable | Effect |
|---|---|
| `NO_COLOR` | Disables colour, per [no-color.org](https://no-color.org) |
| `FORCE_COLOR` | Forces colour on when stdout is not a terminal |
| `TERM=dumb` | Disables colour |

## Running as a service

There is deliberately no bundled unit file — where logs go and which user owns
the server are decisions for your machine, not this project. A minimal
`systemd` unit:

```ini
[Unit]
Description=FactorioReforge
After=network.target

[Service]
Type=simple
User=factorio
WorkingDirectory=/home/factorio/FactorioReforge
ExecStart=/home/factorio/FactorioReforge/.venv/bin/python -m factorio_reforge
Restart=on-failure
KillSignal=SIGINT
TimeoutStopSec=180

[Install]
WantedBy=multi-user.target
```

`KillSignal=SIGINT` matters: SIGINT is the graceful path, which stops Factorio
and waits for it to exit. `TimeoutStopSec` must be longer than
`server.stop_timeout` plus however long your world takes to save, or systemd
will SIGKILL a server in the middle of writing its save.

Without a terminal, the interactive console is not available — control the
server through Telegram or in-game chat instead.
