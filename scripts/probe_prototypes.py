#!/usr/bin/env python3
"""Check the prototype API the calculator relies on against a running server.

The runtime docs describe the newest build, and this project targets whatever
you are actually running. That gap is not theoretical: on **2.0.77**,

    prototypes.entity['assembling-machine-3'].crafting_speed

raises *"LuaEntityPrototype doesn't contain key crafting_speed"* -- quality made
the value depend on an argument, so it is ``get_crafting_speed()`` -- while the
docs list ``crafting_speed`` as an attribute. ``belt_speed`` did not move.
Everything the calculator reads is tried here, both spellings where there are
two, so an upgrade that moves one of them shows up as a failed line rather than
as a plan quietly missing a machine.

Usage:
    python scripts/probe_prototypes.py [--host 127.0.0.1] [--port 27015] \
        [--password PW]

The password defaults to the one in config.yml.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from factorio_reforge.core import lua  # noqa: E402
from factorio_reforge.core.rcon import RconClient  # noqa: E402

#: label -> Lua expression. Anything the calculator reads should be here.
CHECKS = {
    "prototypes.recipe": "(function() local n = 0 "
                         "for _ in pairs(prototypes.recipe) do n = n + 1 end return n end)()",
    "recipe.energy": "prototypes.recipe['electronic-circuit'].energy",
    "recipe.ingredients": "prototypes.recipe['electronic-circuit'].ingredients",
    "recipe.products": "prototypes.recipe['electronic-circuit'].products",
    "recipe.category": "prototypes.recipe['electronic-circuit'].category",
    "recipe.categories": "prototypes.recipe['electronic-circuit'].categories",
    "recipe.maximum_productivity": "prototypes.recipe['electronic-circuit'].maximum_productivity",
    "force.recipes[].enabled": "game.forces.player.recipes['iron-plate'].enabled",
    "machine.crafting_speed": "prototypes.entity['assembling-machine-3'].crafting_speed",
    "machine.get_crafting_speed()": "prototypes.entity['assembling-machine-3'].get_crafting_speed()",
    "machine.crafting_categories": "prototypes.entity['assembling-machine-3'].crafting_categories",
    "machine.max_energy_usage": "prototypes.entity['assembling-machine-3'].max_energy_usage",
    "machine.get_max_energy_usage()": "prototypes.entity['assembling-machine-3'].get_max_energy_usage()",
    "machine.energy_usage": "prototypes.entity['assembling-machine-3'].energy_usage",
    "machine.module_inventory_size": "prototypes.entity['assembling-machine-3'].module_inventory_size",
    "machine.effect_receiver": "prototypes.entity['assembling-machine-3'].effect_receiver",
    "belt.belt_speed": "prototypes.entity['transport-belt'].belt_speed",
    "module.module_effects": "prototypes.item['productivity-module-3'].module_effects",
    "resource.mineable_properties": "prototypes.entity['iron-ore'].mineable_properties.products",
    "get_entity_filtered": "(function() local n = 0 "
                           "for _ in pairs(prototypes.get_entity_filtered("
                           "{{filter = 'type', type = 'assembling-machine'}})) do n = n + 1 end "
                           "return n end)()",
    "get_item_filtered": "(function() local n = 0 "
                         "for _ in pairs(prototypes.get_item_filtered("
                         "{{filter = 'type', type = 'module'}})) do n = n + 1 end return n end)()",
}


def default_password() -> str:
    config = REPO / "config.yml"
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("password:"):
                return stripped.split(":", 1)[1].strip()
    return ""


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27015)
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    rcon = RconClient(
        args.host, args.port, args.password or default_password(), command_timeout=30
    )
    await rcon.connect()

    failures = 0
    for label, expression in CHECKS.items():
        try:
            value = lua.parse_json_result(
                await rcon.execute("/sc " + lua.json_query(expression))
            )
        except Exception as exc:  # noqa: BLE001 -- reporting, not handling
            failures += 1
            print(f"MISSING  {label:32s} {str(exc)[:100]}")
        else:
            print(f"ok       {label:32s} {str(value)[:100]}")

    await rcon.close()
    # Some names are expected to be missing on any given build -- that is the
    # point of trying both spellings -- so this is a report, not a verdict.
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} present")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
