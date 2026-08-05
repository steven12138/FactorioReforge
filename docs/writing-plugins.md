# Writing plugins

A plugin is a directory in `plugins/` with an `__init__.py`. It can register
commands, listen for events, query the running game, store its own settings and
ship its own translations. Everything the bundled plugins do is available to
yours — none of them is special.

## The smallest one

```
plugins/greeter/
└── __init__.py
```

```python
from factorio_reforge.command.builder import Literal, GreedyText
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "greeter",
    "version": "1.0.0",
    "name": "Greeter",
    "description": "Says hello",
    "author": "you",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}


def on_load(server, prev):
    server.register_command(
        Literal("!!hello")
        .requires(PermissionLevel.USER)
        .runs(lambda source: source.reply("hi"))
    )
    server.register_help_message("!!hello", "say hello")


async def on_player_joined(server, player, info):
    await server.say(f"Welcome, {player}!")


async def on_unload(server):
    ...   # cancel tasks, close sockets
```

`!!FR plugin reload greeter` picks up changes without restarting Factorio.

A single `.py` file also loads, but a directory is what you want: only a
directory can hold [translations](#translations), and everything else scales
better in one too.

`dependencies` may name other plugins as well as `factorio_reforge`, and load
order is resolved from them. A cycle is reported by name rather than hanging.

## Commands

Commands are trees. Each node matches one word; arguments are typed.

```python
from factorio_reforge.command.builder import Literal, Text, GreedyText, Integer

server.register_command(
    Literal("!!shop")
    .requires(PermissionLevel.USER)
    .runs(overview)                                   # bare "!!shop"
    .then(Literal("list").runs(show_list))
    .then(Literal("buy").then(Text("item").then(Integer("count").runs(buy))))
    .then(Literal("gift").requires(PermissionLevel.ADMIN)
          .then(GreedyText("message").runs(gift)))
)
```

| Node | Matches |
|---|---|
| `Literal("word")` | Exactly that word |
| `Text("name")` | One whitespace-delimited word |
| `GreedyText("name")` | The whole rest of the line |
| `Integer("name")` | A whole number |

A handler receives `(source, **arguments)`, named after the nodes:

```python
async def buy(source, item: str, count: int):
    await source.reply(f"{count} x {item}")
```

`.requires(level)` applies to a node and everything under it, so `!!shop gift`
above is admin-only while the rest is not.

When the input does not match, the **deepest** failure is reported — typing
`!!shop buy` says what `buy` still wants, not that `!!shop` was misunderstood.
Where a node has literal children, they are listed: `Unknown option 'lst'.
Expected one of: list, buy, gift`.

A `description` key in your `lang/` catalogue is used for the one-line summary
in `!!FR help`, in the reader's language; `PLUGIN_METADATA["description"]` is
the fallback and is always English, being a Python literal.

`register_help_message(prefix, message, detail=(...))` puts a command in
`!!FR help`; `detail` lines show under `!!FR help <your_plugin>`.

## Events

Declare a function named after the event, and it is registered automatically:

```python
async def on_player_death(server, player, info):
    await server.say(f"F for {player}")
```

| Event | Fires |
|---|---|
| `on_load(server, prev)` | Plugin loaded; `prev` is the old module on a reload |
| `on_unload(server)` | Plugin going away — clean up here |
| `on_info(server, info)` | Every parsed line |
| `on_user_info(server, info)` | Only lines a person produced |
| `on_player_joined(server, player, info)` | A player joined |
| `on_player_left(server, player, info)` | A player left |
| `on_player_death(server, player, info)` | A player died |
| `on_server_start_pre(server)` | About to launch Factorio |
| `on_server_start(server)` | Process started |
| `on_server_startup(server)` | World loaded, players can connect |
| `on_server_stop_pre(server)` | Shutting down, **server still up** |
| `on_server_stop(server, code)` | Process has **exited**, with its return code |
| `on_server_crash(server, code)` | It exited without being asked to |
| `on_rcon_connected(server)` / `on_rcon_lost(server)` | RCON came up / went away |
| `on_snapshot_created(server, slot)` | A backup finished |
| `on_rollback_started(server, slot)` / `on_rollback_finished(server, ok)` | A restore |
| `on_reforge_start(server)` / `on_reforge_stop(server)` | FactorioReforge itself |

**`on_server_stop` fires after the process is gone**, which is the whole reason
it is separate from `on_server_stop_pre`. Anything touching files Factorio held
open — `mod-list.json` above all — has to wait for it, or the change is
discarded when the server writes its own copy out.

Callbacks may be `def` or `async def`, and may declare fewer parameters than the
event carries. A listener that raises is logged and skipped, so one broken
plugin does not take the others down.

You can also register explicitly, with a priority, or by decorator:

```python
server.register_event_listener("reforge.player_joined", callback, priority=50)

from factorio_reforge.plugin.events import event_listener

@event_listener("reforge.player_joined", priority=50)
async def welcome(server, player, info): ...
```

### Real Factorio events

The events above are what FactorioReforge itself notices. For a handful of
things, you can have **Factorio's own events** pushed to you instead of polling
for them:

```python
def on_load(server, prev):
    server.request_lua_event("on_research_finished")

async def on_lua_event(server, payload):
    if payload["event"] == "on_research_finished":
        await server.say(f"{payload['name']} is done")
```

This works because `script.on_event` *can* be registered from `/sc`, and
`print()` from inside the handler reaches stdout — measured, and the opposite of
what this project assumed for most of its life. The event arrives on the tick it
happened rather than at the next poll.

What is bridged is a short declared list in
`factorio_reforge.core.luahooks.BRIDGED`, not anything you name: `on_entity_died`
fires thousands of times a minute on a defended base, and stdout is not a
firehose worth opening. Adding one means adding its payload there.

Two things worth knowing before you rely on it:

* **It cannot see the past.** Handlers are installed when the server starts, so
  anything that happened while FactorioReforge was down is simply not delivered.
  If that matters, keep a slow poll underneath as the backstop —
  `world_watch` does exactly this, and both paths write to the same seen-set so
  whichever arrives first wins.
* **Asking twice is free.** The handler lives in the game, installed once per
  server start, so reloading your plugin does not duplicate it.

## Talking to the server

```python
await server.say("hello everyone")            # stdin: chat
await server.execute("/promote alice")        # stdin: a raw command
await server.tell("alice", "psst")            # RCON: one player
await source.reply("...")                     # wherever the command came from
```

`source.reply` is the one to use in a command handler: it answers in the
console, in game, or in Telegram, depending on where the command came from.

Anything with a **result** goes over RCON, and comes back as parsed Python
rather than as text to scrape:

```python
stats = await server.get_server_stats()
# {'tick': 18569, 'evolution': 0.00123, 'pollution': 0.0,
#  'research': None, 'players_online': 0, 'surface': 'nauvis', ...}

for p in await server.get_online_player_details():
    print(p["name"], p["online_time"], p["position"])

count = await server.lua_json("game.forces.player.get_entity_count('lab')")

await server.teleport_player("alice", {"x": 100, "y": 200})
await server.add_map_marker({"x": 0, "y": 0}, "base",
                            icon={"type": "virtual", "name": "signal-info"})
```

`lua_json` takes a Lua *expression*, wraps it in `helpers.table_to_json`, and
returns real Python. Lua errors arrive as exceptions carrying the Lua message,
not as a string that happens to begin with "Cannot execute command".

Both failure modes derive from one exception, so plugin code catches one thing:

```python
from factorio_reforge.core.errors import QueryError   # RconError, LuaError

try:
    stats = await server.get_server_stats()
except QueryError as exc:
    await source.reply(f"Could not look that up: {exc}")
```

Interpolate player names with `lua.lua_string()`, never with an f-string.
Factorio runs Lua 5.2, which has no `\u` escape, so `json.dumps` produces source
that does not compile for a non-ASCII name.

Everything goes through `/sc`, never `/c`, so nothing a plugin does can mark the
world as cheated. Keep it that way — a test greps the tree for `/c`.

## Rich text

Factorio's chat renders inline tags, and `[gps=x,y,surface]` is **clickable** —
it pings that position on everyone's map.

```python
from factorio_reforge.core import lua

await server.game_print(f"{player} is at {lua.gps(x, y, surface)}")
```

`lua.gps()`, `lua.item_tag()`, `lua.technology_tag()` and `lua.colored()` build
them. This is what makes `!!here` and `!!warp` genuinely useful rather than a
way to print coordinates. Chart tags complement it: a gps tag says "look here
now", a chart tag says "this place has a name".

## Storage

```python
config = server.load_config_simple("config.json", {"enabled": True, "radius": 32})
config["radius"] = 64
server.save_config_simple(config)

path = server.get_data_folder()      # config/<your_id>/, created for you
```

Missing keys are filled in from the defaults, so adding a setting in a new
version does not force operators to edit their file by hand.

## Translations

A plugin owns its translations the way it owns its code:

```
plugins/greeter/
├── __init__.py
└── lang/
    ├── en.yml
    └── zh_cn.yml
```

```yaml
# en.yml
welcome: "Welcome, {player}!"
error:
  no_such_place: "There is no warp called {name}"
```

```python
await server.say(server.tr("welcome", player=player))
await source.reply(server.tr("error.no_such_place", name=name))
```

Keys are namespaced under your plugin id automatically, so two plugins can both
have `failed`. A key your catalogue does not define falls through to the core
one, which is where shared strings like `common.enabled` live.

Three deliberate behaviours:

- **A missing key renders as the key.** A visible `greeter.welcome` in chat says
  exactly what to add; a blank line says nothing.
- **English is always the fallback**, so a half-translated language stays usable
  rather than turning into holes.
- **A broken placeholder falls back to the raw template**, so a translator who
  drops a `{player}` causes a slightly wrong message, not an exception inside a
  command handler.

> **YAML reads `yes`, `no`, `on` and `off` as booleans — keys included.** A bare
> `yes:` key becomes `True`, and every lookup of `common.yes` then renders as
> the key. Quote them, or name them something else. A test rejects any catalogue
> containing one.

Tests assert that every bundled plugin ships both languages, that neither has
keys the other lacks, and that matching keys carry the same placeholders — a key
whose placeholders drift would format wrongly in one language only. Adding a
language means copying `en.yml` to `<code>.yml` in the core catalogue and in
each plugin, and translating the values.

## Telegram sub-plugins

`telegram_bridge` is a **service** other plugins register with, so your plugin
can be driven from Telegram without importing `telegram` or handling a token:

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

`ctx` carries `args`, `text`, `user_id`, `user_name`, `level`, `is_admin` and
`is_owner`, plus `reply()` — which splits messages over Telegram's
4096-character limit — and `confirm()`, which shows inline Yes/Cancel buttons
and returns `False` on timeout.

Levels are `viewer` / `admin` / `owner`, resolved from the id lists in the
bridge's config. Registrations are keyed by the owning plugin, so unloading your
plugin takes its Telegram commands with it.

## Testing

Bundled plugin logic is tested without a server at all: `tests/test_plugin_logic.py`
imports plugins by path, the way the manager does, and calls their pure
functions. Keep the parsing, the formatting and the arithmetic in functions that
take data and return data, and they stay testable that way.

For anything that needs a server, `tests/fake_factorio.py` is a stand-in that
reproduces the real binary's behaviour, including the ones that surprised us —
see [Factorio notes](factorio-notes.md).

```bash
python -m pytest tests/ -q
```
