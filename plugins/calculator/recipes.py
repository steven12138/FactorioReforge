"""Recipe and machine data, read out of the running game.

Every other Factorio calculator ships a data dump scraped from a particular
version, which is why they all have a version dropdown and why none of them
knows about your mods. This one asks the server, over the same RCON channel
everything else here uses:

    prototypes.recipe / prototypes.get_entity_filtered{...}

so the numbers are the ones your server will actually run, including whatever
the mod list does to them, and there is no table in this repository that can go
stale. The cost is that ``!!ratio`` needs the server up. That is the honest
trade: a wrong ratio delivered while the server is down is worse than a "start
the server first".

The graph is fetched breadth-first, one round trip per level, rather than
dumping all ~2000 recipes at once. A science pack closes in about eight levels
and a few kilobytes; the whole dump is a megabyte of JSON for one question.

API notes, all of them things that moved in 2.0 and would silently return nil:

* ``game.recipe_prototypes`` is now ``prototypes.recipe``.
* a recipe's category is ``category`` on 2.0 and ``categories`` (an array) on
  later builds, so both are read and whichever exists is used.
* ``module_effects`` entries changed shape in 2.1.12 -- ``{speed = 0.2}`` versus
  the older ``{speed = {bonus = 0.2}}`` -- so both are accepted.
* productivity built into a machine (foundry, electromagnetic plant, biochamber)
  lives in ``effect_receiver.base_effect``, which does not exist on every
  prototype and is read through a pcall for that reason.
* which machines a save can actually *place* is worked out from the force's
  enabled recipes -- a machine whose item no researched recipe produces cannot
  be built, and answering "use an assembling machine 3" on a save that has not
  researched them is the single most useless thing this can say. A machine
  already standing on the map counts too, since somebody clearly has one.
* what a **mining drill** yields is read from ``mineable_properties`` on every
  ``resource`` entity, and it matters more than it sounds. With Space Age data
  loaded, iron ore is a *product* -- of asteroid crushing -- so "nothing makes
  it" stops being the test for a raw material, and the chunks themselves are
  produced only by reprocessing each other. Asking the game what comes out of
  the ground is what keeps a plan for circuits from being denominated in
  asteroids.
* **``crafting_speed`` is a method, not an attribute.** Quality made the value
  depend on an argument, so 2.0.77 has ``get_crafting_speed()`` and reading
  ``.crafting_speed`` raises *"LuaEntityPrototype doesn't contain key
  crafting_speed"*. Same for ``get_max_energy_usage()``. ``belt_speed`` is still
  a plain attribute. Measured on the server, not read off the docs, which
  describe a later build; both spellings are tried in that order.

Everything here was checked against a live 2.0.77 server -- see
``scripts/probe_prototypes.py``, which re-runs the check.
"""

from __future__ import annotations

import dataclasses
from fractions import Fraction

from factorio_reforge.core import lua

from .solver import Machine, Number, Recipe

#: Belt throughput = belt_speed (tiles/tick) x 60 ticks x 8 items per tile
#: (4 per lane, 2 lanes). A yellow belt is 0.03125 x 480 = 15 items/s, which is
#: the number on the wiki, so the derivation is right.
BELT_ITEMS_PER_TILE = 8
TICKS_PER_SECOND = 60

#: ``max_energy_usage`` is documented in energy per tick, so a machine drawing
#: 375 kW reports 6250. Everything user-facing is watts.
JOULES_PER_TICK_TO_WATTS = 60

_SAFE = (
    "local function safe(f) local ok, v = pcall(f) if ok then return v end return nil end "
)


def static_data() -> str:
    """Machines, belts and modules -- everything that does not depend on the query."""
    return (
        "(function() " + _SAFE +
        "local machines = {} "
        "for _, kind in pairs({'assembling-machine', 'furnace', 'rocket-silo'}) do "
        "  for name, e in pairs(prototypes.get_entity_filtered({{filter = 'type', type = kind}})) do "
        "    local cats = {} "
        "    for c in pairs(safe(function() return e.crafting_categories end) or {}) do "
        "      cats[#cats + 1] = c end "
        "    local prod = 0 "
        "    local recv = safe(function() return e.effect_receiver end) "
        "    if recv and recv.base_effect and recv.base_effect.productivity then "
        "      prod = recv.base_effect.productivity end "
        "    local speed = safe(function() return e.get_crafting_speed() end) "
        "      or safe(function() return e.crafting_speed end) "
        "    local energy = safe(function() return e.get_max_energy_usage() end) "
        "      or safe(function() return e.max_energy_usage end) "
        "      or safe(function() return e.energy_usage end) "
        "    if speed then "
        "      machines[name] = {speed = speed, categories = cats, "
        "        slots = safe(function() return e.module_inventory_size end) or 0, "
        "        energy = energy or 0, productivity = prod, kind = kind} "
        "    end "
        "  end "
        "end "
        "local belts = {} "
        "for name, e in pairs(prototypes.get_entity_filtered({{filter = 'type', type = 'transport-belt'}})) do "
        "  belts[name] = e.belt_speed "
        "end "
        "local modules = {} "
        "for name, it in pairs(prototypes.get_item_filtered({{filter = 'type', type = 'module'}})) do "
        "  modules[name] = safe(function() return it.module_effects end) or {} "
        "end "
        "local buildable = {} "
        "local made = {} "
        # Recycling is why this needs a filter at all. Every machine has an
        # X-recycling recipe, those are enabled from the start, and recycling
        # an assembling machine 3 yields an assembling machine 2 -- so a save
        # that has researched neither reports tier 2 as buildable. Measured on
        # the live server: assembling-machine-2 and captive-biter-spawner both
        # came back "unlocked" from their recycling recipes alone.
        "local skip = {" + ", ".join(
            f"[{lua.lua_string(category)}] = true" for category in EXCLUDED_CATEGORIES
        ) + "} "
        "for name, r in pairs(game.forces.player.recipes) do "
        "  if r.enabled and not skip[r.category] then "
        "    for _, p in pairs(r.products) do made[p.name] = true end "
        "  end "
        "end "
        "for name in pairs(machines) do "
        "  local e = prototypes.entity[name] "
        "  local ok = false "
        "  for _, stack in pairs(safe(function() return e.items_to_place_this end) or {}) do "
        "    if made[stack.name or stack] then ok = true end "
        "  end "
        "  if not ok then "
        "    ok = (safe(function() "
        "      return game.surfaces[1].count_entities_filtered{name = name} end) or 0) > 0 "
        "  end "
        "  if ok then buildable[#buildable + 1] = name end "
        "end "
        "local mined = {} "
        "for _, kind in pairs({'resource', 'plant'}) do "
        "  for _, e in pairs(prototypes.get_entity_filtered({{filter = 'type', type = kind}})) do "
        "    local m = safe(function() return e.mineable_properties end) "
        "    for _, p in pairs((m and m.products) or {}) do mined[#mined + 1] = p.name end "
        "  end "
        "end "
        "return {machines = machines, belts = belts, modules = modules, "
        "        mined = mined, buildable = buildable} end)()"
    )


def player_context(player: str) -> str:
    """What the player is pointing at, so the item does not have to be typed.

    Typing ``electronic-circuit`` is the worst part of every other calculator,
    and this one is running inside the game, where the player is already holding
    or hovering over the thing they mean. Three sources, in the order they are
    preferred:

    * the machine under the cursor and *the recipe set in it* -- point at an
      assembler and ask what feeding it costs;
    * the item held in the cursor;
    * the entity under the cursor, when it is not a machine.

    ``get_recipe`` replaced ``entity.recipe`` in 2.0 and does not exist on
    entities that do not craft, so both are behind pcall.
    """
    return (
        "(function() " + _SAFE +
        f"local p = game.get_player({lua.lua_string(player)}) "
        "if not p then return nil end "
        "local out = {} "
        "local cursor = safe(function() return p.cursor_stack end) "
        "if cursor and cursor.valid_for_read then out.cursor = cursor.name end "
        "local target = safe(function() return p.selected end) "
        "  or safe(function() "
        "       if p.opened_gui_type == defines.gui_type.entity then return p.opened end "
        "     end) "
        "if target and safe(function() return target.valid end) then "
        "  out.entity = target.name "
        "  local r = safe(function() return target.get_recipe() end) "
        "  if not r then r = safe(function() return target.recipe end) end "
        "  if r then "
        "    out.recipe = r.name "
        "    local first = r.prototype and r.prototype.main_product "
        "    out.recipe_product = first and first.name or r.name "
        "  end "
        "end "
        "return out end)()"
    )


def recipes_named(names: list[str]) -> str:
    """Look recipes up by their own name rather than by what they produce.

    ``!!recipe advanced-oil-processing`` is a reasonable thing to type, and
    there is no *item* by that name, so a product lookup finds nothing.
    """
    wanted = "{" + ",".join(lua.lua_string(name) for name in names) + "}"
    return (
        "(function() " + _SAFE +
        f"local out = {{}} for _, name in pairs({wanted}) do "
        "  local r = prototypes.recipe[name] "
        "  if r then "
        "    local ing, prod = {}, {} "
        "    for _, i in pairs(r.ingredients) do "
        "      ing[#ing + 1] = {name = i.name, amount = i.amount, type = i.type} end "
        "    for _, p in pairs(r.products) do "
        "      prod[#prod + 1] = {name = p.name, type = p.type, amount = p.amount, "
        "        amount_min = p.amount_min, amount_max = p.amount_max, "
        "        probability = p.probability} end "
        "    out[name] = {energy = r.energy, ingredients = ing, products = prod, "
        "      category = safe(function() return r.category end), "
        "      categories = safe(function() return r.categories end), "
        "      max_productivity = safe(function() return r.maximum_productivity end)} "
        "  end "
        "end return out end)()"
    )


def search_items(term: str, limit: int = 6) -> str:
    """Item and fluid names containing ``term`` -- for "did you mean" replies.

    Worth a round trip only when a lookup already failed, which is why it is a
    separate query rather than something the failure path could have avoided.
    """
    return (
        "(function() local out = {} "
        f"local term = {lua.lua_string(term)} "
        "for _, source in pairs({prototypes.item, prototypes.fluid}) do "
        "  for name in pairs(source) do "
        f"    if #out < {int(limit)} and string.find(name, term, 1, true) then "
        "      out[#out + 1] = name end "
        "  end "
        "end "
        "return out end)()"
    )


def recipes_for(
    items: list[str],
    exclude_categories: set[str] | None = None,
    exclude_patterns: list[str] | None = None,
    keep: set[str] | None = None,
    only_unlocked: bool = True,
) -> str:
    """Every recipe producing any of ``items``, with its full ingredient list.

    One pass over ``prototypes.recipe`` per call regardless of how many items
    are asked about, because the pass is the expensive part and the set lookup
    is not.

    The exclusions are not a nicety. 310 of the 659 recipes on a stock 2.0.77
    server are recycling, and every one of them "produces" the circuits inside
    whatever it shreds. Left in, the cheapest way to make an electronic circuit
    is to recycle scrap -- true, in the sense that the solver was asked to
    minimise raw input, and useless as something to build. ``keep`` is how a
    recipe the caller asked for by name survives the filter anyway.

    ``only_unlocked`` asks the *force*, not the prototype: ``force.recipes[name]
    .enabled`` is what this save has actually researched. It matters more than
    it sounds on a Space Age server, where the full recipe set includes casting
    from molten metal and Gleba's bioflux chain -- both genuinely cheaper by raw
    units, and both unreachable until you have been to that planet. Restricting
    to what is researched turns "what is theoretically optimal" into "what you
    can go and build", which is the question someone typing in chat is asking.
    """
    wanted = "{" + ",".join(f"[{lua.lua_string(name)}] = true" for name in items) + "}"
    kept = "{" + ",".join(f"[{lua.lua_string(name)}] = true" for name in sorted(keep or ())) + "}"
    bad_categories = "{" + ",".join(
        f"[{lua.lua_string(name)}] = true" for name in sorted(exclude_categories or ())
    ) + "}"
    patterns = "{" + ",".join(
        lua.lua_string(pattern) for pattern in (exclude_patterns or [])
    ) + "}"
    return (
        "(function() " + _SAFE +
        f"local want = {wanted} "
        f"local keep = {kept} "
        f"local bad = {bad_categories} "
        f"local patterns = {patterns} "
        f"local unlocked_only = {'true' if only_unlocked else 'false'} "
        "local force = game.forces.player "
        "local function excluded(name, r) "
        "  if keep[name] then return false end "
        "  if unlocked_only then "
        "    local fr = safe(function() return force.recipes[name] end) "
        "    if not fr or not fr.enabled then return true end "
        "  end "
        "  if bad[safe(function() return r.category end) or ''] then return true end "
        "  for _, p in pairs(patterns) do "
        "    if string.find(name, p, 1, true) then return true end "
        "  end "
        "  return false "
        "end "
        "local found = {} "
        "local producers = {} "
        "for name, r in pairs(prototypes.recipe) do "
        "  local hit = false "
        "  if not excluded(name, r) then "
        "  for _, p in pairs(r.products) do "
        "    if want[p.name] then "
        "      hit = true "
        "      producers[p.name] = producers[p.name] or {} "
        "      table.insert(producers[p.name], name) "
        "    end "
        "  end "
        "  end "
        "  if hit then "
        "    local ing, prod = {}, {} "
        "    for _, i in pairs(r.ingredients) do "
        "      ing[#ing + 1] = {name = i.name, amount = i.amount, type = i.type} end "
        "    for _, p in pairs(r.products) do "
        "      prod[#prod + 1] = {name = p.name, type = p.type, "
        "        amount = p.amount, amount_min = p.amount_min, amount_max = p.amount_max, "
        "        probability = p.probability} end "
        "    found[name] = {energy = r.energy, ingredients = ing, products = prod, "
        "      category = safe(function() return r.category end), "
        "      categories = safe(function() return r.categories end), "
        "      enabled = r.enabled, "
        "      max_productivity = safe(function() return r.maximum_productivity end)} "
        "  end "
        "end "
        "return {recipes = found, producers = producers} end)()"
    )


# ---------------------------------------------------------------------------

def _fraction(value, default=0) -> Number:
    """Exact where it can be, and never a float once it is in the model.

    ``limit_denominator`` keeps the numbers small: 3.2 seconds is 16/5 rather
    than the 3602879701896397/1125899906842624 that the binary float really is,
    and every ratio computed from it stays readable.
    """
    if value is None:
        return Fraction(default)
    try:
        return Fraction(value).limit_denominator(1_000_000)
    except (TypeError, ValueError):
        return Fraction(default)


def _as_list(value) -> list:
    """table_to_json renders an empty Lua table as ``{}``, so accept both."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _product_amount(product: dict) -> Number:
    """Expected output per craft, averaging over anything random.

    Uranium processing yields 0.993 U-238 and 0.007 U-235 per craft, and a
    calculator that answered "1 U-235" for it would be wrong by a factor of a
    hundred and forty. Expectation is the right model for a steady-state ratio,
    which is the only thing this is used for.
    """
    amount = product.get("amount")
    if amount is None:
        low = _fraction(product.get("amount_min"))
        high = _fraction(product.get("amount_max"))
        value = (low + high) / 2
    else:
        value = _fraction(amount)
    probability = product.get("probability")
    if probability is not None:
        value *= _fraction(probability, 1)
    return value


def parse_recipe(name: str, raw: dict) -> Recipe:
    ingredients: dict[str, Number] = {}
    for entry in _as_list(raw.get("ingredients")):
        item = entry.get("name")
        if item:
            ingredients[item] = ingredients.get(item, Fraction(0)) + _fraction(entry.get("amount"))

    products: dict[str, Number] = {}
    for entry in _as_list(raw.get("products")):
        item = entry.get("name")
        if item:
            products[item] = products.get(item, Fraction(0)) + _product_amount(entry)

    categories = _as_list(raw.get("categories")) or [raw.get("category") or "crafting"]
    ceiling = raw.get("max_productivity")
    return Recipe(
        name=name,
        energy=_fraction(raw.get("energy"), 1) or Fraction(1),
        ingredients=ingredients,
        products=products,
        category=str(categories[0]),
        max_productivity=_fraction(ceiling) if ceiling is not None else None,
    )


def parse_machine(name: str, raw: dict) -> Machine:
    return Machine(
        name=name,
        speed=_fraction(raw.get("speed"), 1) or Fraction(1),
        productivity=_fraction(raw.get("productivity")),
        energy_watts=_fraction(raw.get("energy")) * JOULES_PER_TICK_TO_WATTS,
        categories=tuple(str(c) for c in _as_list(raw.get("categories"))),
    )


def belt_throughput(belt_speed: float) -> Number:
    """Items per second a full belt carries, both lanes."""
    return _fraction(belt_speed) * TICKS_PER_SECOND * BELT_ITEMS_PER_TILE


def module_effect(effects: dict, key: str) -> Number:
    """One module effect, tolerating both the 2.0 and the 2.1.12 shapes."""
    value = (effects or {}).get(key)
    if isinstance(value, dict):
        value = value.get("bonus")
    return _fraction(value)


# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Modules:
    """What is in the machines, as the two numbers that change the answer."""

    speed: Number = Fraction(0)
    productivity: Number = Fraction(0)

    def describe(self) -> str:
        """Signed, because productivity modules *cost* speed and should say so."""
        parts = []
        for label, value in (("speed", self.speed), ("prod", self.productivity)):
            if value:
                parts.append(f"{label} {int(value * 100):+d}%")
        return ", ".join(parts)


#: Recipe categories no plan should be built out of by default.
#:
#: ``recycling`` is 310 of the 659 recipes on a stock 2.0.77 server and every
#: one of them lists what it shreds as a product, which makes "recycle scrap"
#: the cheapest way to make almost anything. ``parameters`` are the placeholder
#: recipes behind parameterised blueprints and make nothing at all.
EXCLUDED_CATEGORIES = ("recycling", "recycling-or-hand-crafting", "parameters")

#: Recipe names to skip. Barrelling is a closed loop -- fill and empty cancel
#: out -- so it only ever adds noise to a plan that was not about barrels.
EXCLUDED_PATTERNS = ("-barrel",)


class RecipeBook:
    """A cache of what the server said, and the closure walk over it."""

    def __init__(self, server, excluded_categories=None, excluded_patterns=None,
                 only_unlocked=True):
        self.only_unlocked = only_unlocked
        self.excluded_categories = set(
            EXCLUDED_CATEGORIES if excluded_categories is None else excluded_categories
        )
        self.excluded_patterns = list(
            EXCLUDED_PATTERNS if excluded_patterns is None else excluded_patterns
        )
        self.server = server
        self.recipes: dict[str, Recipe] = {}
        self.producers: dict[str, list[str]] = {}
        self.machines: dict[str, Machine] = {}
        self.belts: dict[str, Number] = {}
        self.modules: dict[str, dict] = {}
        self.mined: set[str] = set()
        """What a mining drill yields -- the things that genuinely come from the map."""

        self.buildable: set[str] = set()
        """Machines this save can place: researched, or already standing."""

        self._static_loaded = False

    def set_unlocked_only(self, flag: bool) -> None:
        """Switching what counts as available invalidates every cached answer."""
        if flag != self.only_unlocked:
            self.only_unlocked = flag
            self.recipes.clear()
            self.producers.clear()

    def clear(self) -> None:
        self.recipes.clear()
        self.producers.clear()
        self.machines.clear()
        self.belts.clear()
        self.modules.clear()
        self.mined.clear()
        self.buildable.clear()
        self._static_loaded = False

    async def load_static(self) -> None:
        if self._static_loaded:
            return
        data = await self.server.lua_json(static_data()) or {}
        for name, raw in (data.get("machines") or {}).items():
            self.machines[name] = parse_machine(name, raw)
        for name, speed in (data.get("belts") or {}).items():
            self.belts[name] = belt_throughput(speed)
        self.modules = data.get("modules") or {}
        self.mined = {str(name) for name in _as_list(data.get("mined"))}
        self.buildable = {str(name) for name in _as_list(data.get("buildable"))}
        self._static_loaded = True

    async def fetch(self, items: list[str], keep: set[str] | None = None) -> None:
        """Pull every recipe producing any of ``items`` into the cache."""
        missing = [item for item in items if item not in self.producers]
        if not missing:
            return
        data = await self.server.lua_json(recipes_for(
            missing, self.excluded_categories, self.excluded_patterns, keep,
            self.only_unlocked,
        )) or {}
        for name, raw in (data.get("recipes") or {}).items():
            if name not in self.recipes:
                self.recipes[name] = parse_recipe(name, raw)
        producers = data.get("producers") or {}
        for item in missing:
            self.producers[item] = [str(n) for n in _as_list(producers.get(item))]

    async def closure(
        self,
        targets: list[str],
        *,
        raw: set[str] | None = None,
        prefer: dict[str, str] | None = None,
        max_depth: int = 30,
    ) -> dict[str, Recipe]:
        """Every recipe needed to make ``targets``, walked breadth-first.

        ``raw`` stops the walk at items you would rather buy in than build --
        left at the default it stops only where the game does, at ores and
        fluids nothing produces.

        An item with several producers keeps *all* of them unless ``prefer``
        picks one, because choosing between them is exactly what the solver is
        for: which oil recipe to run is a consequence of what you asked for, not
        something to decide up front.

        Excluded categories are skipped except for a recipe named after a target
        or named in ``prefer``: asking for ``petroleum-gas-barrel`` should get
        you barrels, and asking for a circuit should not get you a recycler.
        """
        raw = raw or set()
        prefer = prefer or {}
        keep = set(targets) | set(prefer.values())
        chosen: dict[str, Recipe] = {}
        frontier = [item for item in targets if item not in raw]
        seen: set[str] = set(frontier)
        depth = 0

        while frontier:
            depth += 1
            if depth > max_depth:
                raise RecursionError(f"recipe graph deeper than {max_depth} levels")
            await self.fetch(frontier, keep=keep)
            next_items: list[str] = []
            for item in frontier:
                names = self.producers.get(item) or []
                if item in prefer and prefer[item] in self.recipes:
                    names = [prefer[item]]
                for name in names:
                    recipe = self.recipes.get(name)
                    if recipe is None or name in chosen:
                        continue
                    # A recipe that consumes its own product (Kovarex, coal
                    # liquefaction) is kept: the solver nets it out. What is
                    # skipped is a recipe producing nothing we want, which the
                    # server should not have returned in the first place.
                    chosen[name] = recipe
                    for ingredient in recipe.ingredients:
                        if ingredient not in seen and ingredient not in raw:
                            seen.add(ingredient)
                            next_items.append(ingredient)
            frontier = next_items

        return chosen

    # -- machine choice ------------------------------------------------------

    def machines_for(self, category: str) -> list[Machine]:
        return [m for m in self.machines.values() if category in m.categories]

    def best_machine(
        self, category: str, preferred: list[str], *, researched_only: bool = True
    ) -> Machine | None:
        """The machine to plan with: what was asked for, else the best available.

        Three steps, in order:

        1. a name from ``preferred`` -- the config list, or ``machine=`` on the
           question. A server that wants steel furnaces gets steel furnaces even
           though electric ones are faster;
        2. the fastest machine this save can actually **place**. Answering "use
           an assembling machine 3" to somebody who has not researched them is
           the most useless thing this can say, and it was what it used to do,
           because the machine list comes from prototypes and prototypes know
           nothing about research;
        3. when nothing in the category is unlocked, the **simplest** one that
           exists -- better a plan in a machine you have yet to unlock than no
           plan at all, and the one you will unlock first is the one worth
           naming. Speed is the proxy for tier: stone before steel before
           electric, assembling machine 1 before the foundry. Answering
           "cryogenic plant" to somebody running assembling machine 1 is
           technically the fastest and of no use to anyone.

        Asking for the fastest regardless of research (``researched_only=False``)
        is a different question and still gets the fastest.
        """
        candidates = self.machines_for(category)
        if not candidates:
            return None
        by_name = {m.name: m for m in candidates}
        for name in preferred:
            if name in by_name:
                return by_name[name]

        fastest = max(candidates, key=lambda m: (m.speed, m.name))
        if not researched_only:
            return fastest
        if self.buildable:
            available = [m for m in candidates if m.name in self.buildable]
            if available:
                return max(available, key=lambda m: (m.speed, m.name))
        return min(candidates, key=lambda m: (m.speed, m.name))

    def assign_machines(
        self,
        recipes: dict[str, Recipe],
        preferred: list[str],
        modules: Modules,
        overrides: dict[str, str] | None = None,
        researched_only: bool = True,
    ) -> tuple[dict[str, Machine], list[str]]:
        """Pick the machine for each recipe and fold modules into it.

        Productivity is folded into the recipe's *products* by the caller, not
        here, because it changes the matrix rather than the machine count.
        Speed is folded in here, where it belongs: it only changes how many
        machines a given craft rate needs.

        Returns the assignment and the names of recipes nothing can run.
        """
        overrides = overrides or {}
        assigned: dict[str, Machine] = {}
        unbuildable: list[str] = []

        for name, recipe in recipes.items():
            machine = None
            if name in overrides:
                machine = self.machines.get(overrides[name])
            if machine is None:
                machine = self.best_machine(
                    recipe.category, preferred, researched_only=researched_only
                )
            if machine is None:
                unbuildable.append(name)
                continue
            assigned[name] = dataclasses.replace(
                machine,
                speed=machine.speed * (1 + modules.speed),
                productivity=machine.productivity + modules.productivity,
            )
        return assigned, unbuildable


def apply_productivity(
    recipes: dict[str, Recipe], machines: dict[str, Machine]
) -> dict[str, Recipe]:
    """Scale each recipe's output by the productivity of the machine running it.

    Doing this before the matrix is built is what makes productivity correct
    rather than approximately correct: a 50% bonus on the foundry means the
    plate recipe genuinely produces 1.5x, and every ratio downstream of it
    follows from that one coefficient rather than from a fudge applied at the
    end.
    """
    scaled: dict[str, Recipe] = {}
    for name, recipe in recipes.items():
        machine = machines.get(name)
        bonus = machine.productivity if machine else Fraction(0)
        if recipe.max_productivity is not None and recipe.max_productivity > 0:
            bonus = min(bonus, recipe.max_productivity)
        if not bonus:
            scaled[name] = recipe
            continue
        factor = 1 + bonus
        scaled[name] = dataclasses.replace(
            recipe,
            products={item: amount * factor for item, amount in recipe.products.items()},
        )
    return scaled
