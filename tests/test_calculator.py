"""The calculator plugin: the evaluator, the simplex, and the recipe model.

None of this needs a server. The solver is fed hand-built recipes with numbers
taken from the real game, so an assertion here is a claim about a ratio that can
be checked against the wiki rather than about an implementation detail.
"""

import importlib.util
import sys
from fractions import Fraction
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


def load(module: str):
    """Import a plugin submodule the way the plugin manager imports packages."""
    package = "_test_plugin_calculator"
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package,
            PLUGINS / "calculator" / "__init__.py",
            submodule_search_locations=[str(PLUGINS / "calculator")],
        )
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[package] = loaded
        spec.loader.exec_module(loaded)
    return importlib.import_module(f"{package}.{module}") if module else sys.modules[package]


@pytest.fixture(scope="module")
def expr():
    return load("expr")


@pytest.fixture(scope="module")
def solver():
    return load("solver")


@pytest.fixture(scope="module")
def recipes():
    return load("recipes")


@pytest.fixture(scope="module")
def plugin():
    return load("")


# ---------------------------------------------------------------------------
# The chat calculator
# ---------------------------------------------------------------------------

class TestExpressions:
    @pytest.mark.parametrize("text,expected", [
        ("1+1", 2),
        ("2 * (3 + 4)", 14),
        ("1400/7.5", 186.66666666666666),
        ("2**10", 1024),
        ("7 // 2", 3),
        ("7 % 2", 1),
        ("-3 + 1", -2),
        ("sqrt(144)", 12),
        ("max(1, 2, 3)", 3),
        ("round(45/7, 2)", 6.43),
        ("ceil(3.2)", 4),
    ])
    def test_arithmetic(self, expr, text, expected):
        assert expr.evaluate(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", [
        "__import__('os').system('ls')",
        "open('/etc/passwd')",
        "().__class__",
        "[1,2,3]",
        "lambda: 1",
        "x = 1",
        "print(1)",
        "'a' * 5",
        "1 if 1 else 2",
    ])
    def test_anything_that_is_not_arithmetic_is_refused(self, expr, text):
        """The whitelist is on node types, so nothing has to be blacklisted."""
        with pytest.raises(expr.CalcError):
            expr.evaluate(text)

    def test_division_by_zero_is_a_message_not_a_traceback(self, expr):
        with pytest.raises(expr.CalcError, match="division by zero"):
            expr.evaluate("1/0")

    def test_a_huge_exponent_is_refused_before_it_is_computed(self, expr):
        """``9**9**9`` is valid arithmetic and would hang the server."""
        with pytest.raises(expr.CalcError):
            expr.evaluate("9**9**9")

    def test_a_long_expression_is_refused(self, expr):
        with pytest.raises(expr.CalcError):
            expr.evaluate("1+" * 200 + "1")

    @pytest.mark.parametrize("value,expected", [
        (2, "2"),
        (1234567, "1,234,567"),
        (6.43, "6.43"),
        (12.0, "12"),
        (0.0000001, "1e-07"),
    ])
    def test_formatting(self, expr, value, expected):
        assert expr.format_number(value) == expected


# ---------------------------------------------------------------------------
# The simplex
# ---------------------------------------------------------------------------

class TestSimplex:
    def test_a_trivial_minimum(self, solver):
        # min x subject to x >= 5
        assert solver.solve_min_cost([[Fraction(1)]], [Fraction(5)], [Fraction(1)]) == [5]

    def test_it_picks_the_cheaper_of_two_ways(self, solver):
        # Two variables both satisfy the constraint; the cheap one should win.
        result = solver.solve_min_cost(
            [[Fraction(1), Fraction(1)]], [Fraction(10)], [Fraction(5), Fraction(1)]
        )
        assert result == [0, 10]

    def test_it_stays_exact(self, solver):
        """A third of a machine is 1/3, not 0.3333333333333333."""
        result = solver.solve_min_cost([[Fraction(3)]], [Fraction(1)], [Fraction(1)])
        assert result == [Fraction(1, 3)]

    def test_an_impossible_demand_is_reported(self, solver):
        with pytest.raises(solver.Infeasible):
            solver.solve_min_cost([[Fraction(0)]], [Fraction(1)], [Fraction(1)])


# ---------------------------------------------------------------------------
# The production model
# ---------------------------------------------------------------------------

def recipe(solver, name, seconds, ingredients, products, category="crafting"):
    return solver.Recipe(
        name=name,
        energy=Fraction(seconds).limit_denominator(1000),
        ingredients={k: Fraction(v) for k, v in ingredients.items()},
        products={k: Fraction(v) for k, v in products.items()},
        category=category,
    )


@pytest.fixture
def circuits(solver):
    """The real electronic circuit chain, straight off the wiki."""
    return {
        "electronic-circuit": recipe(
            solver, "electronic-circuit", "0.5",
            {"iron-plate": 1, "copper-cable": 3}, {"electronic-circuit": 1},
        ),
        "copper-cable": recipe(
            solver, "copper-cable", "0.5", {"copper-plate": 1}, {"copper-cable": 2}
        ),
    }


@pytest.fixture
def assembler(solver):
    return solver.Machine(name="assembling-machine-2", speed=Fraction(3, 4), energy_watts=Fraction(150_000))


class TestPlan:
    def machines(self, recipes_, machine):
        return dict.fromkeys(recipes_, machine)

    def test_the_known_ratio(self, solver, circuits, assembler):
        """5 circuits/s needs 15 cable/s: the ratio everyone knows by heart."""
        plan = solver.build_plan(
            circuits, self.machines(circuits, assembler), {"electronic-circuit": Fraction(5)}
        )
        rates = {step.recipe: step.crafts_per_second for step in plan.steps}
        assert rates["electronic-circuit"] == 5
        assert rates["copper-cable"] == Fraction(15, 2)   # 15 cable/s at 2 per craft

    def test_machine_counts_follow_from_crafting_speed(self, solver, circuits, assembler):
        plan = solver.build_plan(
            circuits, self.machines(circuits, assembler), {"electronic-circuit": Fraction(5)}
        )
        counts = {step.recipe: step.machines for step in plan.steps}
        # 5 crafts/s x 0.5s each / 0.75 speed
        assert counts["electronic-circuit"] == Fraction(10, 3)
        assert counts["copper-cable"] == Fraction(5)

    def test_raw_inputs_are_what_no_recipe_makes(self, solver, circuits, assembler):
        plan = solver.build_plan(
            circuits, self.machines(circuits, assembler), {"electronic-circuit": Fraction(5)}
        )
        assert plan.raw == {"iron-plate": Fraction(5), "copper-plate": Fraction(15, 2)}

    def test_power_adds_up(self, solver, circuits, assembler):
        plan = solver.build_plan(
            circuits, self.machines(circuits, assembler), {"electronic-circuit": Fraction(5)}
        )
        assert plan.power_watts == (Fraction(10, 3) + 5) * 150_000

    def test_productivity_shows_up_as_fewer_inputs(self, solver, circuits, assembler):
        """The bonus is folded into the recipe, so every rate upstream drops."""
        import dataclasses
        boosted = dataclasses.replace(assembler, productivity=Fraction(1, 2))
        scaled = {
            name: dataclasses.replace(
                r, products={k: v * Fraction(3, 2) for k, v in r.products.items()}
            )
            for name, r in circuits.items()
        }
        plan = solver.build_plan(
            scaled, self.machines(scaled, boosted), {"electronic-circuit": Fraction(3)}
        )
        # 3/s out of a 1.5x recipe is 2 crafts/s, each eating 1 iron plate.
        assert plan.raw["iron-plate"] == 2


@pytest.fixture
def oil(solver):
    """Advanced oil processing plus both cracking recipes -- the hard case.

    This is why a tree walk is not enough: three recipes produce overlapping
    fluids, cracking turns one into another, and how much of each to run is not
    a property of any single recipe.
    """
    return {
        "advanced-oil-processing": recipe(
            solver, "advanced-oil-processing", 5,
            {"crude-oil": 100, "water": 50},
            {"heavy-oil": 25, "light-oil": 45, "petroleum-gas": 55},
            category="oil-processing",
        ),
        "heavy-oil-cracking": recipe(
            solver, "heavy-oil-cracking", 2,
            {"heavy-oil": 40, "water": 30}, {"light-oil": 30}, category="chemistry",
        ),
        "light-oil-cracking": recipe(
            solver, "light-oil-cracking", 2,
            {"light-oil": 30, "water": 30}, {"petroleum-gas": 20}, category="chemistry",
        ),
    }


#: Water pumps itself; crude has to be found and piped. Charging them the same
#: makes the solver refuse to crack, which is the wrong answer on most maps.
WATER_IS_CHEAP = {"water": Fraction(1, 100)}


class TestOil:
    def machines(self, recipes_, solver):
        return {
            name: solver.Machine(name="plant", speed=Fraction(1)) for name in recipes_
        }

    def test_it_cracks_rather_than_pumping_more_crude(self, solver, oil):
        """Full cracking yields 97.5 petroleum per 100 crude -- so 4000/39 crude."""
        plan = solver.build_plan(
            oil, self.machines(oil, solver), {"petroleum-gas": Fraction(100)},
            raw_costs=WATER_IS_CHEAP,
        )
        assert plan.raw["crude-oil"] == Fraction(4000, 39)
        running = {step.recipe for step in plan.steps}
        assert running == set(oil)

    def test_nothing_is_left_over(self, solver, oil):
        """Cracking everything means no heavy or light oil piles up."""
        plan = solver.build_plan(
            oil, self.machines(oil, solver), {"petroleum-gas": Fraction(100)},
            raw_costs=WATER_IS_CHEAP,
        )
        assert plan.surplus == {}

    def test_asking_for_light_oil_uses_a_different_mix(self, solver, oil):
        plan = solver.build_plan(
            oil, self.machines(oil, solver), {"light-oil": Fraction(10)}
        )
        rates = {step.recipe: step.crafts_per_second for step in plan.steps}
        assert "light-oil-cracking" not in rates
        assert plan.surplus.get("petroleum-gas", 0) > 0


class TestCycles:
    def test_a_recipe_that_eats_its_own_product_terminates(self, solver):
        """Kovarex-shaped: it consumes the thing it produces, netting a gain.

        A tree walk recurses forever here. The matrix nets the coefficients out
        and the answer falls out of the same solve as everything else.
        """
        enrich = recipe(
            solver, "enrichment", 60,
            {"depleted": 40, "enriched": 5}, {"depleted": 39, "enriched": 6},
        )
        plan = solver.build_plan(
            {"enrichment": enrich},
            {"enrichment": solver.Machine(name="centrifuge", speed=Fraction(1))},
            {"enriched": Fraction(1)},
        )
        # Net +1 enriched and -1 depleted per craft, so one craft per second.
        assert plan.steps[0].crafts_per_second == 1
        assert plan.raw == {"depleted": Fraction(1)}


# ---------------------------------------------------------------------------
# Reading the game's data
# ---------------------------------------------------------------------------

class TestGameData:
    def test_belt_throughput_matches_the_wiki(self, recipes):
        """0.03125 tiles/tick is the yellow belt, and that is 15 items/s."""
        assert recipes.belt_throughput(0.03125) == 15
        assert recipes.belt_throughput(0.0625) == 30
        assert recipes.belt_throughput(0.09375) == 45

    def test_probabilistic_products_are_averaged(self, recipes):
        """Uranium processing gives 0.7% U-235, not one per craft."""
        raw = {
            "energy": 12,
            "ingredients": [{"name": "uranium-ore", "amount": 10}],
            "products": [
                {"name": "uranium-235", "amount": 1, "probability": 0.007},
                {"name": "uranium-238", "amount": 1, "probability": 0.993},
            ],
        }
        parsed = recipes.parse_recipe("uranium-processing", raw)
        assert parsed.products["uranium-235"] == Fraction(7, 1000)
        assert parsed.products["uranium-238"] == Fraction(993, 1000)

    def test_a_range_product_uses_its_mean(self, recipes):
        raw = {
            "energy": 1, "ingredients": [],
            "products": [{"name": "x", "amount_min": 1, "amount_max": 3}],
        }
        assert recipes.parse_recipe("r", raw).products["x"] == 2

    def test_both_module_effect_shapes_are_read(self, recipes):
        """The shape changed in 2.1.12; a server on either must still work."""
        assert recipes.module_effect({"speed": 0.2}, "speed") == Fraction(1, 5)
        assert recipes.module_effect({"speed": {"bonus": 0.2}}, "speed") == Fraction(1, 5)
        assert recipes.module_effect({}, "speed") == 0

    def test_the_category_field_is_read_under_either_name(self, recipes):
        singular = recipes.parse_recipe("a", {"energy": 1, "category": "smelting"})
        plural = recipes.parse_recipe("b", {"energy": 1, "categories": ["chemistry"]})
        assert (singular.category, plural.category) == ("smelting", "chemistry")

    def test_energy_usage_becomes_watts(self, recipes):
        """max_energy_usage is per tick; an assembler 3 reports 6250 for 375 kW."""
        machine = recipes.parse_machine("assembling-machine-3", {"speed": 1.25, "energy": 6250})
        assert machine.energy_watts == 375_000


class TestQueryParsing:
    @pytest.fixture(autouse=True)
    def _config(self, plugin):
        plugin._state.clear()
        plugin._state["config"] = plugin.DEFAULT_CONFIG
        yield
        plugin._state.clear()

    @pytest.mark.parametrize("text,expected", [
        ("5", Fraction(5)),
        ("5/s", Fraction(5)),
        ("300/m", Fraction(5)),
        ("1/min", Fraction(1, 60)),
        ("3600/h", Fraction(1)),
        ("0.5/s", Fraction(1, 2)),
    ])
    def test_rates(self, plugin, text, expected):
        assert plugin.parse_rate(text) == expected

    @pytest.mark.parametrize("text", ["", "abc", "5/decade", "5//s"])
    def test_bad_rates_are_rejected(self, plugin, text):
        assert plugin.parse_rate(text) is None

    def test_an_item_icon_pasted_from_the_game_is_an_argument(self, plugin):
        """Players insert icons with the in-game picker; that should just work."""
        assert plugin.normalise("[item=iron-plate]") == "iron-plate"
        assert plugin.normalise("[fluid=crude-oil,quality=normal]") == "crude-oil"

    def test_names_are_forgiving(self, plugin):
        assert plugin.normalise("Iron Gear Wheel") == "iron-gear-wheel"
        assert plugin.normalise("green circuit") == "electronic-circuit"

    def test_options_are_split_out_wherever_they_appear(self, plugin):
        words, rate, options = plugin.parse_query("prod=20 electronic circuit 30/m machine=foundry")
        assert words == ["electronic", "circuit"]
        assert rate == Fraction(1, 2)
        assert options == {"prod": "20", "machine": "foundry"}

    def test_a_query_with_no_rate_leaves_the_default_to_the_caller(self, plugin):
        words, rate, options = plugin.parse_query("iron-plate")
        assert (words, rate, options) == (["iron-plate"], None, {})


# ---------------------------------------------------------------------------
# Everything together, with the server faked at the RCON boundary
# ---------------------------------------------------------------------------

#: What a 2.0 server replies with, in the shape helpers.table_to_json produces.
STATIC_REPLY = {
    "machines": {
        "assembling-machine-2": {
            "speed": 0.75, "categories": ["crafting"], "energy": 2500, "productivity": 0,
        },
        "assembling-machine-3": {
            "speed": 1.25, "categories": ["crafting"], "energy": 6250, "productivity": 0,
        },
        "electric-furnace": {
            "speed": 2, "categories": ["smelting"], "energy": 3000, "productivity": 0,
        },
    },
    "belts": {"transport-belt": 0.03125, "express-transport-belt": 0.09375},
    "modules": {"productivity-module": {"productivity": 0.04, "speed": -0.05}},
}

RECIPE_REPLIES = {
    "electronic-circuit": {
        "energy": 0.5, "category": "crafting",
        "ingredients": [
            {"name": "iron-plate", "amount": 1}, {"name": "copper-cable", "amount": 3},
        ],
        "products": [{"name": "electronic-circuit", "amount": 1}],
    },
    "copper-cable": {
        "energy": 0.5, "category": "crafting",
        "ingredients": [{"name": "copper-plate", "amount": 1}],
        "products": [{"name": "copper-cable", "amount": 2}],
    },
    "copper-plate": {
        "energy": 3.2, "category": "smelting",
        "ingredients": [{"name": "copper-ore", "amount": 1}],
        "products": [{"name": "copper-plate", "amount": 1}],
    },
    "iron-plate": {
        "energy": 3.2, "category": "smelting",
        "ingredients": [{"name": "iron-ore", "amount": 1}],
        "products": [{"name": "iron-plate", "amount": 1}],
    },
}


class FakeServer:
    """Answers the two Lua queries the calculator makes, and nothing else."""

    def __init__(self):
        self.queries = []

    async def lua_json(self, expression):
        self.queries.append(expression)
        if "get_entity_filtered" in expression:
            return STATIC_REPLY
        wanted = [name for name in RECIPE_REPLIES if f'"{name}"' in expression]
        return {
            "recipes": {name: RECIPE_REPLIES[name] for name in wanted},
            "producers": {name: [name] for name in wanted},
        }

    def tr(self, key, **kwargs):
        return f"{key}:" + ",".join(f"{k}={v}" for k, v in sorted(kwargs.items()))


@pytest.mark.asyncio
class TestEndToEnd:
    @pytest.fixture(autouse=True)
    def _wired(self, plugin, recipes):
        server = FakeServer()
        plugin._state.clear()
        plugin._state.update(
            config=plugin.DEFAULT_CONFIG, server=server, book=recipes.RecipeBook(server)
        )
        yield server
        plugin._state.clear()

    async def plan_for(self, plugin, recipes, item, rate, modules=None):
        book = plugin._state["book"]
        await book.load_static()
        closure = await book.closure([item])
        machines, unbuildable = book.assign_machines(
            closure, plugin.DEFAULT_CONFIG["machines"], modules or recipes.Modules()
        )
        assert not unbuildable
        scaled = recipes.apply_productivity(closure, machines)
        return plugin.build_plan(scaled, machines, {item: rate})

    async def test_it_walks_the_whole_chain_and_stops_at_ore(self, plugin, recipes):
        plan = await self.plan_for(plugin, recipes, "electronic-circuit", Fraction(5))
        assert {step.recipe for step in plan.steps} == {
            "electronic-circuit", "copper-cable", "copper-plate", "iron-plate",
        }
        assert plan.raw == {"iron-ore": Fraction(5), "copper-ore": Fraction(15, 2)}

    async def test_it_picks_the_configured_machine_per_category(self, plugin, recipes):
        plan = await self.plan_for(plugin, recipes, "electronic-circuit", Fraction(5))
        used = {step.recipe: step.machine for step in plan.steps}
        assert used["electronic-circuit"] == "assembling-machine-3"
        assert used["iron-plate"] == "electric-furnace"

    async def test_smelting_machine_counts_are_right(self, plugin, recipes):
        """5 plates/s at 3.2 s each in a 2x furnace is exactly 8 furnaces."""
        plan = await self.plan_for(plugin, recipes, "electronic-circuit", Fraction(5))
        counts = {step.recipe: step.machines for step in plan.steps}
        assert counts["iron-plate"] == 8

    async def test_modules_change_the_answer(self, plugin, recipes):
        plain = await self.plan_for(plugin, recipes, "electronic-circuit", Fraction(5))
        boosted = await self.plan_for(
            plugin, recipes, "electronic-circuit", Fraction(5),
            recipes.Modules(speed=Fraction(1), productivity=Fraction(1, 10)),
        )
        assert boosted.raw["iron-ore"] < plain.raw["iron-ore"]
        assert sum(s.machines for s in boosted.steps) < sum(s.machines for s in plain.steps)

    async def test_it_renders_without_blowing_up(self, plugin, recipes):
        plan = await self.plan_for(plugin, recipes, "electronic-circuit", Fraction(5))
        lines = plugin._format_plan(
            plugin._state["server"], plan, "electronic-circuit", Fraction(5),
            recipes.Modules(), in_game=True,
        )
        assert lines[0].startswith("plan.header:")
        assert any("plan.raw:" in line for line in lines)
        # In game, names carry their icon tag so the answer is readable.
        assert "[item=electronic-circuit]" in lines[0]

    async def test_one_round_trip_per_level_not_per_item(self, plugin, recipes):
        server = plugin._state["server"]
        await self.plan_for(plugin, recipes, "electronic-circuit", Fraction(5))
        recipe_queries = [q for q in server.queries if "prototypes.recipe" in q]
        # circuit -> {iron-plate, copper-cable} -> copper-plate -> the ores,
        # that last one being how it learns the ores are raw. Four levels, four
        # round trips -- not one per item, and not a dump of all 2000 recipes.
        assert len(recipe_queries) == 4
