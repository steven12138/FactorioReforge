"""A calculator in the chat box: arithmetic, and Factorio production ratios.

Two things, sharing a plugin because they are the same reflex -- you are in the
middle of building and you need a number now, not in a browser tab.

``==1400/7.5`` answers arithmetic. ``!!ratio`` answers the real question: what
does it take to make this, how many machines, how many belts, how much power.

The ratio side follows the algorithm every mature Factorio calculator converged
on (Kirk McDonald's, FactorioLab, YAFC): recipes become a matrix, and the rates
are a linear program solved with exact rational arithmetic. See ``solver.py``
for why a tree walk is not enough, and ``recipes.py`` for why the numbers come
out of the running game rather than out of a table in this repository.

The input side is where being *inside* the game pays off. Point at an assembler
and type ``!!ratio`` -- no item name, no spelling. See :func:`_resolve_item`.
"""

from __future__ import annotations

import re
from fractions import Fraction

from factorio_reforge.command.builder import GreedyText, Literal
from factorio_reforge.core import lua
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

from . import recipes as data
from .expr import CalcError, evaluate, format_number
from .solver import Infeasible, Number, Plan, Unbounded, build_plan

PLUGIN_METADATA = {
    "id": "calculator",
    "version": "1.0.0",
    "name": "Calculator",
    "description": "Chat arithmetic, and production ratios solved from live recipe data",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    #: Type this in chat and the rest of the line is evaluated.
    "expression_prefix": "==",
    #: Everyone saw the question go by, so everyone gets the answer. Off means
    #: the result goes only to whoever asked.
    "announce_expression_results": True,
    #: Machine preference per crafting category, first match wins. Anything not
    #: named here falls back to the fastest machine that can run the recipe.
    "machines": [
        "assembling-machine-3", "electric-furnace", "chemical-plant",
        "oil-refinery", "centrifuge", "rocket-silo",
    ],
    #: Belt used to express throughput in the answer.
    "belt": "transport-belt",
    #: Rate assumed when the command does not say one.
    "default_rate": "1/s",
    #: Items to treat as bought in rather than built -- the walk stops here.
    "raw_items": [],
    #: Relative cost of a raw input, which is how the solver breaks ties between
    #: two ways of making the same thing. Water is nearly free on most maps, and
    #: charging it like crude oil makes the solver refuse to crack oil at all.
    "raw_costs": {"water": 0.01, "steam": 0.01},
    #: Chat is not a spreadsheet; long plans are truncated to this many steps.
    "max_steps": 14,
    #: Short names for things nobody wants to spell out.
    "aliases": {
        "green-circuit": "electronic-circuit",
        "red-circuit": "advanced-circuit",
        "blue-circuit": "processing-unit",
        "green-science": "logistic-science-pack",
        "red-science": "automation-science-pack",
        "blue-science": "chemical-science-pack",
        "purple-science": "production-science-pack",
        "yellow-science": "utility-science-pack",
        "gear": "iron-gear-wheel",
        "belt": "transport-belt",
    },
}

_state: dict = {}

#: ``[item=iron-plate]``, which is what a player gets from the in-game icon
#: picker. Accepting it means the icon they inserted *is* a valid argument.
RICH_TEXT = re.compile(r"\[(?:item|fluid|entity|recipe)=([\w\-]+)(?:,[^\]]*)?\]")

RATE_UNITS = {
    "s": Fraction(1), "sec": Fraction(1), "second": Fraction(1),
    "m": Fraction(1, 60), "min": Fraction(1, 60), "minute": Fraction(1, 60),
    "h": Fraction(1, 3600), "hour": Fraction(1, 3600),
}
RATE = re.compile(r"^([\d.]+)\s*(?:/\s*([a-z]+))?$", re.I)


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, server=server, book=data.RecipeBook(server))

    prefix = str(config.get("expression_prefix") or "==")

    server.register_command(
        Literal("!!calc")
        .requires(PermissionLevel.USER)
        .then(GreedyText("expression").runs(_cmd_calc))
    )
    server.register_command(
        Literal("!!ratio")
        .requires(PermissionLevel.USER)
        .runs(_cmd_ratio)
        .then(Literal("refresh").requires(PermissionLevel.HELPER).runs(_cmd_refresh))
        .then(GreedyText("query").runs(_cmd_ratio))
    )
    server.register_command(
        Literal("!!recipe")
        .requires(PermissionLevel.USER)
        .runs(_cmd_recipe)
        .then(GreedyText("item").runs(_cmd_recipe))
    )
    server.register_command(
        Literal("!!belt")
        .requires(PermissionLevel.USER)
        .then(GreedyText("rate").runs(_cmd_belt))
    )

    server.register_help_message(
        f"{prefix}<expression>", server.tr("help.expression"), PermissionLevel.USER
    )
    server.register_help_message(
        "!!ratio [item] [rate]", server.tr("help.ratio"), PermissionLevel.USER,
        detail=(
            server.tr("detail.pointing"),
            server.tr("detail.options"),
            server.tr("detail.example"),
        ),
    )
    server.register_help_message("!!recipe [item]", server.tr("help.recipe"), PermissionLevel.USER)
    server.register_help_message("!!belt <rate>", server.tr("help.belt"), PermissionLevel.USER)

    _register_telegram(server)
    server.register_event_listener("telegram.ready", lambda s: _register_telegram(s))


async def on_unload(server):
    _state.clear()


def on_server_startup(server):
    """A restart can mean a changed mod list, so nothing cached survives it."""
    book = _state.get("book")
    if book is not None:
        book.clear()


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

async def on_user_info(server, info):
    """The ``==`` shortcut. Chat only -- the console has ``!!calc``.

    Console input that is not a command is chat, so answering it here would put
    the operator's arithmetic in front of every player.
    """
    if not _state or not info.player or not info.is_from_server:
        return
    prefix = str(_state["config"].get("expression_prefix") or "==")
    text = (info.content or "").strip()
    if not prefix or not text.startswith(prefix):
        return
    await _answer_expression(server, info.player, text[len(prefix):])


async def _answer_expression(server, player: str | None, expression: str) -> str:
    try:
        result = evaluate(expression)
    except CalcError as exc:
        message = server.tr("expression.failed", error=exc)
    else:
        message = server.tr(
            "expression.result",
            expression=expression.strip(),
            result=format_number(result),
        )
    if player is None:
        return message
    if _state["config"].get("announce_expression_results", True):
        await server.game_print(message)
    else:
        await server.tell(player, message)
    return message


async def _cmd_calc(source, ctx):
    await source.reply(await _answer_expression(source.server, None, ctx["expression"]))


# ---------------------------------------------------------------------------
# Parsing what was asked
# ---------------------------------------------------------------------------

def normalise(name: str) -> str:
    """``Green Circuit`` and ``[item=iron-plate]`` both become a prototype name."""
    tag = RICH_TEXT.search(name)
    if tag:
        return tag.group(1)
    cleaned = name.strip().lower().replace("_", "-")
    cleaned = re.sub(r"\s+", "-", cleaned)
    return _state["config"].get("aliases", {}).get(cleaned, cleaned)


def parse_rate(text: str) -> Number | None:
    """``5``, ``5/s``, ``300/m``, ``90/hour`` -- all as items per second."""
    match = RATE.match(text.strip())
    if not match:
        return None
    try:
        amount = Fraction(match.group(1)).limit_denominator(1_000_000)
    except (ValueError, ZeroDivisionError):
        return None
    unit = (match.group(2) or "s").lower()
    if unit not in RATE_UNITS:
        return None
    return amount * RATE_UNITS[unit]


def parse_query(text: str) -> tuple[list[str], Number | None, dict[str, str]]:
    """Split ``iron-gear-wheel 30/m prod=20 machine=foundry`` into its parts.

    Options are ``key=value`` anywhere in the line, so the order a player types
    them in never matters.
    """
    options: dict[str, str] = {}
    words: list[str] = []
    for word in text.split():
        if "=" in word and not word.startswith("="):
            key, _, value = word.partition("=")
            options[key.strip().lower()] = value.strip()
        else:
            words.append(word)

    rate: Number | None = None
    if words:
        parsed = parse_rate(words[-1])
        if parsed is not None:
            rate = parsed
            words.pop()
    return words, rate, options


async def _resolve_item(server, source, words: list[str]) -> str | None:
    """The item to plan for: what was typed, or what the player is pointing at.

    Falling back to the cursor is the whole reason this is worth running inside
    the game. Hovering an assembler is better still: the recipe set in it is
    more specific than the item, because an item can have several recipes and
    the machine already says which one you chose.
    """
    if words:
        return normalise(" ".join(words))
    if source.player is None:
        return None
    try:
        context = await server.lua_json(data.player_context(source.player)) or {}
    except QueryError:
        return None
    return context.get("recipe_product") or context.get("cursor") or context.get("entity")


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

async def _cmd_refresh(source):
    _state["book"].clear()
    await source.reply(source.server.tr("refresh.done"))


async def _cmd_ratio(source, ctx=None):
    server = source.server
    words, rate, options = parse_query((ctx or {}).get("query", ""))

    item = await _resolve_item(server, source, words)
    if not item:
        await source.reply(server.tr("ratio.what"))
        return
    if rate is None:
        rate = parse_rate(str(_state["config"].get("default_rate", "1/s"))) or Fraction(1)

    book = _state["book"]
    try:
        await book.load_static()
        raw = _raw_items(options)
        prefer = _preferred_recipes(options)
        recipes = await book.closure([item], raw=raw, prefer=prefer)
    except QueryError as exc:
        await source.reply(server.tr("error.needs_server", error=exc))
        return
    except RecursionError as exc:
        await source.reply(server.tr("error.too_deep", error=exc))
        return

    if not recipes:
        await source.reply(await _no_such_item(server, item))
        return

    modules = await _modules(book, options)
    preferred = _machine_preference(options)
    machines, unbuildable = book.assign_machines(recipes, preferred, modules, _machine_overrides(options))
    for name in unbuildable:
        recipes.pop(name, None)
    if not recipes:
        await source.reply(server.tr("error.no_machine", item=item))
        return

    scaled = data.apply_productivity(recipes, machines)
    try:
        plan = build_plan(scaled, machines, {item: rate}, raw_costs=_raw_costs(options))
    except Infeasible:
        await source.reply(server.tr("error.infeasible", item=item))
        return
    except Unbounded:
        await source.reply(server.tr("error.unbounded"))
        return

    for line in _format_plan(server, plan, item, rate, modules, in_game=source.player is not None):
        await source.reply(line)


def _raw_items(options: dict[str, str]) -> set[str]:
    configured = {normalise(name) for name in _state["config"].get("raw_items", [])}
    if options.get("raw"):
        configured |= {normalise(part) for part in options["raw"].split(",") if part}
    return configured


def _raw_costs(options: dict[str, str]) -> dict[str, Number]:
    """Config costs, plus ``cost:water=0.5`` for a one-off question."""
    costs: dict[str, Number] = {}
    for name, value in (_state["config"].get("raw_costs") or {}).items():
        costs[normalise(name)] = Fraction(str(value)).limit_denominator(10_000)
    for key, value in options.items():
        if key.startswith("cost:"):
            try:
                costs[normalise(key[5:])] = Fraction(value).limit_denominator(10_000)
            except (ValueError, ZeroDivisionError):
                continue
    return costs


def _preferred_recipes(options: dict[str, str]) -> dict[str, str]:
    """``use=advanced-oil-processing`` pins the recipe for whatever it makes.

    The product is not known until the recipe is fetched, so this is keyed by
    recipe name and resolved by :meth:`RecipeBook.closure`; a recipe named after
    its product -- which is nearly all of them -- just works.
    """
    picks = {}
    for value in options.get("use", "").split(","):
        name = normalise(value)
        if name:
            picks[name] = name
    return picks


def _machine_preference(options: dict[str, str]) -> list[str]:
    names = [normalise(n) for n in _state["config"].get("machines", [])]
    for key in ("machine", "machines", "furnace"):
        if options.get(key):
            names = [normalise(n) for n in options[key].split(",")] + names
    return names


def _machine_overrides(options: dict[str, str]) -> dict[str, str]:
    """``in:iron-plate=foundry`` -- one recipe, one machine."""
    return {
        normalise(key[3:]): normalise(value)
        for key, value in options.items()
        if key.startswith("in:")
    }


async def _modules(book, options: dict[str, str]) -> data.Modules:
    """``prod=20 speed=50`` as percentages, or ``modules=speed-module-3*4``.

    Beacons are deliberately not modelled: 2.0 gives beacons a diminishing
    ``profile`` by count, and a beacon number that is quietly wrong is worse
    than one the calculator never claimed to know. ``speed=`` is the escape
    hatch for anyone who has worked theirs out.
    """
    modules = data.Modules()
    if options.get("prod"):
        modules.productivity = _percent(options["prod"])
    if options.get("speed"):
        modules.speed = _percent(options["speed"])

    for entry in options.get("modules", "").split(","):
        if not entry:
            continue
        name, _, count = entry.partition("*")
        effects = book.modules.get(normalise(name))
        if effects is None:
            continue
        multiplier = Fraction(int(count)) if count.isdigit() else Fraction(1)
        modules.speed += data.module_effect(effects, "speed") * multiplier
        modules.productivity += data.module_effect(effects, "productivity") * multiplier
    return modules


def _percent(text: str) -> Number:
    try:
        return Fraction(text.rstrip("%")).limit_denominator(10_000) / 100
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


async def _no_such_item(server, item: str) -> str:
    try:
        matches = await server.lua_json(data.search_items(item)) or []
    except QueryError:
        matches = []
    if matches:
        return server.tr("error.no_recipe_hint", item=item, matches=", ".join(matches))
    return server.tr("error.no_recipe", item=item)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _num(value: Number, places: int = 2) -> str:
    return format_number(round(float(value), places))


def _icon(name: str, in_game: bool) -> str:
    """An item tag renders as the icon in game and as noise anywhere else."""
    return f"{lua.item_tag(name)}{name}" if in_game else name


def _rate(value: Number, in_game: bool, name: str | None = None) -> str:
    text = f"{_num(value)}/s"
    return f"{_icon(name, in_game)} {text}" if name else text


def _format_plan(server, plan: Plan, item: str, rate: Number, modules, in_game: bool):
    lines = [server.tr(
        "plan.header",
        item=_icon(item, in_game),
        rate=_num(rate),
        machines=_num(sum((step.machines for step in plan.steps), Fraction(0))),
        power=_watts(plan.power_watts),
    )]
    note = modules.describe()
    if note:
        lines.append(server.tr("plan.modules", modules=note))

    limit = int(_state["config"].get("max_steps", 14))
    for step in plan.steps[:limit]:
        produced = sum(step.outputs.values()) if len(step.outputs) == 1 else None
        lines.append(server.tr(
            "plan.step",
            machines=_num(step.machines),
            machine=step.machine,
            recipe=_icon(step.recipe, in_game),
            rate=_num(produced if produced is not None else step.crafts_per_second),
        ))
    if len(plan.steps) > limit:
        lines.append(server.tr("plan.more", count=len(plan.steps) - limit))

    if plan.raw:
        lines.append(server.tr("plan.raw", items=_items(plan.raw, in_game)))
    if plan.surplus:
        lines.append(server.tr("plan.surplus", items=_items(plan.surplus, in_game)))

    belt = _belt_line(server, plan, item, rate)
    if belt:
        lines.append(belt)
    return lines


def _items(rates: dict[str, Number], in_game: bool, limit: int = 8) -> str:
    ordered = sorted(rates.items(), key=lambda kv: -kv[1])
    shown = ", ".join(_rate(amount, in_game, name) for name, amount in ordered[:limit])
    if len(ordered) > limit:
        shown += f", +{len(ordered) - limit}"
    return shown


def _belt_line(server, plan: Plan, item: str, rate: Number) -> str | None:
    book = _state["book"]
    name = normalise(str(_state["config"].get("belt", "transport-belt")))
    throughput = book.belts.get(name)
    if not throughput:
        return None
    return server.tr(
        "plan.belts", count=_num(rate / throughput), belt=name,
        throughput=_num(throughput),
    )


def _watts(value: Number) -> str:
    watts = float(value)
    for unit, scale in (("GW", 1e9), ("MW", 1e6), ("kW", 1e3)):
        if abs(watts) >= scale:
            return f"{watts / scale:,.2f} {unit}"
    return f"{watts:,.0f} W"


# ---------------------------------------------------------------------------
# The smaller lookups
# ---------------------------------------------------------------------------

async def _cmd_recipe(source, ctx=None):
    server = source.server
    words, _, _ = parse_query((ctx or {}).get("item", ""))
    item = await _resolve_item(server, source, words)
    if not item:
        await source.reply(server.tr("ratio.what"))
        return

    book = _state["book"]
    try:
        await book.load_static()
        await book.fetch([item])
    except QueryError as exc:
        await source.reply(server.tr("error.needs_server", error=exc))
        return

    names = book.producers.get(item) or []
    if not names:
        await source.reply(await _no_such_item(server, item))
        return

    in_game = source.player is not None
    for name in names[:4]:
        recipe = book.recipes[name]
        machine = book.best_machine(recipe.category, _machine_preference({}))
        await source.reply(server.tr(
            "recipe.line",
            recipe=_icon(name, in_game),
            seconds=_num(recipe.energy),
            ingredients=", ".join(
                f"{_num(amount)} x {_icon(ing, in_game)}"
                for ing, amount in sorted(recipe.ingredients.items())
            ) or "-",
            products=", ".join(
                f"{_num(amount)} x {_icon(prod, in_game)}"
                for prod, amount in sorted(recipe.products.items())
            ),
        ))
        if machine is not None:
            per_second = machine.speed / recipe.energy
            await source.reply(server.tr(
                "recipe.machine", machine=machine.name,
                speed=_num(machine.speed), rate=_num(per_second),
            ))


async def _cmd_belt(source, ctx):
    """How many belts a rate needs, on every belt tier the game has."""
    server = source.server
    rate = parse_rate(ctx["rate"])
    if rate is None:
        await source.reply(server.tr("belt.usage"))
        return

    book = _state["book"]
    try:
        await book.load_static()
    except QueryError as exc:
        await source.reply(server.tr("error.needs_server", error=exc))
        return

    if not book.belts:
        await source.reply(server.tr("belt.none"))
        return
    await source.reply(server.tr("belt.header", rate=_num(rate)))
    for name, throughput in sorted(book.belts.items(), key=lambda kv: kv[1]):
        await source.reply(server.tr(
            "belt.line", count=_num(rate / throughput), belt=name,
            throughput=_num(throughput), lane=_num(throughput / 2),
        ))


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _register_telegram(server):
    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is None:
        return
    bridge.register_command(
        "calculator", "calc", _telegram_calc, level="viewer",
        help=server.tr("help.expression"),
    )


async def _telegram_calc(ctx):
    server = _state.get("server")
    if server is None:
        return
    await ctx.reply(await _answer_expression(server, None, ctx.text))
