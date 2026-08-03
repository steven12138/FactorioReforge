"""A server-side blueprint library.

Blueprint strings can be produced and consumed entirely through a scratch
inventory over RCON, so this needs nothing installed on any client: a player
saves the area around them, another player asks for it and it lands in their
inventory. The library outlives both of them.

    !!bp save <name>        blueprint the area around you and store it
    !!bp get <name>         put a stored blueprint into your inventory
    !!bp list / info / del

Strings are stored as-is and validated on the way in, so a malformed one is
rejected at save time rather than failing silently when someone asks for it.
"""

from __future__ import annotations

import json
import time

from factorio_reforge.command.builder import GreedyText, Integer, Literal, Text
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "blueprints",
    "version": "1.0.0",
    "name": "Blueprint Library",
    "description": "Save and share blueprints server-side",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    #: Half-width of the square captured by !!bp save, in tiles.
    "capture_radius": 32,
    "max_capture_radius": 200,
    "max_blueprints": 200,
    #: Anyone may save; only this level and above may delete someone else's.
    "manage_permission": "admin",
    "surface": "nauvis",
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, library=_load_library(server))

    manage = _parse_level(config.get("manage_permission", "admin"), server)
    server.register_command(
        Literal("!!bp")
        .requires(PermissionLevel.USER)
        .runs(_cmd_list)
        .then(Literal("list").runs(_cmd_list))
        .then(Literal("info").then(GreedyText("name").runs(_cmd_info)))
        .then(Literal("get").then(GreedyText("name").runs(_cmd_get)))
        .then(
            Literal("save")
            .then(
                Text("name").runs(_cmd_save)
                .then(Integer("radius").runs(_cmd_save))
            )
        )
        .then(Literal("del").requires(manage).then(GreedyText("name").runs(_cmd_delete)))
    )
    server.register_help_message("!!bp", server.tr("help"), PermissionLevel.USER)


async def on_unload(server):
    _state.clear()


def _parse_level(value, server) -> PermissionLevel:
    try:
        return PermissionLevel.parse(value)
    except ValueError:
        server.logger.warning("manage_permission %r is not a level; using admin", value)
        return PermissionLevel.ADMIN


# -- storage ----------------------------------------------------------------

def _library_path(server):
    return server.get_data_folder() / "library.json"


def _load_library(server) -> dict:
    path = _library_path(server)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        server.logger.error("The blueprint library is unreadable (%s); starting empty", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save_library(server) -> None:
    path = _library_path(server)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(_state["library"], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temp.replace(path)


def _find(name: str) -> tuple[str, dict] | None:
    library = _state["library"]
    if name in library:
        return name, library[name]
    lowered = name.lower()
    for key, value in library.items():
        if key.lower() == lowered:
            return key, value
    return None


# -- public helper, used by web_panel ---------------------------------------

def get_library() -> dict:
    """Metadata for every stored blueprint, without the strings themselves."""
    return {
        name: {k: v for k, v in entry.items() if k != "blueprint"}
        for name, entry in (_state.get("library") or {}).items()
    }


# -- commands ---------------------------------------------------------------

async def _cmd_list(source):
    library = _state["library"]
    if not library:
        await source.reply(source.server.tr("list.empty"))
        return
    await source.reply(source.server.tr("list.header", count=len(library)))
    for name, entry in sorted(library.items()):
        await source.reply(source.server.tr(
            "list.entry", name=name, entities=entry.get("entities", 0),
            by=entry.get("saved_by", "?")))
    await source.reply(source.server.tr("list.hint"))


async def _cmd_info(source, ctx):
    found = _find(ctx["name"].strip())
    if found is None:
        await source.reply(source.server.tr("info.not_found", name=ctx["name"]))
        return
    name, entry = found
    tr = source.server.tr
    await source.reply(tr("info.header", name=name))
    await source.reply(tr("info.entities", count=entry.get("entities", 0)))
    await source.reply(tr("info.saved_by", by=entry.get("saved_by", "?")))
    counts = entry.get("counts") or {}
    if counts:
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
        await source.reply(tr("info.contains",
                              items=", ".join(f"{n} x{c}" for n, c in top)))


async def _cmd_save(source, ctx):
    """Store what the caller is holding, or failing that the area around them.

    Holding a blueprint and typing ``!!bp save x`` can only mean one thing, and
    it is the gesture people already have from the in-game library. Capturing
    the surroundings stays as the behaviour for an empty hand, so nothing that
    used to work stopped working.
    """
    name = ctx["name"].strip()
    if source.player is None:
        await source.reply(source.server.tr("save.console_only"))
        return
    if ctx.get("radius") is None and await _save_held(source, name):
        return

    config = _state["config"]
    radius = int(ctx.get("radius") or config.get("capture_radius", 32))
    limit = config.get("max_capture_radius", 200)
    if radius < 1 or radius > limit:
        await source.reply(source.server.tr("save.bad_radius", max=limit))
        return
    if len(_state["library"]) >= config.get("max_blueprints", 200) and not _find(name):
        await source.reply(source.server.tr("save.full", count=len(_state["library"])))
        return

    server = source.server
    try:
        info = await server.get_player_info(source.player)
    except QueryError as exc:
        await source.reply(server.tr("save.read_failed", error=exc))
        return
    if not info or not info.get("position"):
        await source.reply(server.tr("save.no_position"))
        return

    position = info["position"]
    surface = info.get("surface") or config.get("surface", "nauvis")
    area = {
        1: {"x": position["x"] - radius, "y": position["y"] - radius},
        2: {"x": position["x"] + radius, "y": position["y"] + radius},
    }

    await source.reply(server.tr("save.capturing", size=radius * 2))
    try:
        result = await server.lua_json(lua.capture_blueprint(surface, area))
    except QueryError as exc:
        await source.reply(server.tr("save.failed", error=exc))
        return

    if not result or not result.get("blueprint"):
        await source.reply(server.tr("save.empty_area"))
        return

    _state["library"][name] = {
        "blueprint": result["blueprint"],
        "entities": result.get("entities", 0),
        "saved_by": source.player,
        "saved_at": time.time(),
        "radius": radius,
    }
    _save_library(server)
    await source.reply(server.tr("save.done", name=name, entities=result.get("entities", 0)))


async def _save_held(source, name: str) -> bool:
    """Store the blueprint in the caller's hand. False means their hand is empty.

    Anything else going wrong -- holding a wrench, a string Factorio will not
    export -- is reported here and still returns True, because falling through
    to "blueprint the area around you" would silently answer a different
    question from the one that was asked.
    """
    server = source.server
    config = _state["config"]
    try:
        held = await server.lua_json(lua.export_held_blueprint(source.player))
    except QueryError as exc:
        await source.reply(server.tr("save.read_failed", error=exc))
        return True

    held = held or {}
    if not held.get("ok"):
        if held.get("reason") == "empty hand":
            return False
        if held.get("reason") == "not a blueprint":
            await source.reply(server.tr("held.not_a_blueprint",
                                         item=held.get("name") or "?"))
            return True
        await source.reply(server.tr("save.failed", error=held.get("reason") or "?"))
        return True

    if len(_state["library"]) >= config.get("max_blueprints", 200) and not _find(name):
        await source.reply(server.tr("save.full", count=len(_state["library"])))
        return True

    _state["library"][name] = {
        "blueprint": held["blueprint"],
        "entities": held.get("entities", 0),
        "counts": held.get("counts") or {},
        "kind": held.get("kind") or "blueprint",
        "label": held.get("label") or "",
        "saved_by": source.player,
        "saved_at": time.time(),
    }
    _save_library(server)
    await source.reply(server.tr(
        "held.saved", name=name, kind=held.get("kind") or "blueprint",
        entities=held.get("entities", 0),
    ))
    return True


async def _cmd_get(source, ctx):
    found = _find(ctx["name"].strip())
    if found is None:
        await source.reply(source.server.tr("get.not_found", name=ctx["name"]))
        return
    name, entry = found

    if source.player is None:
        # The console has no inventory, so hand over the string instead.
        await source.reply(source.server.tr(
            "get.console", name=name, entities=entry.get("entities", 0)))
        await source.reply(entry["blueprint"])
        return

    try:
        result = await source.server.lua_json(lua.give_blueprint_to_cursor(
            source.player, entry["blueprint"], entry.get("kind")
        ))
    except QueryError as exc:
        await source.reply(source.server.tr("get.failed", error=exc))
        return

    result = result or {}
    if not result.get("ok"):
        await source.reply(source.server.tr(
            "get.refused", name=name,
            reason=result.get("reason") or source.server.tr("common.unknown")))
        return
    # Say where it went: "it is in your hand" and "it is in your inventory" send
    # the player to two different places.
    await source.reply(source.server.tr(
        "get.in_cursor" if result.get("where") == "cursor" else "get.done",
        name=name, entities=entry.get("entities", 0)))


async def _cmd_delete(source, ctx):
    found = _find(ctx["name"].strip())
    if found is None:
        await source.reply(f"No blueprint called {ctx['name']!r}.")
        return
    name, _ = found
    del _state["library"][name]
    _save_library(source.server)
    await source.reply(source.server.tr("delete.done", name=name))
