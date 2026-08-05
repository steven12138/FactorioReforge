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
    #: Machine preference by name, first match wins. **Empty by default**, which
    #: means "the fastest one this save can actually build".
    #:
    #: It used to list assembling-machine-3 first, so every plan was denominated
    #: in a machine the server might not have researched -- the most useless
    #: answer available. Name machines here to pin them (steel furnaces over
    #: electric ones, say), or pass `machine=` on a single question.
    "machines": [],
    #: Only plan with machines this save can place. Off means the fastest
    #: machine in the game, researched or not.
    "only_researched_machines": True,
    #: Belt used to express throughput in the answer.
    "belt": "transport-belt",
    #: Rate assumed when the command does not say one.
    "default_rate": "1/s",
    #: Items to treat as supplied rather than built -- the walk stops here.
    #:
    #: Steam comes from a boiler or a heat exchanger, and neither is a *recipe*,
    #: so the only thing the recipe graph knows that makes steam is acid
    #: neutralisation. Left to itself the solver duly builds a sulfuric acid
    #: chain to produce steam as a byproduct -- optimal, and not a factory
    #: anyone would build. Steam is an input to a plan, not an output of one.
    "raw_items": ["steam"],
    #: Relative cost of a raw input, which is how the solver breaks ties between
    #: two ways of making the same thing. Water is nearly free on most maps, and
    #: charging it like crude oil makes the solver refuse to crack oil at all.
    #:
    #: Only water. Steam looks equally free -- no recipe produces it, so it
    #: arrives as a raw material -- but it costs a boiler and fuel, and pricing
    #: it at water's rate makes coal liquefaction beat oil processing on a
    #: measured run. It stays at the default cost for that reason.
    "raw_costs": {"water": 0.01},
    #: Chat is not a spreadsheet; long plans are truncated to this many steps.
    "max_steps": 14,
    #: Plan only with recipes this save has actually researched. Off means the
    #: answer can be denominated in things you cannot reach yet.
    "only_researched": True,
    #: Recipe categories to keep out of plans. Recycling is nearly half the
    #: recipes on a 2.0 server and lists what it shreds as a product, which
    #: makes "recycle scrap" the cheapest way to make almost anything.
    "exclude_categories": list(data.EXCLUDED_CATEGORIES),
    #: Recipe names to skip, matched as substrings. Barrelling is a closed loop.
    "exclude_patterns": list(data.EXCLUDED_PATTERNS),
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

#: Wraps a prototype id inside a formatted line, so the finished line can be
#: split back apart and handed to Factorio as a LocalisedString. NUL never
#: appears in a translation or an item name, so it cannot collide with content.
NAME_MARK = "\x00"

#: Factorio caps a LocalisedString at 20 parameters. A line past that is sent
#: as plain text rather than silently truncated by the game.
MAX_LOCALISED_PARTS = 20

#: ``[item=iron-plate]``, which is what a player gets from the in-game icon
#: picker. Accepting it means the icon they inserted *is* a valid argument.
RICH_TEXT = re.compile(r"\[(?:item|fluid|entity|recipe)=([\w\-]+)(?:,[^\]]*)?\]")

RATE_UNITS = {
    "s": Fraction(1), "sec": Fraction(1), "second": Fraction(1),
    "m": Fraction(1, 60), "min": Fraction(1, 60), "minute": Fraction(1, 60),
    "h": Fraction(1, 3600), "hour": Fraction(1, 3600),
}
RATE = re.compile(r"^([\d.]+)\s*(?:/\s*([a-z]+))?$", re.I)

#: ``prod=20``, ``in:iron-plate=foundry``, ``cost:water=0.5``. Deliberately
#: narrow so a rich-text icon is never mistaken for one.
OPTION = re.compile(r"^[a-z][\w:.-]*=", re.I)


#: What "machines" used to default to. A config file written by that version
#: still says this, and load_config_simple never overwrites a key that is
#: already there -- so changing the default to [] fixed nothing for anybody who
#: had already run the plugin once. Their answers stayed denominated in
#: assembling machine 3 whether or not the save had researched it, which is the
#: exact complaint the change was meant to answer.
LEGACY_MACHINES = [
    "assembling-machine-3",
    "electric-furnace",
    "chemical-plant",
    "oil-refinery",
    "centrifuge",
    "rocket-silo",
]


def migrate_machines(config: dict) -> bool:
    """Turn the old hardcoded default back into "pick the best I can build".

    Only the exact old list is touched. Somebody who chose those machines *and*
    reordered or trimmed the list meant it, and a preference someone set on
    purpose is not ours to discard.
    """
    if list(config.get("machines") or []) != LEGACY_MACHINES:
        return False
    config["machines"] = []
    return True


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    if migrate_machines(config):
        server.save_config_simple(config)
        server.logger.info(server.tr("machine.migrated"))
    _state.clear()
    _state.update(config=config, server=server, book=data.RecipeBook(
        server,
        excluded_categories=config.get("exclude_categories"),
        excluded_patterns=config.get("exclude_patterns"),
        only_unlocked=config.get("only_researched", True),
    ))

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
        .then(
            Literal("machine")
            .runs(_cmd_machine_show)
            .then(Literal("auto").requires(PermissionLevel.HELPER).runs(_cmd_machine_auto))
            .then(
                Literal("use").requires(PermissionLevel.HELPER)
                .then(GreedyText("name").runs(_cmd_machine_use))
            )
            .then(
                Literal("drop").requires(PermissionLevel.HELPER)
                .then(GreedyText("name").runs(_cmd_machine_drop))
            )
        )
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
            server.tr("detail.machine"),
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
        # `[item=iron-plate]` is an argument, not an option. It contains an `=`
        # because that is how Factorio writes an icon, and splitting on it turns
        # the item the player picked from the in-game selector into a key called
        # "[item".
        if OPTION.match(word):
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


# ---------------------------------------------------------------------------
# which machine the plan is denominated in
# ---------------------------------------------------------------------------
#
# The default is "the best one this save can actually place", which is what
# somebody who has only researched stone furnaces wants to be told. Pinning is
# for the server that has electric furnaces and wants steel ones anyway.

async def _cmd_machine_show(source):
    server = source.server
    book = _state["book"]
    pinned = [normalise(n) for n in (_state["config"].get("machines") or [])]
    # The whole plugin registers its commands under literal "!!ratio"; there is
    # no configurable prefix here to thread through.
    prefix = "!!ratio machine"

    try:
        await book.load_static()
    except QueryError as exc:
        await source.reply(server.tr("machine.no_data", error=exc))
        # The pins are config, not game data, so they are still worth saying.
        await _say_pins(source, pinned, prefix)
        return

    unlocked, locked = _machine_groups(book, pinned, _researched_machines())

    await source.reply(server.tr("machine.header"))
    for machine, categories in unlocked:
        mark = server.tr("machine.why_pinned") if machine in pinned else ""
        await source.reply(f"  {machine}{mark}  --  {_join_categories(categories)}")
    if not unlocked:
        await source.reply(server.tr("machine.none_unlocked"))
    if locked:
        await source.reply(server.tr(
            "machine.locked_summary",
            count=sum(len(c) for _, c in locked),
            machines=", ".join(machine for machine, _ in locked[:4]),
        ))

    await _say_pins(source, pinned, prefix)


#: Categories per machine before the line says "and N more". A machine that
#: runs twenty categories is not more informative for listing all twenty.
MAX_CATEGORIES = 5


def _machine_groups(book, pinned: list[str], researched_only: bool = True):
    """The chosen machine per category, grouped by machine and split by whether
    the save can build it.

    Grouped because one machine covers a dozen categories -- a line each is
    twenty-nine lines of chat, which is longer than the chat box and mostly
    repeats. Split because "what am I planning in" and "what would I be
    planning in if I researched it" are different questions, and only the first
    is worth reading in full.
    """
    chosen: dict[str, list[str]] = {}
    for category in sorted(_interesting_categories(book)):
        machine = book.best_machine(category, pinned, researched_only=researched_only)
        if machine is not None:
            chosen.setdefault(machine.name, []).append(category)

    unlocked, locked = [], []
    for machine, categories in sorted(chosen.items()):
        (unlocked if machine in book.buildable else locked).append((machine, categories))
    # Most-used machine first: that is the one the answers are written in.
    unlocked.sort(key=lambda pair: (-len(pair[1]), pair[0]))
    locked.sort(key=lambda pair: (-len(pair[1]), pair[0]))
    return unlocked, locked


def _join_categories(categories: list[str]) -> str:
    shown = ", ".join(categories[:MAX_CATEGORIES])
    extra = len(categories) - MAX_CATEGORIES
    return f"{shown} +{extra}" if extra > 0 else shown


async def _say_pins(source, pinned: list[str], prefix: str) -> None:
    server = source.server
    if pinned:
        await source.reply(server.tr("machine.pinned", machines=", ".join(pinned)))
        await source.reply(server.tr("machine.auto_hint", prefix=prefix))
    else:
        await source.reply(server.tr("machine.automatic", prefix=prefix))


def _researched_machines() -> bool:
    return bool(_state["config"].get("only_researched_machines", True))


def _interesting_categories(book) -> set[str]:
    """Categories something can actually be built in.

    Every machine prototype declares its categories, so this is the set the
    answer can be denominated in -- not the set of recipe categories, which
    includes ones no machine in the game runs.
    """
    return {category for machine in book.machines.values() for category in machine.categories}


async def _cmd_machine_auto(source):
    config = _state["config"]
    config["machines"] = []
    source.server.save_config_simple(config)
    await source.reply(source.server.tr("machine.now_automatic"))


async def _cmd_machine_use(source, ctx):
    server = source.server
    book = _state["book"]
    name = normalise(str(ctx.get("name", "")))

    try:
        await book.load_static()
    except QueryError as exc:
        await source.reply(server.tr("machine.no_data", error=exc))
        return

    if name not in book.machines:
        near = [m for m in sorted(book.machines) if name and name in m][:5]
        await source.reply(server.tr("machine.unknown", name=name))
        if near:
            await source.reply(server.tr("machine.did_you_mean", names=", ".join(near)))
        return

    config = _state["config"]
    pinned = [n for n in (config.get("machines") or []) if normalise(n) != name]
    # First match wins when a machine is chosen, so a newly named one goes to
    # the front: naming it is how you say you want it.
    config["machines"] = [name, *pinned]
    server.save_config_simple(config)

    machine = book.machines[name]
    await source.reply(server.tr(
        "machine.now_pinned", name=name, categories=", ".join(sorted(machine.categories))))
    if name not in book.buildable:
        await source.reply(server.tr("machine.not_unlocked", name=name))


async def _cmd_machine_drop(source, ctx):
    server = source.server
    name = normalise(str(ctx.get("name", "")))
    config = _state["config"]
    pinned = list(config.get("machines") or [])
    kept = [n for n in pinned if normalise(n) != name]

    if len(kept) == len(pinned):
        await source.reply(server.tr("machine.not_pinned", name=name))
        return
    config["machines"] = kept
    server.save_config_simple(config)
    await source.reply(server.tr("machine.dropped", name=name))


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
    modules = await _modules(book, options)
    unlocked_only = _unlocked_only(options)

    # Researched recipes first, everything second. A plan you cannot build yet
    # is worth having -- that is what research is for -- but only once the plan
    # you *can* build has been ruled out.
    plan = None
    used_everything = False
    for restrict in ([True, False] if unlocked_only else [False]):
        try:
            plan = await _solve(book, item, rate, options, modules, restrict)
        except QueryError as exc:
            await source.reply(server.tr("error.needs_server", error=exc))
            return
        except RecursionError as exc:
            await source.reply(server.tr("error.too_deep", error=exc))
            return
        except Unbounded:
            await source.reply(server.tr("error.unbounded"))
            return
        except (_NoRecipe, Infeasible):
            continue
        used_everything = not restrict
        break

    if plan is None:
        await source.reply(await _no_such_item(server, item))
        return

    in_game = source.player is not None
    if used_everything and unlocked_only:
        await source.reply(server.tr("plan.not_researched"))
    for line in _format_plan(server, plan, item, rate, modules, in_game=in_game):
        await _say(source, line)

    # An input that is neither mined nor declared raw is something a recipe
    # makes -- it is only an input here because that recipe is not researched.
    # Saying so beats listing copper cable next to iron ore as if both came out
    # of the ground.
    if unlocked_only and not used_everything:
        supplied = _raw_items(options) | book.mined
        blocked = sorted(name for name in plan.raw if name not in supplied)
        if blocked:
            await _say(source, server.tr(
                "plan.unresearched", items=", ".join(_icon(n, in_game) for n in blocked)
            ))


class _NoRecipe(Exception):
    """Nothing available produces the item, at least under this restriction."""


async def _solve(book, item, rate, options, modules, only_unlocked):
    """One attempt: fetch the closure, assign machines, solve."""
    await book.load_static()
    book.set_unlocked_only(only_unlocked)

    recipes = await book.closure(
        [item], raw=_raw_items(options), prefer=_preferred_recipes(options)
    )
    if not recipes:
        raise _NoRecipe(item)

    machines, unbuildable = book.assign_machines(
        recipes, _machine_preference(options), modules, _machine_overrides(options),
        researched_only=only_unlocked
        and _researched_machines(),
    )
    for name in unbuildable:
        recipes.pop(name, None)
    if not recipes:
        raise _NoRecipe(item)

    scaled = data.apply_productivity(recipes, machines)
    return build_plan(
        scaled, machines, {item: rate},
        raw_costs=_raw_costs(options), mined=book.mined,
    )


def _unlocked_only(options: dict[str, str]) -> bool:
    """``all=1`` plans with every recipe, researched or not."""
    if options.get("all", "").lower() in ("1", "true", "yes", "on"):
        return False
    return bool(_state["config"].get("only_researched", True))


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
    names = [normalise(n) for n in (_state["config"].get("machines") or [])]
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
    """An item tag renders as the icon in game and as noise anywhere else.

    In game the name is wrapped in markers so :func:`localise` can hand it to
    Factorio for translation. ``iron-plate`` is not a word in any language, and
    a plan written in prototype ids is unreadable to most of the people reading
    it.
    """
    if not in_game:
        return name
    return f"{lua.item_tag(name)}{NAME_MARK}{name}{NAME_MARK}"


def localise(text: str) -> list | None:
    """Split a marked-up line into a LocalisedString, or None if it is plain.

    Each client renders the result in its own language, which is the only way
    one message reads correctly for a Chinese player and an English one at the
    same time -- the translation happens on their machine, from the game's own
    catalogue, so there is nothing here to keep up to date.
    """
    if NAME_MARK not in text:
        return None
    chunks = text.split(NAME_MARK)
    parts: list = [""]
    for index, chunk in enumerate(chunks):
        if index % 2 == 0:
            if chunk:
                parts.append(chunk)
        else:
            parts.append(lua.localised_name(chunk))
    return parts if len(parts) <= MAX_LOCALISED_PARTS else None


def plain(text: str) -> str:
    """The same line with the markers taken out, for the console and Telegram."""
    return text.replace(NAME_MARK, "")


async def _say(source, text: str) -> None:
    """Reply, translated by the game when the reader is in the game.

    Falls back to the plain line everywhere else: the console and Telegram have
    no Factorio to render a LocalisedString, and a partial answer is worse than
    an untranslated one.
    """
    parts = localise(text) if source.player else None
    if parts is None:
        await source.reply(plain(text))
        return
    try:
        await source.server.tell_localised(source.player, parts)
    except QueryError:
        await source.reply(plain(text))


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
        # For one product the useful number is items per second. For oil
        # processing, which makes three fluids at once, no single item rate
        # describes the step, so it is quoted in crafts and labelled as such.
        single = len(step.outputs) == 1
        lines.append(server.tr(
            "plan.step" if single else "plan.step_crafts",
            machines=_num(step.machines),
            machine=_icon(step.machine, in_game),
            recipe=_icon(step.recipe, in_game),
            rate=_num(sum(step.outputs.values()) if single else step.crafts_per_second),
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
    words, _, options = parse_query((ctx or {}).get("item", ""))
    item = await _resolve_item(server, source, words)
    if not item:
        await source.reply(server.tr("ratio.what"))
        return

    book = _state["book"]
    try:
        await book.load_static()
        # The book caches per mode, so a previous `all=1` question would
        # otherwise decide what this one is allowed to see.
        book.set_unlocked_only(_unlocked_only(options))
        await book.fetch([item])
        found = dict.fromkeys(book.producers.get(item) or [])
        if not found:
            # Not an item, but possibly a recipe: `!!recipe
            # advanced-oil-processing` is a reasonable thing to type and there
            # is no item by that name.
            named = await server.lua_json(data.recipes_named([item])) or {}
            for name, raw in named.items():
                book.recipes[name] = data.parse_recipe(name, raw)
                found[name] = None
    except QueryError as exc:
        await source.reply(server.tr("error.needs_server", error=exc))
        return

    names = list(found)
    if not names:
        await source.reply(await _no_such_item(server, item))
        return

    in_game = source.player is not None
    for name in names[:4]:
        recipe = book.recipes[name]
        machine = book.best_machine(recipe.category, _machine_preference({}))
        await _say(source, server.tr(
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
            await _say(source, server.tr(
                "recipe.machine", machine=_icon(machine.name, in_game),
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
