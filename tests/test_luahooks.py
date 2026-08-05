"""Pushing real Factorio events out of the game instead of polling for them.

This whole module exists because an assumption turned out to be wrong. The
project was built believing RCON could not register event handlers, so
everything was polling. Measured on a live 2.0.77 server:

* ``script`` is available inside ``/sc`` and ``script.on_event`` registers a
  handler that really fires -- hooked ``on_tick``, wrote ``game.tick`` into
  ``storage``, read it back one tick later;
* ``print()`` from inside that handler reaches stdout, which the parser already
  reads. ``game.print`` does not -- it goes to in-game chat only.

The tests below are about the three ways this can go wrong quietly.
"""

from __future__ import annotations

import json

import pytest

from factorio_reforge.core import luahooks
from factorio_reforge.core.luahooks import SENTINEL, UnknownEvent


class TestRegistrationLua:
    def test_it_chains_rather_than_replacing(self):
        """The measurement that makes this mandatory.

        ``on_research_finished`` and ``on_player_created`` already have
        handlers on a plain freeplay save. ``script.on_event`` overwrites, so a
        naive registration deletes the scenario's own behaviour and says
        nothing about it.
        """
        code = luahooks.build_registration(["on_research_finished"])
        assert "script.get_event_handler(id)" in code
        assert "if prev then prev(e) end" in code

    def test_the_scenario_handler_runs_before_ours(self):
        """If our payload ever raises, the game keeps its own behaviour."""
        code = luahooks.build_registration(["on_research_finished"])
        assert code.index("prev(e)") < code.index("print(line)")

    def test_our_side_cannot_break_the_game(self):
        """Building the line is wrapped: a bad payload loses an event, not a save."""
        code = luahooks.build_registration(["on_player_died"])
        assert "pcall(function()" in code
        assert "if ok then print(line) end" in code

    def test_it_reports_what_it_hooked(self):
        code = luahooks.build_registration(["on_rocket_launched"])
        assert "hooked" in code and "return {hooked = hooked}" in code

    def test_an_event_missing_from_this_factorio_is_skipped(self):
        """A version bump should cost a feature, not the server."""
        code = luahooks.build_registration(["on_research_finished"])
        assert "if id ~= nil then" in code

    def test_every_bridged_event_builds(self):
        code = luahooks.build_registration(sorted(luahooks.BRIDGED))
        for name in luahooks.BRIDGED:
            assert f"defines.events.{name}" in code

    def test_asking_for_something_unbridged_is_refused(self):
        with pytest.raises(UnknownEvent, match="on_tick"):
            luahooks.build_registration(["on_tick"])

    def test_entity_died_is_not_bridged(self):
        """It fires thousands of times a minute on a defended base."""
        assert "on_entity_died" not in luahooks.BRIDGED

    def test_the_payload_is_json_not_string_formatting(self):
        """Player and entity names can contain anything, separators included."""
        code = luahooks.build_registration(["on_player_died"])
        assert "helpers.table_to_json" in code


class TestParsing:
    def line(self, **payload) -> str:
        return SENTINEL + json.dumps(payload)

    def test_a_bridged_event_comes_back_as_data(self):
        parsed = luahooks.parse_line(
            self.line(event="on_research_finished", name="automation", level=1)
        )
        assert parsed == {"event": "on_research_finished", "name": "automation", "level": 1}

    def test_an_ordinary_line_is_not_one(self):
        assert luahooks.parse_line("Hosting game at IP ADDR:({0.0.0.0:34197})") is None

    def test_an_empty_line_is_not_one(self):
        assert luahooks.parse_line("") is None

    def test_rubbish_after_the_sentinel_is_ignored_not_raised(self):
        """Somebody else's print starting with the sentinel must not kill the parser."""
        assert luahooks.parse_line(SENTINEL + "not json at all") is None

    def test_json_that_is_not_an_object_is_ignored(self):
        assert luahooks.parse_line(SENTINEL + "[1, 2, 3]") is None

    def test_a_payload_with_no_event_name_is_ignored(self):
        assert luahooks.parse_line(SENTINEL + '{"name": "automation"}') is None

    def test_a_name_with_a_space_survives_the_round_trip(self):
        """The reason this is JSON and not "%s %s"."""
        parsed = luahooks.parse_line(
            self.line(event="on_player_died", player="Some One", cause="small biter")
        )
        assert parsed["player"] == "Some One"

    def test_the_sentinel_cannot_collide_with_engine_output(self):
        """Chat and engine lines both carry a timestamp prefix; ours is bare."""
        assert not SENTINEL[0].isalnum()
        assert " " not in SENTINEL


class TestRemoval:
    def test_removal_covers_the_named_events(self):
        code = luahooks.build_removal(["on_research_finished"])
        assert "script.on_event(id, nil)" in code

    def test_removal_says_what_it_cannot_undo(self):
        """It takes the scenario's handler with it -- the closure is unreachable.

        Documented in the function rather than discovered on a live server,
        which is where it was discovered.
        """
        assert "previous handler was captured in a closure" in luahooks.build_removal.__doc__


class TestReactorDelivery:
    """A printed line has to become a plugin event, and not console noise."""

    @pytest.fixture
    def wiring(self):
        import asyncio
        import logging

        from factorio_reforge.core.handler import FactorioHandler
        from factorio_reforge.core.reactor import InfoReactor
        from factorio_reforge.plugin import events as ev

        dispatched: list[tuple] = []
        echoed: list[str] = []

        class Plugins:
            async def dispatch(self, event, *args):
                dispatched.append((event, args))

        class Loglens:
            def observe(self, info):
                pass

        class Process:
            is_startup_done = True

            def mark_startup_done(self):
                pass

        class Server:
            def __init__(self):
                self.handler = FactorioHandler()
                self.plugins = Plugins()
                self.loglens = Loglens()
                self.process = Process()

            def tr(self, key, **kwargs):
                return key

            def echo(self, text, info):
                echoed.append(text)

            def on_rcon_port_open(self):
                pass

            def on_save_completed(self):
                pass

            def schedule_startup_report(self):
                pass

        server = Server()
        return (
            InfoReactor(server, logging.getLogger("test")),
            server, dispatched, echoed, ev, asyncio,
        )

    async def react(self, wiring, line: str):
        reactor, server, dispatched, echoed, ev, _ = wiring
        info = server.handler.parse_server_stdout(line)
        await reactor.react(info)
        return dispatched, echoed, ev

    @pytest.mark.asyncio
    async def test_a_bridged_line_reaches_plugins_as_data(self, wiring):
        line = SENTINEL + json.dumps({"event": "on_research_finished", "name": "automation"})
        dispatched, _, ev = await self.react(wiring, line)
        assert (ev.LUA_EVENT, ({"event": "on_research_finished", "name": "automation"},)) \
            in dispatched

    @pytest.mark.asyncio
    async def test_it_never_reaches_the_console(self, wiring):
        """Otherwise every research completion puts JSON in the operator's log."""
        line = SENTINEL + json.dumps({"event": "on_research_finished", "name": "automation"})
        _, echoed, _ = await self.react(wiring, line)
        assert echoed == []

    @pytest.mark.asyncio
    async def test_an_ordinary_line_still_goes_through(self, wiring):
        dispatched, echoed, ev = await self.react(
            wiring, "   1.234 Hosting game at IP ADDR:({0.0.0.0:34197})"
        )
        assert echoed, "a normal line must still be echoed"
        assert not any(event is ev.LUA_EVENT for event, _ in dispatched)
