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

- **Every plugin is now a package that owns its `lang/` directory.** Translations
  used to share `plugins/lang/<id>/`, which only existed because a solo `.py`
  file had nowhere to put them.
- `!!server commands true` is refused: it lets every player run `/c`, which is a
  decision to stop playing the game rather than a server setting.

### Fixed

- **A deadlock that froze the server on `!!save make`.** Command handlers ran
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
