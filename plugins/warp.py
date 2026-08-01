"""Named places, shared with everyone -- without teleporting anyone.

The teleport-free half of what people usually want from ``!!tp``: admins name
the places that matter ("main bus", "north iron"), and anyone can look them up.
Each warp is announced as a clickable ``[gps=...]`` tag, which pings the spot on
the asker's map, and is pinned as a chart tag so it stays visible.

Nobody is moved. Walking there is the game.
"""

from __future__ import annotations

import json
import time

from factorio_reforge.command.builder import GreedyText, Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "warp",
    "version": "1.0.0",
    "name": "Warp Points",
    "description": "Named, shareable map locations -- no teleporting",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    #: Who may add or delete a warp. Looking them up is always open.
    "manage_permission": "helper",
    #: Pin each warp as a chart tag so it shows on the map permanently.
    "chart_tags": True,
    "chart_tag_icon": {"type": "virtual", "name": "signal-star"},
    "max_warps": 100,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, server=server, warps=_load_warps(server))

    manage = _parse_level(config.get("manage_permission", "helper"), server)
    server.register_command(
        Literal("!!warp")
        .requires(PermissionLevel.USER)
        .runs(_cmd_list)
        .then(Literal("list").runs(_cmd_list))
        .then(
            Literal("set").requires(manage)
            .then(GreedyText("name").runs(_cmd_set))
        )
        .then(
            Literal("del").requires(manage)
            .then(GreedyText("name").runs(_cmd_delete))
        )
        .then(GreedyText("name").runs(_cmd_goto))
    )
    server.register_help_message("!!warp [name]", server.tr("help"), PermissionLevel.USER)


async def on_unload(server):
    _state.clear()


def _parse_level(value, server) -> PermissionLevel:
    try:
        return PermissionLevel.parse(value)
    except ValueError:
        server.logger.warning("manage_permission %r is not a level; using helper", value)
        return PermissionLevel.HELPER


# -- storage ----------------------------------------------------------------

def _warps_path(server):
    return server.get_data_folder() / "warps.json"


def _load_warps(server) -> dict:
    path = _warps_path(server)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        server.logger.error("warps.json is unreadable (%s); starting empty", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save_warps(server) -> None:
    path = _warps_path(server)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(_state["warps"], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temp.replace(path)


def _find(name: str) -> tuple[str, dict] | None:
    """Case-insensitive lookup, so players do not have to match capitalisation."""
    warps = _state["warps"]
    if name in warps:
        return name, warps[name]
    lowered = name.lower()
    for key, value in warps.items():
        if key.lower() == lowered:
            return key, value
    return None


# -- commands ---------------------------------------------------------------

async def _cmd_list(source):
    warps = _state["warps"]
    if not warps:
        await source.reply(source.server.tr("list.empty"))
        return
    await source.reply(source.server.tr("list.header", count=len(warps)))
    for name, warp in sorted(warps.items()):
        await source.reply(source.server.tr(
            "list.entry", name=name, x=int(warp["x"]), y=int(warp["y"]),
            surface=warp.get("surface", "nauvis"),
        ))
    await source.reply(source.server.tr("list.hint"))


async def _cmd_goto(source, ctx):
    """Announce a warp as a clickable coordinate. Moves nobody."""
    name = ctx["name"].strip()
    found = _find(name)
    if found is None:
        await source.reply(source.server.tr("goto.not_found", name=name))
        return
    key, warp = found
    surface = warp.get("surface", "nauvis")
    tag = lua.gps(warp["x"], warp["y"], surface)

    if source.player:
        # Send it to the asker only: a ping for one person should not spam chat.
        await source.server.tell(source.player, source.server.tr("goto.told", name=key, gps=tag))
    else:
        await source.reply(source.server.tr(
            "goto.console", name=key, x=int(warp["x"]), y=int(warp["y"]), surface=surface))
    if warp.get("note"):
        await source.reply(f"  {warp['note']}")


async def _cmd_set(source, ctx):
    """Name the caller's current position."""
    name = ctx["name"].strip()
    if not name:
        await source.reply(source.server.tr("set.usage"))
        return
    if source.player is None:
        await source.reply(source.server.tr("set.console_only"))
        return
    if len(_state["warps"]) >= _state["config"].get("max_warps", 100) and not _find(name):
        await source.reply(source.server.tr("set.full", count=len(_state["warps"])))
        return

    server = source.server
    try:
        info = await server.get_player_info(source.player)
    except QueryError as exc:
        await source.reply(server.tr("set.read_failed", error=exc))
        return
    if not info or not info.get("position"):
        await source.reply(server.tr("set.no_position"))
        return

    position = info["position"]
    surface = info.get("surface") or "nauvis"
    _state["warps"][name] = {
        "x": position["x"], "y": position["y"], "surface": surface,
        "set_by": source.player, "set_at": time.time(),
    }
    _save_warps(server)

    x, y = int(position["x"]), int(position["y"])
    await source.reply(server.tr("set.done", name=name, x=x, y=y, surface=surface))

    if _state["config"].get("chart_tags", True):
        try:
            await server.add_map_marker(
                {"x": x, "y": y}, name, surface=surface,
                icon=_state["config"].get("chart_tag_icon"),
            )
        except QueryError as exc:
            server.logger.warning("Could not pin the warp on the map: %s", exc)

    await server.game_print(server.tr("set.announced", name=name, gps=lua.gps(x, y, surface)))


async def _cmd_delete(source, ctx):
    found = _find(ctx["name"].strip())
    if found is None:
        await source.reply(source.server.tr("delete.not_found", name=ctx["name"]))
        return
    key, _ = found
    del _state["warps"][key]
    _save_warps(source.server)
    await source.reply(source.server.tr("delete.done", name=key))
