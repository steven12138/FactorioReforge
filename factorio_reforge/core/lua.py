"""Build Lua for RCON, and get structured data back instead of scraped text.

``rcon.print`` is the only way a command returns anything, and it returns a
string. Wrapping every reply in ``helpers.table_to_json`` turns that string into
JSON we can parse, so a query yields real numbers and lists rather than text a
regex has to pick apart.

Everything here was checked against a live Factorio **2.0.77** server. A few 1.1
spellings no longer exist and are called out where they bite:

* ``game.table_to_json`` is gone -- it is ``helpers.table_to_json`` in 2.0.
* ``force.get_evolution_factor`` now takes a surface.
* ``force.item_production_statistics`` is now
  ``force.get_item_production_statistics(surface)``.
"""

from __future__ import annotations

import json
from typing import Any

from factorio_reforge.core.errors import QueryError

#: Characters Lua's own escape syntax handles; everything else outside plain
#: ASCII is emitted as decimal escapes.
_SIMPLE_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\0": "\\0",
}


def lua_string(value: Any) -> str:
    """Quote a Python value as a Lua string literal.

    ``json.dumps`` is not usable here: it escapes non-ASCII as ``\\uXXXX``, and
    Factorio runs Lua 5.2, which has no ``\\u`` escape. Decimal escapes of the
    UTF-8 bytes work in every Lua version and survive player names in any script.
    """
    text = "" if value is None else str(value)
    out = ['"']
    for char in text:
        if char in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[char])
        elif " " <= char <= "~":
            out.append(char)
        else:
            out.extend(f"\\{byte}" for byte in char.encode("utf-8"))
    out.append('"')
    return "".join(out)


def lua_value(value: Any) -> str:
    """Render a Python value as Lua source: nil, boolean, number, string, table."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, (list, tuple)):
        return "{" + ",".join(lua_value(item) for item in value) + "}"
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            key_source = f"[{lua_value(key)}]" if not _is_identifier(key) else str(key)
            parts.append(f"{key_source}={lua_value(item)}")
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"cannot express {type(value).__name__} as Lua")


def _is_identifier(key: Any) -> bool:
    return isinstance(key, str) and key.isidentifier()


def json_query(body: str) -> str:
    """Wrap a Lua expression so its value comes back as JSON.

    ``body`` must be an expression, not statements. Errors are caught and
    returned as ``{"__error": "..."}`` so a mistake in a snippet surfaces as a
    readable message instead of Factorio's bare "Cannot execute command".
    """
    return (
        "local ok, result = pcall(function() return %s end) "
        "if ok then rcon.print(helpers.table_to_json({ok=true, value=result})) "
        "else rcon.print(helpers.table_to_json({ok=false, error=tostring(result)})) end"
    ) % body


class LuaError(QueryError):
    """The Lua ran but raised, or returned something that was not JSON."""


def parse_json_result(raw: str) -> Any:
    """Unpack what :func:`json_query` produced."""
    text = (raw or "").strip()
    if not text:
        raise LuaError("the server returned nothing (is allow_commands disabled?)")
    if text.startswith("Cannot execute command"):
        raise LuaError(text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LuaError(f"expected JSON, got {text[:200]!r}") from exc
    if not isinstance(payload, dict) or "ok" not in payload:
        raise LuaError(f"unexpected reply shape: {text[:200]!r}")
    if not payload["ok"]:
        raise LuaError(payload.get("error", "unknown Lua error"))
    return payload.get("value")


# ---------------------------------------------------------------------------
# A vetted snippet library. Each returns a Lua *expression* for json_query.
# ---------------------------------------------------------------------------

def online_players() -> str:
    return (
        "(function() local t = {} "
        "for _, p in pairs(game.connected_players) do "
        "t[#t+1] = {name = p.name, admin = p.admin, "
        "online_time = p.online_time, "
        "position = p.physical_position, "
        "surface = p.physical_surface.name} end "
        "return t end)()"
    )


def all_players() -> str:
    """Everyone who has ever joined, with playtime and last-seen tick."""
    return (
        "(function() local t = {} "
        "for _, p in pairs(game.players) do "
        "t[#t+1] = {name = p.name, admin = p.admin, connected = p.connected, "
        "online_time = p.online_time, last_online = p.last_online} end "
        "return t end)()"
    )


def player_info(name: str) -> str:
    return (
        "(function() local p = game.get_player(%s) "
        "if not p then return nil end "
        "return {name = p.name, admin = p.admin, connected = p.connected, "
        "online_time = p.online_time, last_online = p.last_online, "
        "position = p.connected and p.physical_position or nil, "
        "surface = p.connected and p.physical_surface.name or nil, "
        "force = p.force.name} end)()"
    ) % lua_string(name)


def server_stats() -> str:
    """World-level numbers worth putting in a status line."""
    return (
        "(function() "
        "local s = game.surfaces[1] "
        "local f = game.forces.player "
        "return {tick = game.tick, ticks_played = game.ticks_played, "
        "speed = game.speed, daytime = s.daytime, "
        "pollution = s.get_total_pollution(), "
        "evolution = game.forces.enemy.get_evolution_factor(s), "
        "research = f.current_research and f.current_research.name or nil, "
        "research_progress = f.research_progress, "
        "players_total = #game.players, players_online = #game.connected_players, "
        "surface = s.name} end)()"
    )


def add_map_marker(
    surface: str, position: dict, text: str, icon: dict | None = None
) -> str:
    """Drop a chart tag -- a persistent marker on everyone's map.

    Complements rather than replaces :func:`gps`: a ``[gps=...]`` tag in chat is
    clickable and pings the spot *now*, while a chart tag stays on the map until
    someone deletes it. Use gps for "look here", a chart tag for "this place has
    a name".

    The API can return ``nil`` for a position it rejects -- the docs say the
    chunk must be charted, though 2.0.77 accepted uncharted positions in
    testing. Either way the caller must handle ``None`` rather than assume a
    marker was placed.
    """
    spec: dict[str, Any] = {"position": position, "text": text}
    if icon:
        spec["icon"] = icon
    return (
        "(function() local t = game.forces.player.add_chart_tag("
        "game.get_surface(%s), %s) "
        "if not t then return nil end "
        "return {tag_number = t.tag_number, position = t.position} end)()"
    ) % (lua_string(surface), lua_value(spec))


def teleport(player: str, position: dict, surface: str | None = None) -> str:
    return (
        "(function() local p = game.get_player(%s) "
        "if not p then return {ok = false, reason = 'no such player'} end "
        "if not p.connected then return {ok = false, reason = 'not online'} end "
        "local surf = %s "
        "local ok = p.teleport(%s, surf) "
        "return {ok = ok, position = p.physical_position} end)()"
    ) % (
        lua_string(player),
        f"game.get_surface({lua_string(surface)})" if surface else "nil",
        lua_value(position),
    )


def entity_count(name: str) -> str:
    return "game.forces.player.get_entity_count(%s)" % lua_string(name)


def item_produced(name: str) -> str:
    return (
        "game.forces.player.get_item_production_statistics(game.surfaces[1])"
        ".get_input_count(%s)"
    ) % lua_string(name)


def print_to_all(message: str) -> str:
    """``game.print`` rather than stdin chat, so it is not echoed back at us."""
    return "(function() game.print(%s) return true end)()" % lua_string(message)


def localised_name(name: str) -> list:
    """A LocalisedString that renders an internal name in the reader's language.

    ``iron-plate`` is not a word in any language, and a production plan written
    in prototype ids is unreadable to anyone not already fluent in them. Factorio
    solves this itself: pass a **LocalisedString** to ``game.print`` and every
    client renders it locally, so the same message is Chinese for one player and
    English for the next -- which is better than anything this side could do,
    because there is no server-side translation table to go stale.

    The ``?`` form takes the first alternative that resolves, which is how one
    token covers items, fluids, entities and recipes without knowing which it
    is. The bare name is the last alternative, so an unknown key degrades to
    what would have been printed anyway.
    """
    return [
        "?",
        [f"item-name.{name}"],
        [f"fluid-name.{name}"],
        [f"entity-name.{name}"],
        [f"recipe-name.{name}"],
        name,
    ]


def print_localised_to_all(parts: list) -> str:
    return "(function() game.print(%s) return true end)()" % lua_value(parts)


def print_localised_to_player(player: str, parts: list) -> str:
    return (
        "(function() local p = game.get_player(%s) "
        "if not p then return false end p.print(%s) return true end)()"
    ) % (lua_string(player), lua_value(parts))


def print_to_player(player: str, message: str) -> str:
    return (
        "(function() local p = game.get_player(%s) "
        "if not p then return false end p.print(%s) return true end)()"
    ) % (lua_string(player), lua_string(message))


# ---------------------------------------------------------------------------
# Rich text
#
# Factorio chat renders inline tags, and `[gps=...]` is *clickable*: it pings
# the position on everyone's map. That makes it the true equivalent of
# MCDReforged's clickable coordinates, better than a chart tag for "look here
# right now" -- verified accepted by game.print on 2.0.77.
# ---------------------------------------------------------------------------

def gps(x: float, y: float, surface: str = "nauvis") -> str:
    """A clickable coordinate. Clicking it pings that spot on the map."""
    return f"[gps={int(x)},{int(y)},{surface}]"


def item_tag(name: str) -> str:
    return f"[item={name}]"


def entity_tag(name: str) -> str:
    return f"[entity={name}]"


def technology_tag(name: str) -> str:
    return f"[technology={name}]"


def colored(text: str, color: str) -> str:
    """Wrap in a colour tag. ``color`` is a name or ``r,g,b`` floats."""
    return f"[color={color}]{text}[/color]"


# ---------------------------------------------------------------------------
# Production statistics
# ---------------------------------------------------------------------------

#: defines.flow_precision_index, read off a live 2.0.77 server.
FLOW_PRECISION = {
    "five_seconds": 0, "one_minute": 1, "ten_minutes": 2, "one_hour": 3,
    "ten_hours": 4, "fifty_hours": 5, "two_hundred_fifty_hours": 6,
    "one_thousand_hours": 7,
}


def production_totals(surface: str = "nauvis", limit: int = 40) -> str:
    """Cumulative produced/consumed counts for every item, biggest first."""
    return (
        "(function() local s = game.forces.player.get_item_production_statistics("
        "game.get_surface(%s)) "
        "local rows = {} "
        "for name, count in pairs(s.input_counts) do "
        "rows[#rows+1] = {name = name, produced = count, "
        "consumed = s.output_counts[name] or 0} end "
        "table.sort(rows, function(a, b) return a.produced > b.produced end) "
        "local top = {} for i = 1, math.min(#rows, %d) do top[i] = rows[i] end "
        "return top end)()"
    ) % (lua_string(surface), int(limit))


def production_rate(
    item: str, precision: str = "one_minute", surface: str = "nauvis"
) -> str:
    """Produced and consumed counts over one time window.

    ``precision`` selects the window: ``five_seconds`` through
    ``one_thousand_hours``. This is the windowed figure the in-game production
    graph shows, not a running total.
    """
    index = FLOW_PRECISION.get(precision, 1)
    return (
        "(function() local s = game.forces.player.get_item_production_statistics("
        "game.get_surface(%s)) "
        "return {item = %s, "
        "produced = s.get_flow_count{name = %s, category = 'input', precision_index = %d}, "
        "consumed = s.get_flow_count{name = %s, category = 'output', precision_index = %d}, "
        "total_produced = s.input_counts[%s] or 0} end)()"
    ) % (
        lua_string(surface), lua_string(item),
        lua_string(item), index, lua_string(item), index, lua_string(item),
    )


def kill_counts(surface: str = "nauvis", limit: int = 20) -> str:
    return (
        "(function() local s = game.forces.player.get_kill_count_statistics("
        "game.get_surface(%s)) "
        "local rows = {} "
        "for name, count in pairs(s.input_counts) do rows[#rows+1] = {name = name, kills = count} end "
        "table.sort(rows, function(a, b) return a.kills > b.kills end) "
        "local top = {} for i = 1, math.min(#rows, %d) do top[i] = rows[i] end "
        "return top end)()"
    ) % (lua_string(surface), int(limit))


# ---------------------------------------------------------------------------
# Progress and milestones
# ---------------------------------------------------------------------------

def research_state() -> str:
    """Counts, the current subject, and the most recently finished technology."""
    return (
        "(function() local f = game.forces.player "
        "local total, done = 0, 0 "
        "for _, tech in pairs(f.technologies) do "
        "total = total + 1 if tech.researched then done = done + 1 end end "
        "return {total = total, researched = done, "
        "current = f.current_research and f.current_research.name or nil, "
        "progress = f.research_progress, "
        "rockets_launched = f.rockets_launched} end)()"
    )


def researched_technologies() -> str:
    return (
        "(function() local t = {} "
        "for name, tech in pairs(game.forces.player.technologies) do "
        "if tech.researched then t[#t+1] = name end end "
        "return t end)()"
    )


def world_alerts(surface: str = "nauvis") -> str:
    """The numbers a watcher plugin polls for threshold crossings."""
    return (
        "(function() local s = game.get_surface(%s) "
        "return {evolution = game.forces.enemy.get_evolution_factor(s), "
        "pollution = s.get_total_pollution(), "
        "tick = game.tick, "
        "rockets_launched = game.forces.player.rockets_launched} end)()"
    ) % lua_string(surface)


# ---------------------------------------------------------------------------
# Blueprints
#
# A blueprint string can be produced and consumed entirely server-side through a
# scratch inventory, so a shared library needs no client involvement at all.
# ---------------------------------------------------------------------------

def capture_blueprint(
    surface: str, area: dict, *, snap_grid: bool = False
) -> str:
    """Blueprint an area of the map and return the exported string."""
    return (
        "(function() local inv = game.create_inventory(1) "
        "inv[1].set_stack{name = 'blueprint'} "
        "local entities = inv[1].create_blueprint{surface = game.get_surface(%s), "
        "force = 'player', area = %s, always_include_tiles = true} "
        "local count = table_size(entities) "
        "local text = count > 0 and inv[1].export_stack() or nil "
        "inv.destroy() "
        "return {entities = count, blueprint = text} end)()"
    ) % (lua_string(surface), lua_value(area))


def give_blueprint(player: str, blueprint: str) -> str:
    """Put a blueprint string into a player's inventory.

    ``import_stack`` returns 0 on success; anything else means Factorio rejected
    the string, which is reported rather than silently handing over an empty
    blueprint.
    """
    return (
        "(function() local p = game.get_player(%s) "
        "if not p then return {ok = false, reason = 'no such player'} end "
        "if not p.connected then return {ok = false, reason = 'not online'} end "
        "local inv = game.create_inventory(1) "
        "inv[1].set_stack{name = 'blueprint'} "
        "local result = inv[1].import_stack(%s) "
        "if result ~= 0 then inv.destroy() "
        "return {ok = false, reason = 'Factorio rejected the blueprint string'} end "
        "local given = p.insert(inv[1]) "
        "inv.destroy() "
        "return {ok = given > 0, reason = given > 0 and '' or 'inventory is full'} end)()"
    ) % (lua_string(player), lua_string(blueprint))


#: Everything ``export_stack`` works on, which is more than blueprints.
BLUEPRINT_KINDS = ("blueprint", "blueprint-book", "deconstruction-planner", "upgrade-planner")


def export_held_blueprint(player: str) -> str:
    """Export whatever the player is holding, as a string.

    Two things can be in a cursor and only one of them is a stack. A blueprint
    taken out of your *personal library* is a ``cursor_record``, not a
    ``cursor_stack``, and reading only the stack makes the library case look
    like an empty hand. Both are tried.

    Books and planners export through the same call as blueprints, so they are
    all accepted; the kind is reported so the caller can say what it stored.
    """
    return (
        "(function() "
        "local function safe(f) local ok, v = pcall(f) if ok then return v end end "
        "local p = game.get_player(%s) "
        "if not p then return {ok = false, reason = 'no such player'} end "
        "if not p.connected then return {ok = false, reason = 'not online'} end "
        "local held = safe(function() return p.cursor_stack end) "
        "if held and not held.valid_for_read then held = nil end "
        "if not held then held = safe(function() return p.cursor_record end) end "
        "if not held then return {ok = false, reason = 'empty hand'} end "
        "local text = safe(function() return held.export_stack() end) "
        "if not text or text == '' then "
        "  return {ok = false, reason = 'not a blueprint', "
        "          name = safe(function() return held.name end)} end "
        "local kind = safe(function() return held.name end) "
        "local entities = safe(function() return held.get_blueprint_entities() end) "
        "local counts = {} "
        "for _, e in pairs(entities or {}) do "
        "  counts[e.name] = (counts[e.name] or 0) + 1 end "
        "return {ok = true, blueprint = text, kind = kind, "
        "        label = safe(function() return held.label end), "
        "        entities = entities and #entities or 0, counts = counts} end)()"
    ) % lua_string(player)


def give_blueprint_to_cursor(
    player: str, blueprint: str, kind: str | None = None
) -> str:
    """Put a blueprint straight into the player's hand, ready to place.

    The point of a shared library is to hand someone the thing, not to make them
    go and find it in their inventory. A cursor already holding something is
    never overwritten -- that would destroy whatever they were building with --
    so it falls back to the inventory and says which happened.

    ``kind`` is what was recorded when the entry was saved. On 2.0.77
    ``import_stack`` converts the stack to match the string -- importing a book
    into a ``blueprint`` stack leaves a ``blueprint-book`` behind and returns 0 --
    so the first attempt is enough there. The other kinds are tried after it
    because that conversion is undocumented, and a library entry saved before
    kinds were recorded has no preference to offer.
    """
    order = [kind] if kind in BLUEPRINT_KINDS else []
    order += [name for name in BLUEPRINT_KINDS if name != kind]
    kinds = "{" + ",".join(lua_string(name) for name in order) + "}"
    return (
        "(function() "
        "local function safe(f) local ok, v = pcall(f) if ok then return v end end "
        "local p = game.get_player(%s) "
        "if not p then return {ok = false, reason = 'no such player'} end "
        "if not p.connected then return {ok = false, reason = 'not online'} end "
        "local inv = game.create_inventory(1) "
        "local loaded = false "
        "for _, kind in pairs(%s) do "
        "  if safe(function() inv[1].set_stack{name = kind} return true end) "
        "     and inv[1].import_stack(%s) == 0 then loaded = true break end "
        "end "
        "if not loaded then inv.destroy() "
        "  return {ok = false, reason = 'Factorio rejected the blueprint string'} end "
        "local cursor = safe(function() return p.cursor_stack end) "
        "if cursor and not cursor.valid_for_read then "
        "  local placed = safe(function() return cursor.set_stack(inv[1]) end) "
        "  if placed then inv.destroy() return {ok = true, where = 'cursor'} end "
        "end "
        "local given = p.insert(inv[1]) "
        "inv.destroy() "
        "return {ok = given > 0, where = 'inventory', "
        "        reason = given > 0 and '' or 'inventory is full'} end)()"
    ) % (lua_string(player), kinds, lua_string(blueprint))


def validate_blueprint(blueprint: str) -> str:
    """Check a string parses, and report what is in it, without giving it away."""
    return (
        "(function() local inv = game.create_inventory(1) "
        "inv[1].set_stack{name = 'blueprint'} "
        "local result = inv[1].import_stack(%s) "
        "if result ~= 0 then inv.destroy() return {ok = false} end "
        "local entities = inv[1].get_blueprint_entities() "
        "local counts = {} "
        "for _, e in pairs(entities or {}) do "
        "counts[e.name] = (counts[e.name] or 0) + 1 end "
        "local label = inv[1].label "
        "inv.destroy() "
        "return {ok = true, label = label, entities = entities and #entities or 0, "
        "counts = counts} end)()"
    ) % lua_string(blueprint)


# ---------------------------------------------------------------------------
# Map overview
#
# A headless server has no renderer: game.take_screenshot exists and accepts the
# call, but produces no file (measured on 2.0.77). So the map is summarised into
# one row per chunk and drawn on our side instead. A chunk is 32x32 tiles, which
# is exactly the resolution a thumbnail wants.
# ---------------------------------------------------------------------------

def map_chunk_list(surface: str = "nauvis") -> str:
    """Just the coordinates of every generated chunk, to size the image."""
    return (
        "(function() local s = game.get_surface(%s) local out = {} "
        "for c in s.get_chunks() do out[#out+1] = {c.x, c.y} end "
        "return out end)()"
    ) % lua_string(surface)


#: Tile classes, chosen so the map reads like the in-game one at a glance.
#: Factorio has dozens of tile variants (dirt-1..7, red-desert-0..3); grouping
#: them by name keeps the payload to one character per tile.
TERRAIN_CLASSIFY = (
    "(n:find('deepwater') and 'D') or (n:find('water') and 'W') "
    "or (n:find('sand') and 'S') or (n:find('desert') and 'E') "
    "or (n:find('grass') and 'G') or (n:find('dirt') and 'R') or '.'"
)


def map_terrain(surface: str, chunks: list, step: int = 1) -> str:
    """Terrain for a batch of chunks, one character per sampled tile.

    Returned as ``"cx,cy,<chars>;cx,cy,<chars>"`` rather than JSON: at one
    character per tile the field names of a structured form would be most of
    the payload. A whole 409-chunk world comes back in 421 KB at step 1, in
    about half a second, so batching exists to keep any single RCON reply
    modest rather than because the query is slow.
    """
    coords = ",".join(f"{{{int(x)},{int(y)}}}" for x, y in chunks)
    return (
        "(function() local s = game.get_surface(%s) local rows = {} "
        "for _, c in pairs({%s}) do local buf = {} "
        "local ox, oy = c[1] * 32, c[2] * 32 "
        "for y = 0, 31, %d do for x = 0, 31, %d do "
        "local n = s.get_tile(ox + x, oy + y).name "
        "buf[#buf+1] = %s end end "
        "rows[#rows+1] = c[1] .. ',' .. c[2] .. ',' .. table.concat(buf) end "
        "return table.concat(rows, ';') end)()"
    ) % (lua_string(surface), coords, int(step), int(step), TERRAIN_CLASSIFY)


def map_entity_positions(surface: str, *, kind: str, limit: int = 200000) -> str:
    """Positions of one entity family, as ``"x,y,name;..."``.

    ``kind`` is ``resource``, ``tree``, or ``player`` for anything the player
    force built -- the last being the part that actually shows the factory.
    """
    if kind == "player":
        query = "{force = 'player', limit = %d}" % int(limit)
        with_name = "false"
    else:
        query = "{type = %s, limit = %d}" % (lua_string(kind), int(limit))
        with_name = "true" if kind == "resource" else "false"

    return (
        "(function() local s = game.get_surface(%s) local out = {} "
        "for _, e in pairs(s.find_entities_filtered%s) do "
        "out[#out+1] = math.floor(e.position.x) .. ',' .. math.floor(e.position.y) "
        "%s end "
        "return table.concat(out, ';') end)()"
    ) % (
        lua_string(surface),
        query,
        ".. ',' .. e.name" if with_name == "true" else "",
    )


def map_markers(surface: str = "nauvis") -> str:
    """Chart tags and connected players, for annotating the rendered map."""
    return (
        "(function() local s = game.get_surface(%s) local tags = {} "
        "for _, t in pairs(game.forces.player.find_chart_tags(s)) do "
        "tags[#tags+1] = {x = t.position.x, y = t.position.y, text = t.text} end "
        "local players = {} "
        "for _, p in pairs(game.connected_players) do "
        "if p.physical_surface == s then "
        "players[#players+1] = {x = p.physical_position.x, y = p.physical_position.y, "
        "name = p.name} end end "
        "return {tags = tags, players = players} end)()"
    ) % lua_string(surface)
