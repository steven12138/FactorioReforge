"""Real Factorio events, pushed out of the game instead of polled for.

This module exists because of a measurement that overturned an assumption the
project had been built on. The belief was that RCON cannot register event
handlers, so everything had to be polling. Measured on 2.0.77:

* ``script`` **is** available inside a ``/sc`` command, and
  ``script.on_event`` registers a handler that really fires -- verified by
  hooking ``on_tick``, writing ``game.tick`` into ``storage``, and reading it
  back one tick later;
* ``print()`` called **from inside that handler** reaches the server's stdout,
  which FactorioReforge is already parsing. ``game.print`` does not: it goes to
  in-game chat only.

So an event can travel game -> stdout -> parser -> plugin in one tick, with no
mod, no ``/c``, and no files. Research completion went from "up to 120 seconds
late" to "the tick it happened".

Three things make this safe rather than clever:

**Chain, never replace.** ``script.on_event`` overwrites whatever was there.
Measured: the freeplay scenario already has handlers on ``on_research_finished``
and ``on_player_created``. Replacing one breaks the scenario silently, so every
handler here captures the previous one with ``script.get_event_handler`` and
calls it first.

**Register once per server start.** Handlers live in the level script and do not
survive a save/load, so they have to be re-registered -- but registering twice
in one session makes our own wrapper the "previous" handler and every event
prints twice. FactorioReforge knows when it started the server, so the count is
kept here rather than in the game.

**JSON, not string formatting.** Player and entity names can contain anything;
a hand-rolled separator would work until the day somebody is called ``a b``.
``helpers.table_to_json`` is already how every other query comes back.
"""

from __future__ import annotations

import json

from factorio_reforge.core import lua

#: Marks a line as ours. A bare line starting with this can only have come from
#: our own ``print``: chat and engine output both carry a timestamp prefix, so
#: there is nothing for it to collide with.
SENTINEL = "@FRE@"

#: Events worth bridging, and what to send with each. Keys are
#: ``defines.events`` names; values are Lua expressions evaluated against the
#: event table ``e``. Deliberately small: ``on_entity_died`` fires thousands of
#: times a minute on a defended base and would turn stdout into a firehose.
BRIDGED: dict[str, dict[str, str]] = {
    "on_research_finished": {
        "name": "e.research.name",
        "level": "e.research.level",
        "force": "e.research.force.name",
        "by_script": "e.by_script and true or false",
    },
    "on_rocket_launched": {
        "force": "e.rocket_silo and e.rocket_silo.force.name or nil",
        "launched": "e.rocket_silo and e.rocket_silo.force.rockets_launched or nil",
    },
    # The [DEATH] line already tells us who died. What it does not carry is
    # where, which is the only part anyone actually needs.
    "on_player_died": {
        "player": "game.get_player(e.player_index) and game.get_player(e.player_index).name or nil",
        "x": "game.get_player(e.player_index) and game.get_player(e.player_index).position.x or nil",
        "y": "game.get_player(e.player_index) and game.get_player(e.player_index).position.y or nil",
        "surface": "game.get_player(e.player_index) "
                   "and game.get_player(e.player_index).surface.name or nil",
        "cause": "e.cause and e.cause.name or nil",
    },
}


class UnknownEvent(ValueError):
    """Asked to bridge an event this module has no payload for."""


def build_registration(names: list[str]) -> str:
    """Lua that installs a chained handler for each of ``names``.

    Returns an expression, so it can go through the same ``lua_json`` path as
    every other query and report back which events it actually hooked -- an
    event name that does not exist in this Factorio version is skipped rather
    than raising, because a version bump should cost a feature and not the
    server.
    """
    unknown = [name for name in names if name not in BRIDGED]
    if unknown:
        raise UnknownEvent(f"no payload defined for: {', '.join(sorted(unknown))}")

    blocks = []
    for name in names:
        fields = ", ".join(
            f"[{lua.lua_string(key)}] = {expr}" for key, expr in BRIDGED[name].items()
        )
        blocks.append(
            f"  local id = defines.events.{name} "
            "  if id ~= nil then "
            "    local prev = script.get_event_handler(id) "
            "    script.on_event(id, function(e) "
            # The scenario's own handler runs first and runs regardless: if our
            # payload ever raises, the game must not lose its own behaviour.
            "      if prev then prev(e) end "
            f"      local ok, line = pcall(function() return {lua.lua_string(SENTINEL)} .. "
            f"        helpers.table_to_json({{event = {lua.lua_string(name)}, {fields}}}) end) "
            "      if ok then print(line) end "
            "    end) "
            f"    hooked[#hooked + 1] = {lua.lua_string(name)} "
            "  end "
        )

    return (
        "(function() local hooked = {} "
        + " ".join(f"do {block} end" for block in blocks)
        + " return {hooked = hooked} end)()"
    )


def build_removal(names: list[str]) -> str:
    """Lua that unhooks the given events.

    Note what this cannot do: the previous handler was captured in a closure and
    there is no way to put it back, so this removes the scenario's handler too.
    Only for a server that is about to stop, where the next load re-runs
    ``control.lua`` and restores everything.
    """
    parts = [
        f"do local id = defines.events.{name} "
        "if id ~= nil then script.on_event(id, nil) end end"
        for name in names
        if name in BRIDGED
    ]
    return "(function() " + " ".join(parts) + " return {removed = true} end)()"


def parse_line(content: str) -> dict | None:
    """The payload of one bridged event, or None if this is an ordinary line.

    Never raises: a malformed line is somebody else's ``print`` that happens to
    start with the sentinel, and losing an event is better than taking the
    parser down.
    """
    if not content.startswith(SENTINEL):
        return None
    try:
        payload = json.loads(content[len(SENTINEL):])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("event"):
        return None
    return payload
