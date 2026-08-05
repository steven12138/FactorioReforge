# Changelog

中文见 [CHANGELOG_zh.md](CHANGELOG_zh.md)。

## Unreleased

### Documentation

- Restructured. The README is now a landing page; the reference material it used
  to carry is split into [Commands](docs/commands.md),
  [Configuration](docs/configuration.md), [Plugins](docs/plugins.md),
  [Writing plugins](docs/writing-plugins.md),
  [Running a Factorio server](docs/factorio-server.md) and
  [Architecture](docs/architecture.md), each with a Chinese counterpart.
- `docs/M0-findings.md` is now [`docs/factorio-notes.md`](docs/factorio-notes.md) —
  it had outgrown being named after a milestone.
- Added [CONTRIBUTING.md](CONTRIBUTING.md) and this file.

### Added

- **`version_manager` — `!!version`.** Changing which Factorio build the server
  runs, without losing the world to it. A save format upgrade is a one-way
  door, so this is shaped like a restore and not like an install: stage, back
  up, swap, verify, put it back.
  - Nothing hardcodes a compatibility rule. `--version` reports a *map input*
    and a *map output* version, and a save carries its own version in the first
    eight bytes of `level-init.dat`, so whether a swap will work is decided
    before the server stops rather than by trying it.
  - Versions become directories with a symlink at the live path, so a rollback
    is a symlink flip — which is the only kind of swap still available when the
    new build will not start and the network is not the thing you want to
    depend on. `!!version adopt` converts an existing install in place, undoing
    every step if any of them fails.
  - Downloading and switching are separate commands: one needs no downtime, the
    other needs the server down.
  - The world from before a swap goes to a fixed `pre-upgrade` backup slot that
    nothing rotates, which is what makes `!!version use <older> with-save
    pre-upgrade` — binary and world back together — possible at all.
  - The expansion is not a hazard: `space-age`, `quality` and `elevated-rails`
    ship inside `data/` and move with the binary. Third-party mods pin
    `major.minor`, so patch upgrades cannot break one, and those are checked.
- `!!version check <series>` lists every release in a series. Asking the
  updater instead of the release API returns the channel markers *and* all 376
  released headless versions in one request -- "newest" is never the question
  when you are going back a version.

### Fixed

- Anything holding the write end of stdin open no longer stops FactorioReforge
  from exiting. `run_in_executor` cannot be cancelled out of a blocking
  `readline`, and asyncio's default executor is non-daemon, so the interpreter
  waited on a thread that was never going to return: Factorio stopped, plugins
  unloaded, "goodbye" logged, and the process sat there. Found with stdin on a
  FIFO; `tail -f | ...` and a supervisor with a socket on stdin do it too.

- **The calculator wrote every plan in a machine you might not have.** Three
  separate causes, all of them live on a real save:
  - the config file still pinned the old hardcoded list, and
    `load_config_simple` never rewrites a key that is already there, so
    changing the default to "pick the best you can build" fixed nothing for
    anyone who had run the plugin once. The exact old list is now cleared on
    load; a list somebody reordered or trimmed is left alone.
  - what counted as *unlocked* included recycling. Every machine has an
    `X-recycling` recipe, those are enabled from the start, and recycling an
    assembling machine 3 yields an assembling machine 2 -- so a save that had
    researched neither reported tier 2, and every plan came out one tier high.
  - where nothing in a category was unlocked, the fallback named the *fastest*
    machine in the game. A save running assembling machine 1 was told to use a
    cryogenic plant. It now names the one you would reach first.
  `!!ratio machine` shows and changes the choice from chat.
- **A reloaded plugin's old commands kept running.** Nothing ever called
  `CommandManager.unregister_plugin`, so every `!!FR plugin reload` left the
  previous tree registered *and* first in the list; dispatch ran the old
  module, whose `on_unload` had just cleared its state. Unloading a plugin left
  its commands dispatching into a module that no longer existed. The stand-in
  used in the tests implements the method, which is why satisfying it proved
  nothing -- the regression tests now drive the real CommandManager.

- `telegram_bridge` retries when it cannot reach Telegram, on a widening
  backoff, instead of logging a traceback and staying dead until someone
  reloads the plugin. Measured on a live server: api.telegram.org times out
  entirely from there, which is the network's problem and not a fault in the
  server. A token Telegram *rejects* is still not retried.
- The colour tests read the developer's own environment: `FORCE_COLOR=3` in a
  shell turned `test_disabled_when_not_a_tty` red, and the code was right --
  forcing colour through a pipe is what that variable means. Both directions of
  the convention are now covered, and the environment is isolated.

- `!!seen` and `!!info` report the date and time somebody was last online,
  from a record kept on this side of the pipe. Factorio's `player.last_online`
  is a tick, and turning ticks into a duration measures how long the *world*
  ran: with `auto_pause` on, an empty server does not tick, so somebody who
  left last night read as "20m ago" the moment the next player logged in.
- Every bundled plugin is now loaded through the real plugin manager in the
  test suite. `version_manager` shipped reaching for `server.config`, which
  lives on the core and not on the interface plugins are handed; 535 tests
  passed and the server was the first thing to run `on_load` for real.

### Added

- **Seven plugins for watching a server you are not sitting in front of.**
  - `ups_watch` — `!!ups`. Factorio fails by getting slower, not by crashing.
    Samples `game.tick` against the wall clock. A paused server reads 0.5
    ticks/s (measured), so samples taken with nobody online are discarded, and
    the window is judged by its median so one autosave dip is not a collapse.
  - `alerts` — attacks, including with the server empty. Player alerts are
    relayed when someone is connected; structure counts catch the rest, which
    is the case worth waking up for and needs no companion mod.
  - `trains` — `!!trains stuck`. No-path immediately, waiting states only after
    they persist, and the clock resets when a train moves.
  - `power` — `!!power`. Accumulator charge summed in Lua, with thresholds that
    report once on the way down and once on the way back.
  - `research` — `!!research add`, from a mine or a phone. The game's refusal is
    relayed rather than predicted.
  - `vote` — `!!vote`. Only players present when it started, ends as soon as the
    outcome is settled, silence counts as no. Emits `vote.finished` and acts on
    nothing by itself.
  - `mail` — `!!mail`. Delivered a few seconds after joining, immediately if
    they are already online.
- `factorio_reforge/core/progress.py`: rate-limited progress reporting for slow
  operations, used by `!!mod refresh`. Nothing is emitted for an operation that
  finishes quickly.

- **`calculator`**: `==1400/7.5` answers arithmetic in chat, and `!!ratio` answers
  what it takes to build something — machines, inputs, belts and power.
  - Recipes are read from the running game (`prototypes.recipe` over RCON), so
    the numbers match your version and your mods and no table here can go stale.
  - The rates are solved as a linear program with an exact-rational simplex, the
    same approach Kirk McDonald's calculator, FactorioLab and YAFC settled on. A
    recursive walk cannot answer oil processing (overlapping products) or Kovarex
    (consumes what it produces); both fall out of the same solve.
  - With no item named, it uses the machine you are hovering over and the recipe
    set in it, or what is in your cursor. Icons from the in-game picker
    (`[item=iron-plate]`) work as arguments.
  - `!!recipe` and `!!belt` for the smaller questions.
  - Arithmetic is evaluated by walking the parsed AST against a whitelist;
    `eval` is never called on player input, and `9**9**9` is refused before it
    runs rather than after.
- **`blueprints`**: `!!bp save <name>` now stores **the blueprint in your hand**,
  which is the gesture people already have from the in-game library. An empty
  hand still blueprints the area around you. Books and deconstruction or upgrade
  planners work too, and `!!bp get` hands it back into your cursor rather than
  burying it in your inventory -- unless you are already holding something, which
  is never overwritten.
- `scripts/probe_prototypes.py` checks the prototype API the calculator reads
  against a running server, so a build that moves one of those names shows up as
  a failed line rather than a plan quietly missing a machine.
- **`server_admin`**: `!!server` reads and edits `server-settings.json` from
  chat — name, description, password, player limit, visibility, autosave, pause,
  verification. Writes go through a temp file and a rename, because a truncated
  `server-settings.json` stops the server from starting at all.
- `!!FR help <plugin>` shows one plugin in detail; `!!FR help` groups commands by
  plugin; `!!FR plugin list` shows versions, descriptions and registered
  commands. A plugin can now be discovered without reading its source.
- Plugins may register `detail` lines with a help entry, shown in their own help.
- A startup warning when `allow_commands` is already `true`.

### Changed

- **`!!help` works as well as `!!FR help`.** Help is what somebody types when
  they do not know the commands, so making it the longest thing to type was
  backwards. Same index, same paging, same search.

- **`!!FR help` is an index, not a transcript.** With twenty-one plugins the
  grouped form ran past sixty lines, so the plugins late in the alphabet
  scrolled off the top of the chat box and were undiscoverable. It is now one
  line per plugin — id, commands, what it does — paginated **for players only**,
  since the console and Telegram have scrollback and the chat box does not.
  `!!FR help <n>` is a page, `!!FR help <plugin>` is that plugin, and anything
  else is a search: `!!FR help ratio` finds the calculator.
- Plugin summaries in that index come from a `description` key in the plugin's
  own catalogue when it has one, so the widest column is no longer always
  English. `PLUGIN_METADATA["description"]` remains the fallback.

- **`!!save` is now `!!qb`**, matching
  [QuickBackupM](https://github.com/TISUnion/QuickBackupM), whose command set
  this already followed. `!!save` still works — a rename that silently breaks a
  backup command is the worst kind of rename — and both names share one staging
  slot, so staging under one and confirming under the other is one restore.
- **Automatic backups have their own ring of slots**, addressed as `a1`, `a2`.
  Sharing one ring meant a timer running every half hour walked the whole
  history out of the building overnight, and what it pushed off the end was the
  backup someone took before doing something risky — the only reason the feature
  exists. `saves.auto_slot_protection` sizes the new ring.

- **Every plugin is now a package that owns its `lang/` directory.** Translations
  used to share `plugins/lang/<id>/`, which only existed because a solo `.py`
  file had nowhere to put them.
- `!!server commands true` is refused: it lets every player run `/c`, which is a
  decision to stop playing the game rather than a server setting.

### Fixed

- **`!!help qb` found nothing.** The framework's own commands are not plugins,
  so they were in no registry and the lookup could not see them -- while the
  index two lines above printed them. `qb` and `fr` are topics now, `save` and
  `backup` reach the backup one, and core commands are included in searches.
- `help.lang` was never in the catalogue, so the help index printed the raw key
  where the description of `!!FR lang` should have been.

- **`install.sh` could write a config.yml with no RCON password.** It was
  generated inside the "if the Factorio binary is here" block, so `--no-server`
  — and any run where the download had failed — produced an empty password and
  no working RCON. It is now its own step, before anything that needs it, with a
  `/dev/urandom` fallback.
- **One install in sixty-six generated a broken password.**
  `secrets.token_urlsafe` emits base64url, 1.5% of which *starts* with `-`, and
  it was interpolated unquoted: `--rcon-password -HjOaa2...` is read by
  Factorio's argument parser as another flag. Passwords are now alphanumeric and
  `shlex.quote`d.
- **Startup now refuses a config whose two RCON passwords disagree**, or whose
  password begins with a dash. Both used to fail silently, and silently means
  every query failing with nothing to point at.

- **The calculator answered every question in `assembling-machine-3`**, whatever
  the save had researched — the machine list comes from prototypes, and
  prototypes know nothing about research. It now picks the fastest machine the
  force can actually place, with `machines` in the config and `machine=` on a
  question to override.
- **Item and machine names now render in each player's own language.** Plans were
  written in prototype ids, which are not words in any language. Lines sent into
  the game are LocalisedStrings, so Factorio translates them client-side from its
  own catalogue; the console and Telegram still get the ids, having no Factorio
  to render with.

- **`!!mod refresh` logged its progress line twice, once untranslated.** Core
  logged an English line the plugin had already said in the operator's language.
- **Portal errors reached chat in English**, so `!!mod info nosuchmod` answered
  in English on a Chinese server. `PortalError` now carries a translation key.
- `!!mod update` is accepted as well as `!!mod updates`.

- **A deadlock that froze the server on `!!qb make`.** Command handlers ran
  inline on the stdout read loop, so a handler waiting for Factorio to print
  "Saving finished" was waiting for a line only it could read. Commands now
  dispatch into their own task; parsing and events stay inline to preserve
  ordering.
- **Translation keys named `yes`, `no`, `on` or `off`.** YAML reads those as
  booleans, keys included, so `common.yes` was stored as `common.True` and
  `!!server` printed `public common.no`. Renamed, and a test now rejects any
  catalogue containing one.
- `PluginServerInterface.tr()` was missing, so a plugin's translator fell through
  to the core one.

## 0.1.0

The first working version, developed against Factorio 2.0.77 headless.

- Process management: start, graceful stop, crash detection, auto-restart.
  Shutdown escalates `/quit` → SIGINT → SIGTERM → SIGKILL, and Ctrl-C takes the
  same path rather than leaving the server running.
- Output parsing into structured events, covering all four line shapes a real
  server produces.
- Hot-reloadable plugins with dependency-ordered loading, a command tree, and a
  five-level permission model.
- RCON with structured queries: every Lua query is wrapped in
  `helpers.table_to_json`, so plugins get real Python data rather than scraped
  text.
- Backups on [QuickBackupM](https://github.com/TISUnion/QuickBackupM)'s slot
  model, with an orchestrated restore that copies the current world aside first.
- Internationalisation throughout, English and Simplified Chinese, logs
  included. Each plugin ships its own catalogues.
- A unified console format with optional colour, and a startup report that
  explains Factorio's own output without modifying a line of it.
- Thirteen bundled plugins: `telegram_bridge`, `auto_snapshot`, `mod_manager`,
  `map_render`, `crash_doctor`, `server_utils`, `warp`, `blueprints`,
  `production`, `world_watch`, `leaderboard`, `join_motd`, `web_panel`.
