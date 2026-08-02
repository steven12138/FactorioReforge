"""Read and edit ``server-settings.json`` from chat.

The settings that decide what a server *is* -- its name, whether it is public,
whether players may cheat -- live in a JSON file next to the binary, and
changing one has so far meant stopping the server and opening an editor.

**Factorio reads this file once, at startup.** Nothing here takes effect until
the server restarts, so every change says so and offers to do it.

Two guards worth stating. Writes go through a temp file and a rename, because a
truncated ``server-settings.json`` stops the server from starting at all. And
``allow_commands`` is reported prominently: set to ``true`` it lets any player
run ``/c``, which is the one setting that quietly turns a server into a
sandbox.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factorio_reforge.command.builder import GreedyText, Integer, Literal, Text
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "server_admin",
    "version": "1.0.0",
    "name": "Server Admin",
    "description": "View and change server-settings.json from chat",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    #: Offer to restart after a change, since nothing applies until one happens.
    "prompt_restart": True,
}

#: Values that must never be shown: they are credentials.
_SECRET_KEYS = ("token", "game_password")

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, server=server)

    admin = PermissionLevel.ADMIN
    server.register_command(
        Literal("!!server")
        .requires(PermissionLevel.USER)
        .runs(_show)
        .then(Literal("show").runs(_show))
        .then(Literal("name").requires(admin).then(GreedyText("value").runs(_set_name)))
        .then(
            Literal("description").requires(admin)
            .then(GreedyText("value").runs(_set_description))
        )
        .then(
            Literal("password").requires(admin)
            .runs(_clear_password)
            .then(GreedyText("value").runs(_set_password))
        )
        .then(
            Literal("maxplayers").requires(admin).then(Integer("value").runs(_set_max_players))
        )
        .then(Literal("public").requires(admin).then(Text("value").runs(_set_public)))
        .then(Literal("lan").requires(admin).then(Text("value").runs(_set_lan)))
        .then(
            Literal("autosave").requires(admin).then(Integer("value").runs(_set_autosave))
        )
        .then(Literal("pause").requires(admin).then(Text("value").runs(_set_pause)))
        .then(
            Literal("commands").requires(PermissionLevel.OWNER)
            .then(Text("value").runs(_set_allow_commands))
        )
        .then(Literal("verify").requires(admin).then(Text("value").runs(_set_verify)))
    )
    server.register_help_message(
        "!!server", server.tr("help.summary"), PermissionLevel.USER,
        detail=[server.tr(f"help.detail.{index}") for index in range(9)],
    )


async def on_unload(server):
    _state.clear()


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------

def settings_path(server) -> Path:
    """Where Factorio is actually told to read its settings from.

    Taken from the start command rather than assumed, so a non-standard
    ``--server-settings`` is honoured instead of silently edited elsewhere.
    """
    core = server._server  # noqa: SLF001
    argv = core.config.command_argv
    if "--server-settings" in argv:
        named = Path(argv[argv.index("--server-settings") + 1])
        if not named.is_absolute():
            named = core.config.working_dir_path / named
        return named
    return core.config.working_dir_path / "server-settings.json"


def read_settings(server) -> dict[str, Any]:
    path = settings_path(server)
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_settings(server, settings: dict[str, Any]) -> None:
    """Save via a temp file and a rename.

    A half-written server-settings.json is not a cosmetic problem: Factorio
    refuses to start on it, and the operator is left with a server that will
    not come up and a file they did not knowingly break.
    """
    path = settings_path(server)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

async def _show(source):
    server = source.server
    tr = server.tr
    try:
        settings = read_settings(server)
    except (OSError, json.JSONDecodeError) as exc:
        await source.reply(tr("read_failed", error=exc))
        return

    visibility = settings.get("visibility") or {}
    await source.reply(tr("show.header"))
    await source.reply(tr("show.name", value=settings.get("name", "")))
    await source.reply(tr("show.description", value=settings.get("description", "")))
    await source.reply(tr(
        "show.visibility",
        public=_yes_no(tr, visibility.get("public")),
        lan=_yes_no(tr, visibility.get("lan")),
    ))
    await source.reply(tr(
        "show.max_players",
        value=settings.get("max_players", 0) or tr("show.unlimited"),
    ))
    await source.reply(tr(
        "show.password",
        value=tr("show.set") if settings.get("game_password") else tr("show.unset"),
    ))
    await source.reply(tr(
        "show.verify", value=_yes_no(tr, settings.get("require_user_verification"))
    ))
    await source.reply(tr(
        "show.autosave",
        minutes=settings.get("autosave_interval", 0),
        slots=settings.get("autosave_slots", 0),
    ))
    await source.reply(tr("show.pause", value=_yes_no(tr, settings.get("auto_pause"))))

    # Last and loudest: this is the one that decides whether it is still a game.
    allow = settings.get("allow_commands", "admins-only")
    await source.reply(tr("show.commands", value=allow))
    if allow is True or allow == "true":
        await source.reply(tr("show.commands_open"))


def _yes_no(tr, value) -> str:
    return tr("common.enabled") if value else tr("common.disabled")


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

async def _update(source, key: str, value: Any, shown: str | None = None) -> None:
    server = source.server
    tr = server.tr
    try:
        settings = read_settings(server)
    except (OSError, json.JSONDecodeError) as exc:
        await source.reply(tr("read_failed", error=exc))
        return

    if "." in key:
        outer, inner = key.split(".", 1)
        settings.setdefault(outer, {})[inner] = value
    else:
        settings[key] = value

    try:
        write_settings(server, settings)
    except OSError as exc:
        await source.reply(tr("write_failed", error=exc))
        return

    display = shown if shown is not None else value
    if any(secret in key for secret in _SECRET_KEYS) and shown is None:
        display = tr("show.set")
    await source.reply(tr("updated", key=key, value=display))
    await source.reply(tr("restart_needed"))


async def _set_name(source, ctx):
    await _update(source, "name", ctx["value"].strip())


async def _set_description(source, ctx):
    await _update(source, "description", ctx["value"].strip())


async def _set_password(source, ctx):
    await _update(source, "game_password", ctx["value"].strip())


async def _clear_password(source):
    await _update(source, "game_password", "", shown=source.server.tr("show.unset"))


async def _set_max_players(source, ctx):
    value = max(0, int(ctx["value"]))
    await _update(
        source, "max_players", value,
        shown=value or source.server.tr("show.unlimited"),
    )


async def _set_public(source, ctx):
    flag = _flag(ctx["value"])
    if flag is None:
        await source.reply(source.server.tr("bad_flag", value=ctx["value"]))
        return
    if flag:
        settings = read_settings(source.server)
        if not (settings.get("username") and settings.get("token")):
            # Factorio silently stays unlisted without them, which looks like
            # the setting did nothing.
            await source.reply(source.server.tr("public_needs_account"))
            return
    await _update(source, "visibility.public", flag)


async def _set_lan(source, ctx):
    flag = _flag(ctx["value"])
    if flag is None:
        await source.reply(source.server.tr("bad_flag", value=ctx["value"]))
        return
    await _update(source, "visibility.lan", flag)


async def _set_autosave(source, ctx):
    await _update(source, "autosave_interval", max(0, int(ctx["value"])))


async def _set_pause(source, ctx):
    flag = _flag(ctx["value"])
    if flag is None:
        await source.reply(source.server.tr("bad_flag", value=ctx["value"]))
        return
    await _update(source, "auto_pause", flag)


async def _set_verify(source, ctx):
    flag = _flag(ctx["value"])
    if flag is None:
        await source.reply(source.server.tr("bad_flag", value=ctx["value"]))
        return
    await _update(source, "require_user_verification", flag)


async def _set_allow_commands(source, ctx):
    """Owner only, and ``true`` is refused.

    ``true`` lets every player run ``/c``: free items, instant research, edited
    terrain. That is not a server setting so much as a decision to stop playing
    the game, and it is not something to reach by typing one word in chat.
    """
    value = ctx["value"].strip().lower()
    tr = source.server.tr
    if value in ("true", "yes", "on", "all", "everyone"):
        await source.reply(tr("commands_refused"))
        return
    if value not in ("false", "admins-only"):
        await source.reply(tr("commands_usage"))
        return
    await _update(source, "allow_commands", value)


def _flag(value: str) -> bool | None:
    lowered = value.strip().lower()
    if lowered in ("on", "true", "yes", "1"):
        return True
    if lowered in ("off", "false", "no", "0"):
        return False
    return None
