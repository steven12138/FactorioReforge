"""Exact-rational linear programming, and the production model built on it.

Every mature Factorio calculator -- Kirk McDonald's, FactorioLab, YAFC -- ends up
at the same place, so this does too rather than inventing a fourth answer:

* recipes become a **matrix**, rows items and columns recipes, positive for a
  product and negative for an ingredient;
* the rates you want are the solution to ``A x >= b``;
* a plain tree walk is only enough because most items have exactly one recipe.
  Oil processing has three recipes producing overlapping fluids, cracking turns
  one into another, and Kovarex feeds its own output back in. Those make the
  system underdetermined or cyclic, and a walk either loops or has to guess.
* so it is solved as a **linear program**: minimise the cost of raw resources,
  subject to producing at least what was asked for.

Two details are worth stating because they are what makes the answers trustworthy.

**Exact arithmetic.** Ratios in this game are fractions -- 3/2 gears per belt,
7/12 of a machine -- and floating point turns those into 0.5833333333333334 and
then into a machine count that is 2.9999999999999996. Everything here is
:class:`fractions.Fraction`, so a ratio that is exact stays exact and rounding
happens once, at the point where it is printed.

**Bland's rule.** Degenerate vertices are the normal case here, not a corner
case: the moment two recipes produce the same item in the same proportion, the
simplex has ties to break. Bland's rule is the slow pivot choice and the only
one that cannot cycle, and the problems are tiny (tens of variables), so the
speed it costs is not measurable and the termination it guarantees is worth
having.
"""

from __future__ import annotations

import dataclasses
from fractions import Fraction

Number = Fraction


class Infeasible(Exception):
    """No combination of the known recipes can produce what was asked for."""


class Unbounded(Exception):
    """The cost can be driven to minus infinity -- a recipe set that mints value.

    Only reachable with a mod whose recipes form a free-energy loop, but it is
    raised rather than looped on forever.
    """


# ---------------------------------------------------------------------------
# Simplex
# ---------------------------------------------------------------------------

def solve_min_cost(
    rows: list[list[Number]], rhs: list[Number], cost: list[Number]
) -> list[Number]:
    """Minimise ``cost . x`` subject to ``rows . x >= rhs`` and ``x >= 0``.

    ``rhs`` must be non-negative, which it is by construction here: it is the
    demand vector, and asking for a negative amount of something is not a
    question this has to answer.

    Returns the solution vector. Raises :class:`Infeasible` or :class:`Unbounded`.
    """
    m = len(rows)
    n = len(cost)
    if m == 0:
        return [Fraction(0)] * n
    if any(value < 0 for value in rhs):
        raise ValueError("rhs must be non-negative")

    # Columns: n structural, m surplus (x - s = b), m artificial (phase 1 basis).
    width = n + 2 * m
    table: list[list[Number]] = []
    for i, row in enumerate(rows):
        cells = [Fraction(0)] * (width + 1)
        for j, value in enumerate(row):
            cells[j] = Fraction(value)
        cells[n + i] = Fraction(-1)          # surplus
        cells[n + m + i] = Fraction(1)       # artificial
        cells[width] = Fraction(rhs[i])
        table.append(cells)

    basis = [n + m + i for i in range(m)]

    # -- phase 1: drive the artificials to zero ------------------------------
    phase1 = [Fraction(0)] * (width + 1)
    for j in range(n + m, width):
        phase1[j] = Fraction(1)
    _price_out(table, basis, phase1)
    _simplex(table, basis, phase1, width)

    if -phase1[width] > 0:
        # The objective row carries the negated value, so a positive optimum
        # means some artificial is still carrying demand nothing can meet.
        raise Infeasible("no recipe chain produces the requested items")

    _expel_artificials(table, basis, n, m, width)

    # -- phase 2: the real objective -----------------------------------------
    phase2 = [Fraction(0)] * (width + 1)
    for j, value in enumerate(cost):
        phase2[j] = Fraction(value)
    for j in range(n + m, width):
        # Artificials are pinned at zero from here on; a huge cost is not needed
        # because _expel_artificials removed every one that could still move.
        phase2[j] = Fraction(0)
    _price_out(table, basis, phase2)
    _simplex(table, basis, phase2, width, forbidden=range(n + m, width))

    solution = [Fraction(0)] * n
    for i, column in enumerate(basis):
        if column < n:
            solution[column] = table[i][width]
    return solution


def _price_out(table, basis, objective) -> None:
    """Make the objective row consistent with the current basis."""
    for i, column in enumerate(basis):
        factor = objective[column]
        if factor:
            row = table[i]
            for j in range(len(objective)):
                objective[j] -= factor * row[j]


def _simplex(table, basis, objective, width, forbidden=()) -> None:
    blocked = set(forbidden)
    while True:
        # Bland: the *lowest-indexed* improving column, which is what makes
        # cycling impossible on the degenerate vertices this problem is full of.
        entering = -1
        for j in range(width):
            if j not in blocked and objective[j] < 0:
                entering = j
                break
        if entering < 0:
            return

        leaving = -1
        best: Number | None = None
        for i, row in enumerate(table):
            if row[entering] <= 0:
                continue
            ratio = row[width] / row[entering]
            # Bland again on ties: smallest basis variable index leaves.
            if best is None or ratio < best or (ratio == best and basis[i] < basis[leaving]):
                best, leaving = ratio, i
        if leaving < 0:
            raise Unbounded("the recipe set allows unlimited free production")

        _pivot(table, basis, objective, leaving, entering, width)


def _pivot(table, basis, objective, row_index, column, width) -> None:
    row = table[row_index]
    pivot = row[column]
    table[row_index] = row = [value / pivot for value in row]
    for i, other in enumerate(table):
        if i == row_index or not other[column]:
            continue
        factor = other[column]
        table[i] = [a - factor * b for a, b in zip(other, row, strict=True)]
    factor = objective[column]
    if factor:
        for j in range(width + 1):
            objective[j] -= factor * row[j]
    basis[row_index] = column


def _expel_artificials(table, basis, n, m, width) -> None:
    """Pivot artificial variables out of the basis, dropping redundant rows.

    An artificial left in the basis at value zero is harmless to the objective
    but would let phase 2 pivot it back up. Either some real column can take its
    place, or the row said nothing the other rows did not already say.
    """
    for i in reversed(range(len(table))):
        if basis[i] < n + m:
            continue
        row = table[i]
        replacement = next(
            (j for j in range(n + m) if row[j] != 0), None
        )
        if replacement is None:
            del table[i]
            del basis[i]
        else:
            _pivot(table, basis, [Fraction(0)] * (width + 1), i, replacement, width)


# ---------------------------------------------------------------------------
# The production model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Recipe:
    """One recipe, already resolved against the machine that will run it."""

    name: str
    energy: Number
    """Seconds one craft takes in a machine of speed 1."""

    ingredients: dict[str, Number]
    products: dict[str, Number]
    category: str = "crafting"

    max_productivity: Number | None = None
    """Ceiling on the productivity bonus, when the recipe declares one."""

    def net(self, item: str) -> Number:
        return self.products.get(item, Fraction(0)) - self.ingredients.get(item, Fraction(0))

    def items(self) -> set[str]:
        return set(self.ingredients) | set(self.products)


@dataclasses.dataclass
class Machine:
    name: str
    speed: Number
    """Crafting speed. The recipe takes ``energy / speed`` seconds here."""

    productivity: Number = Fraction(0)
    """Bonus output, as a fraction: 0.5 means every craft yields 1.5x."""

    energy_watts: Number = Fraction(0)
    """What it draws while running. For a burner this is fuel, not electricity."""

    categories: tuple[str, ...] = ()

    #: "electric" or "burner". A stone furnace draws 90 kW and none of it comes
    #: from a wire, so reporting one number called "power" for both is how an
    #: answer gets read as a demand on the grid that does not exist.
    energy_type: str = "electric"

    #: Fraction of the fuel's energy the machine actually gets. 1 on every
    #: burner measured on 2.0.77, and read rather than assumed because it is a
    #: real prototype field that mods and other entities do vary.
    effectivity: Number = Fraction(1)

    #: Which fuels it accepts: "chemical" for furnaces, but a biochamber burns
    #: "nutrients" and a captive biter spawner burns "food". Offering coal to
    #: either is not an approximation, it is wrong.
    fuel_categories: tuple[str, ...] = ()

    #: The fuel picked for this machine, filled in when machines are assigned.
    fuel: str = ""
    fuel_joules: Number = Fraction(0)

    @property
    def is_burner(self) -> bool:
        return self.energy_type == "burner"


@dataclasses.dataclass
class Step:
    """One recipe in the plan, with how much of it to build."""

    recipe: str
    machine: str
    crafts_per_second: Number
    machines: Number
    outputs: dict[str, Number]
    """Items per second this step produces, productivity included."""

    inputs: dict[str, Number]

    power_watts: Number = Fraction(0)
    """Electrical draw. Zero for a burner -- see :attr:`fuel_watts`."""

    fuel_watts: Number = Fraction(0)
    """Chemical energy burned. Zero for anything on a wire."""

    fuel: str = ""
    fuel_per_second: Number = Fraction(0)


@dataclasses.dataclass
class Plan:
    steps: list[Step]
    raw: dict[str, Number]
    """Items per second that have to come from somewhere else."""

    surplus: dict[str, Number]
    """Items per second produced beyond what the plan consumes -- byproducts."""

    targets: dict[str, Number]

    @property
    def power_watts(self) -> Number:
        """Electrical demand only. Fuel is :attr:`fuel_watts`."""
        return sum((step.power_watts for step in self.steps), Fraction(0))

    @property
    def fuel_watts(self) -> Number:
        return sum((step.fuel_watts for step in self.steps), Fraction(0))

    @property
    def fuel_per_second(self) -> dict[str, Number]:
        """Fuel items per second, by item.

        Reported rather than folded into :attr:`raw`, because the solver works
        over the recipe matrix and fuel is not an ingredient of anything. Coal
        is mined, so listing it beside the ore is the whole answer; a fuel that
        has to be manufactured is named here but its own chain is not built.
        """
        totals: dict[str, Number] = {}
        for step in self.steps:
            if step.fuel and step.fuel_per_second:
                totals[step.fuel] = totals.get(step.fuel, Fraction(0)) + step.fuel_per_second
        return totals

    @property
    def machine_counts(self) -> dict[str, Number]:
        totals: dict[str, Number] = {}
        for step in self.steps:
            totals[step.machine] = totals.get(step.machine, Fraction(0)) + step.machines
        return totals


#: A pseudo-cost charged on every recipe, so the solver does not run one for
#: free when it changes nothing. Without it an underdetermined system can answer
#: with a cracking loop spinning at some arbitrary rate that cancels itself out.
RECIPE_TAX = Fraction(1, 1000)

#: What one unit of a raw resource costs. Uniform by default, so the solver
#: minimises total raw material; override per item to express "I have plenty of
#: coal but oil is far away".
#:
#: This is the one place the answer depends on a judgement rather than on the
#: game's own numbers, and it is not avoidable: "crack the heavy oil" and "pump
#: more crude" both produce petroleum, and which is better depends on what your
#: map makes cheap. Charging water the same as crude oil makes the solver
#: refuse to crack, which is the wrong answer on almost every map -- hence the
#: default below, and the config key that overrides it.
DEFAULT_RAW_COST = Fraction(1)

#: What it costs to conjure an item that recipes *could* have made. Every item
#: gets a source at this price, which is what makes the answer "you need to
#: supply 5 iron ore" instead of the exception "this is impossible".
#:
#: A structural test -- raw means nothing produces it -- is not enough on a
#: server with Space Age data. Iron ore is a product there, of asteroid
#: crushing, and the chunks are produced only by reprocessing each other, so a
#: cycle nothing seeds ends up being the only "source" of iron and the whole
#: plan comes out infeasible. Pricing every item and letting the mined ones be
#: cheap turns that from a failure into an ordering.
FALLBACK_SOURCE_COST = Fraction(1000)


def build_plan(
    recipes: dict[str, Recipe],
    machines: dict[str, Machine],
    targets: dict[str, Number],
    *,
    raw_costs: dict[str, Number] | None = None,
    mined: set[str] | None = None,
) -> Plan:
    """Solve for the recipe rates that meet ``targets``, then size the machines.

    ``recipes`` is the closure already fetched for this question: every recipe
    that could contribute. ``machines[recipe_name]`` says which machine runs it,
    resolved by the caller because that choice decides the productivity bonus
    and so changes the matrix itself.

    Every item gets a pseudo-recipe that mints it at a cost, which is what turns
    "what does this need" into a minimisation the simplex can answer. What
    separates a raw material from an intermediate is only the *price*: things
    that come out of the ground, and things nothing produces, are cheap, so the
    solver reaches for them; everything else is priced high enough that crafting
    it always wins where crafting is possible at all.
    """
    raw_costs = raw_costs or {}
    mined = mined or set()

    items: set[str] = set(targets)
    for recipe in recipes.values():
        items |= recipe.items()

    # Nothing here *nets* this item, so it has to be supplied. Testing the net
    # rather than the product list is what makes cycles work: Kovarex lists
    # U-238 as a product but consumes more than it makes, so U-238 still has to
    # come from somewhere, and calling it "produced" would make the demand look
    # impossible.
    produced = {
        item for recipe in recipes.values() for item in recipe.items()
        if recipe.net(item) > 0
    }
    cheap = (items - produced) | (items & mined)
    source_items = sorted(items)

    order = sorted(recipes)
    item_order = sorted(items)
    index = {item: i for i, item in enumerate(item_order)}

    rows = [[Fraction(0)] * (len(order) + len(source_items)) for _ in item_order]
    for column, name in enumerate(order):
        recipe = recipes[name]
        for item in recipe.items():
            rows[index[item]][column] = recipe.net(item)
    for offset, item in enumerate(source_items):
        rows[index[item]][len(order) + offset] = Fraction(1)

    rhs = [Fraction(targets.get(item, 0)) for item in item_order]
    cost = [RECIPE_TAX] * len(order) + [
        Fraction(raw_costs[item]) if item in raw_costs
        else DEFAULT_RAW_COST if item in cheap
        else FALLBACK_SOURCE_COST
        for item in source_items
    ]

    solution = solve_min_cost(rows, rhs, cost)

    steps: list[Step] = []
    for column, name in enumerate(order):
        rate = solution[column]
        if rate <= 0:
            continue
        recipe = recipes[name]
        machine = machines[name]
        count = rate * recipe.energy / machine.speed
        draw = count * machine.energy_watts
        burning = machine.is_burner
        # Watts of fuel to items of fuel: a stone furnace burning 90 kW eats
        # 90,000 / 4,000,000 = 0.0225 coal a second. Effectivity divides,
        # because a machine that wastes half the energy needs twice the coal.
        per_second = Fraction(0)
        if burning and machine.fuel_joules > 0 and machine.effectivity > 0:
            per_second = draw / (machine.fuel_joules * machine.effectivity)
        steps.append(Step(
            recipe=name,
            machine=machine.name,
            crafts_per_second=rate,
            machines=count,
            outputs={item: amount * rate for item, amount in recipe.products.items()},
            inputs={item: amount * rate for item, amount in recipe.ingredients.items()},
            power_watts=Fraction(0) if burning else draw,
            fuel_watts=draw if burning else Fraction(0),
            fuel=machine.fuel if burning else "",
            fuel_per_second=per_second,
        ))
    steps.sort(key=lambda step: (-step.machines, step.recipe))

    raw = {
        item: solution[len(order) + offset]
        for offset, item in enumerate(source_items)
        if solution[len(order) + offset] > 0
    }

    balance: dict[str, Number] = {}
    for step in steps:
        for item, amount in step.outputs.items():
            balance[item] = balance.get(item, Fraction(0)) + amount
        for item, amount in step.inputs.items():
            balance[item] = balance.get(item, Fraction(0)) - amount
    surplus = {
        item: amount - Fraction(targets.get(item, 0))
        for item, amount in balance.items()
        if amount - Fraction(targets.get(item, 0)) > 0
    }

    return Plan(steps=steps, raw=raw, surplus=surplus, targets=dict(targets))
