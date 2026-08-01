"""Render an overview of the map and hand it to whoever asked.

    !!map              render it and say where it was written
    /map    (Telegram) render it and send the image

**Factorio cannot screenshot a headless server.** ``game.take_screenshot``
exists there and accepts the call without complaint, but writes no file --
there is no renderer in the process. So the map is not captured, it is drawn:
one row per chunk comes back from Lua, and the picture is composed here.

That means chunk resolution, 32x32 tiles per cell, which is what a thumbnail
wants anyway. Each chunk is coloured by what dominates it -- water, an ore
patch, or player construction -- and chart tags and connected players are
marked on top.
"""

from __future__ import annotations

import time
from pathlib import Path

from factorio_reforge.command.builder import Literal
from factorio_reforge.core.errors import QueryError
from factorio_reforge.core.png import Canvas
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "map_render",
    "version": "1.0.0",
    "name": "Map Render",
    "description": "Draw an overview of the map and send it to chat or Telegram",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    "surface": "nauvis",
    #: Pixels per sampled tile. 1 is a real map; raise it to zoom in.
    "pixels_per_tile": 1,
    #: Cap on the output, in pixels along the longer edge. When the world is
    #: bigger than this the sampling step is raised automatically, so a
    #: megabase produces a coarser map instead of a refusal or a 100 MB PNG.
    "max_dimension": 2000,
    #: Chunks per RCON round trip. Terrain is ~1 KB per chunk at step 1, so
    #: this keeps a single reply to a few hundred KB.
    "chunk_batch": 96,
    "show_trees": True,
    "show_resources": True,
    "show_players": True,
    "show_tags": True,
    #: Ignore a request within this many seconds of the last one.
    "cooldown_seconds": 20,
}

#: Terrain palette, keyed by the single character the Lua classifier emits.
TERRAIN = {
    "D": (26, 44, 74),     # deepwater
    "W": (42, 72, 108),    # water
    "S": (134, 116, 82),   # sand
    "E": (106, 74, 54),    # red desert
    "G": (62, 78, 44),     # grass
    "R": (80, 68, 52),     # dirt
    ".": (72, 66, 56),     # anything else
}
UNKNOWN_TERRAIN = (72, 66, 56)

TREE = (38, 60, 34)
RESOURCE_COLOURS = {
    "iron-ore": (122, 158, 186),
    "copper-ore": (196, 118, 68),
    "coal": (22, 22, 26),
    "stone": (166, 152, 116),
    "uranium-ore": (86, 172, 86),
    "crude-oil": (104, 72, 128),
}
UNKNOWN_RESOURCE = (150, 150, 150)
BUILT = (250, 214, 130)
TAG_COLOUR = (255, 255, 255)
PLAYER_COLOUR = (255, 64, 64)
CHUNK_TILES = 32

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, last=0.0, server=server)

    server.register_command(
        Literal("!!map").requires(PermissionLevel.USER).runs(_cmd_map)
    )
    server.register_help_message("!!map", "render an overview of the map")
    _register_telegram(server)
    server.register_event_listener("telegram.ready", lambda s: _register_telegram(s))


async def on_unload(server):
    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is not None:
        bridge.unregister_plugin("map_render")
    _state.clear()


def output_path(server) -> Path:
    return server.get_data_folder() / "map.png"


def latest_png(server=None) -> bytes | None:
    """The last render, for anything that wants to serve it -- the web panel."""
    server = server or _state.get("server")
    if server is None:
        return None
    path = output_path(server)
    return path.read_bytes() if path.is_file() else None


def _timestamped_name() -> str:
    """A distinct filename per render, so a chat keeps a history of them."""
    return time.strftime("factorio-map-%Y%m%d-%H%M.png")


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

async def render(server) -> tuple[bytes, dict]:
    """Query the world tile by tile and draw it. Returns ``(png, summary)``."""
    from factorio_reforge.core import lua

    config = _state["config"]
    surface = config.get("surface", "nauvis")

    chunks = await server.lua_json(lua.map_chunk_list(surface)) or []
    if not chunks:
        raise QueryError("the server reported no generated chunks")

    min_x = min(c[0] for c in chunks)
    max_x = max(c[0] for c in chunks)
    min_y = min(c[1] for c in chunks)
    max_y = max(c[1] for c in chunks)
    tiles_w = (max_x - min_x + 1) * CHUNK_TILES
    tiles_h = (max_y - min_y + 1) * CHUNK_TILES

    # Pick a sampling step that keeps the image within max_dimension. A step of
    # 1 is one pixel per tile; 2 halves each edge, and so on.
    scale = max(1, int(config.get("pixels_per_tile", 1)))
    limit = max(64, int(config.get("max_dimension", 2000)))
    step = 1
    while max(tiles_w, tiles_h) * scale // step > limit:
        step *= 2

    cells_per_chunk = len(range(0, CHUNK_TILES, step))
    width = (max_x - min_x + 1) * cells_per_chunk * scale
    height = (max_y - min_y + 1) * cells_per_chunk * scale
    canvas = Canvas(width, height, UNKNOWN_TERRAIN)

    def tile_to_pixel(world_x: float, world_y: float) -> tuple[int, int]:
        return (
            int((world_x - min_x * CHUNK_TILES) / step * scale),
            int((world_y - min_y * CHUNK_TILES) / step * scale),
        )

    # -- terrain, in batches so no single RCON reply is enormous
    batch_size = max(1, int(config.get("chunk_batch", 96)))
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        payload = await server.lua_json(lua.map_terrain(surface, batch, step)) or ""
        for row in payload.split(";"):
            if not row:
                continue
            cx, cy, chars = row.split(",", 2)
            base_x = (int(cx) - min_x) * cells_per_chunk
            base_y = (int(cy) - min_y) * cells_per_chunk
            for index, char in enumerate(chars):
                colour = TERRAIN.get(char, UNKNOWN_TERRAIN)
                px = (base_x + index % cells_per_chunk) * scale
                py = (base_y + index // cells_per_chunk) * scale
                canvas.fill_rect(px, py, scale, scale, colour)

    summary = {
        "chunks": len(chunks),
        "tiles_across": tiles_w,
        "step": step,
        "width": width,
        "height": height,
        "trees": 0,
        "resources": {},
        "built": 0,
        "tags": 0,
        "players": 0,
    }

    # -- overlays, drawn darkest first so the factory ends up on top
    if config.get("show_trees", True):
        trees = await _positions(server, lua, surface, "tree")
        summary["trees"] = len(trees)
        for x, y, _ in trees:
            px, py = tile_to_pixel(x, y)
            canvas.fill_rect(px, py, scale, scale, TREE)

    if config.get("show_resources", True):
        for x, y, name in await _positions(server, lua, surface, "resource"):
            px, py = tile_to_pixel(x, y)
            canvas.fill_rect(px, py, scale, scale,
                             RESOURCE_COLOURS.get(name, UNKNOWN_RESOURCE))
            summary["resources"][name] = summary["resources"].get(name, 0) + 1

    built = await _positions(server, lua, surface, "player")
    summary["built"] = len(built)
    for x, y, _ in built:
        px, py = tile_to_pixel(x, y)
        canvas.fill_rect(px, py, max(scale, 1), max(scale, 1), BUILT)

    markers = {"tags": [], "players": []}
    if config.get("show_tags", True) or config.get("show_players", True):
        try:
            markers = await server.lua_json(lua.map_markers(surface)) or markers
        except QueryError:
            pass

    if config.get("show_tags", True):
        for tag in markers.get("tags", []):
            canvas.cross(*tile_to_pixel(tag["x"], tag["y"]), 2, TAG_COLOUR)
        summary["tags"] = len(markers.get("tags", []))

    if config.get("show_players", True):
        for player in markers.get("players", []):
            canvas.dot(*tile_to_pixel(player["x"], player["y"]), 2, PLAYER_COLOUR)
        summary["players"] = len(markers.get("players", []))

    return canvas.to_png(), summary


async def _positions(server, lua, surface: str, kind: str) -> list[tuple[int, int, str]]:
    """Parse the ``"x,y[,name];..."`` payload into tuples."""
    try:
        payload = await server.lua_json(
            lua.map_entity_positions(surface, kind=kind)
        ) or ""
    except QueryError:
        return []

    out: list[tuple[int, int, str]] = []
    for entry in payload.split(";"):
        if not entry:
            continue
        parts = entry.split(",")
        if len(parts) < 2:
            continue
        try:
            out.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _cooldown_remaining() -> int:
    cooldown = _state["config"].get("cooldown_seconds", 20)
    if not cooldown:
        return 0
    elapsed = time.monotonic() - _state.get("last", 0.0)
    return max(0, int(cooldown - elapsed))


async def _cmd_map(source):
    remaining = _cooldown_remaining()
    if remaining:
        await source.reply(f"The map was just rendered -- try again in {remaining}s.")
        return

    server = source.server
    await source.reply("Rendering the map...")
    try:
        data, summary = await render(server)
    except QueryError as exc:
        await source.reply(f"Could not render the map: {exc}")
        return

    _state["last"] = time.monotonic()
    path = output_path(server)
    path.write_bytes(data)

    await source.reply(
        f"{summary['width']}x{summary['height']} px, {summary['tiles_across']:,} tiles "
        f"across at {summary['step']} tile(s) per pixel"
    )
    await source.reply(
        f"  {summary['built']:,} built entities, {summary['trees']:,} trees, "
        f"{summary['tags']} markers"
    )
    if summary["resources"]:
        top = sorted(summary["resources"].items(), key=lambda kv: -kv[1])[:5]
        await source.reply("  ore: " + ", ".join(f"{n} x{c}" for n, c in top))
    await source.reply(f"  written to {path}")

    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is not None and bridge.is_ready():
        # As a document: Telegram recompresses photos, and this map is one
        # pixel per tile, which is exactly the detail that would be lost.
        await bridge.broadcast_photo(
            data,
            caption=f"Map overview - {summary['tiles_across']:,} tiles across",
            filename=_timestamped_name(),
            as_document=True,
        )
        await source.reply("  also sent to Telegram")


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _register_telegram(server):
    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is None:
        return
    try:
        bridge.register_command(
            "map_render", "map", _tg_map, level="viewer", help="render the map"
        )
    except RuntimeError as exc:
        server.logger.debug("Could not register the Telegram command: %s", exc)


async def _tg_map(ctx):
    remaining = _cooldown_remaining()
    if remaining:
        await ctx.reply(f"The map was just rendered -- try again in {remaining}s.")
        return

    server = _telegram_server()
    if server is None:
        await ctx.reply("The map plugin is not ready.")
        return

    await ctx.reply("Rendering...")
    try:
        data, summary = await render(server)
    except QueryError as exc:
        await ctx.reply(f"Could not render the map: {exc}")
        return

    _state["last"] = time.monotonic()
    output_path(server).write_bytes(data)
    caption = (
        f"{summary['tiles_across']:,} tiles across · "
        f"{summary['built']:,} built entities · "
        f"{summary['players']} online"
    )
    await ctx.send_image_file(data, caption=caption, filename=_timestamped_name())


def _telegram_server():
    """The interface this plugin was loaded with.

    A Telegram handler has no CommandSource to take one from, so it is captured
    at load time.
    """
    return _state.get("server")
