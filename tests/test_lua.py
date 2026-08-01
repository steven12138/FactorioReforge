"""Lua source generation and reply parsing.

Pure functions, so these run without a server. The values they produce were
checked against a live 2.0.77 instance -- see docs/M0-findings.md.
"""

import json

import pytest

from factorio_reforge.core import lua
from factorio_reforge.core.lua import LuaError


class TestLuaString:
    def test_plain_text(self):
        assert lua.lua_string("hello") == '"hello"'

    @pytest.mark.parametrize(
        "value, expected",
        [
            ('say "hi"', '"say \\"hi\\""'),
            ("back\\slash", '"back\\\\slash"'),
            ("two\nlines", '"two\\nlines"'),
        ],
    )
    def test_escapes(self, value, expected):
        assert lua.lua_string(value) == expected

    def test_non_ascii_uses_decimal_escapes_not_backslash_u(self):
        """Factorio runs Lua 5.2, which has no \\u escape -- json.dumps would break."""
        source = lua.lua_string("玩家")
        assert "\\u" not in source
        assert source.startswith('"\\') and source.endswith('"')
        # Decimal escapes of the UTF-8 bytes.
        expected = "".join(f"\\{b}" for b in "玩家".encode())
        assert source == f'"{expected}"'

    def test_a_name_cannot_break_out_of_the_string(self):
        """A player called `"); evil()` must not become executable Lua."""
        source = lua.lua_string('"); game.print("pwned"); ("')
        assert source.count('"') == 2 + source.count('\\"')
        assert lua.json_query(f"({source})").count('rcon.print') == 2

    def test_none_becomes_an_empty_string(self):
        assert lua.lua_string(None) == '""'


class TestLuaValue:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, "nil"),
            (True, "true"),
            (False, "false"),
            (5, "5"),
            ("x", '"x"'),
            ([1, 2], "{1,2}"),
        ],
    )
    def test_scalars_and_lists(self, value, expected):
        assert lua.lua_value(value) == expected

    def test_dict_keys_that_are_identifiers_are_bare(self):
        assert lua.lua_value({"x": 1, "y": 2}) == "{x=1,y=2}"

    def test_dict_keys_that_are_not_identifiers_are_bracketed(self):
        assert lua.lua_value({"a b": 1}) == '{["a b"]=1}'

    def test_nested_structures(self):
        source = lua.lua_value({"position": {"x": 1, "y": 2}, "text": "hi"})
        assert source == '{position={x=1,y=2},text="hi"}'

    def test_an_unsupported_type_is_refused(self):
        with pytest.raises(TypeError):
            lua.lua_value(object())


class TestParseJsonResult:
    def test_success(self):
        raw = json.dumps({"ok": True, "value": {"a": 1}})
        assert lua.parse_json_result(raw) == {"a": 1}

    def test_a_lua_error_becomes_a_python_exception(self):
        raw = json.dumps({"ok": False, "error": "attempt to index a nil value"})
        with pytest.raises(LuaError, match="nil value"):
            lua.parse_json_result(raw)

    def test_factorios_own_refusal_is_surfaced(self):
        with pytest.raises(LuaError, match="Cannot execute command"):
            lua.parse_json_result("Cannot execute command. Error: no such key.")

    def test_an_empty_reply_hints_at_the_likely_cause(self):
        with pytest.raises(LuaError, match="allow_commands"):
            lua.parse_json_result("")

    def test_non_json_is_reported_with_the_text(self):
        with pytest.raises(LuaError, match="expected JSON"):
            lua.parse_json_result("Players (0):")

    def test_a_reply_missing_the_ok_field_is_rejected(self):
        with pytest.raises(LuaError, match="unexpected reply shape"):
            lua.parse_json_result('{"value": 1}')

    def test_a_null_value_round_trips(self):
        assert lua.parse_json_result(json.dumps({"ok": True, "value": None})) is None


class TestSnippets:
    def test_json_query_wraps_in_pcall_so_errors_are_caught(self):
        source = lua.json_query("game.tick")
        assert "pcall" in source
        assert "helpers.table_to_json" in source
        # game.table_to_json was removed in 2.0; using it would break every query.
        assert "game.table_to_json" not in source

    @pytest.mark.parametrize(
        "source",
        [
            lua.online_players(),
            lua.all_players(),
            lua.player_info("alice"),
            lua.server_stats(),
            lua.entity_count("stone-furnace"),
        ],
    )
    def test_snippets_are_expressions_usable_inside_json_query(self, source):
        wrapped = lua.json_query(source)
        assert wrapped.startswith("local ok, result = pcall(function() return ")

    def test_stats_uses_the_2_0_evolution_signature(self):
        """get_evolution_factor takes a surface since 2.0; the 1.1 form errors."""
        assert "get_evolution_factor(s)" in lua.server_stats()

    def test_stats_uses_the_2_0_production_statistics_spelling(self):
        assert "get_item_production_statistics" in lua.item_produced("iron-plate")

    def test_player_names_are_escaped_inside_snippets(self):
        source = lua.player_info('bad") or game.print("x')
        assert 'game.get_player("bad\\")' in source

    def test_map_marker_carries_position_text_and_icon(self):
        source = lua.add_map_marker(
            "nauvis", {"x": 10, "y": -5}, "alice", {"type": "virtual", "name": "signal-info"}
        )
        assert "add_chart_tag" in source
        assert "position={x=10,y=-5}" in source
        assert 'text="alice"' in source
        assert 'icon={type="virtual",name="signal-info"}' in source

    def test_map_marker_without_an_icon_omits_the_key(self):
        assert "icon" not in lua.add_map_marker("nauvis", {"x": 0, "y": 0}, "t")

    def test_teleport_to_the_same_surface_passes_nil(self):
        assert "local surf = nil" in lua.teleport("alice", {"x": 1, "y": 2})

    def test_teleport_across_surfaces_resolves_the_surface(self):
        source = lua.teleport("alice", {"x": 1, "y": 2}, "vulcanus")
        assert 'game.get_surface("vulcanus")' in source


def test_both_failure_kinds_share_one_base_for_plugin_authors():
    """Plugins catch QueryError once instead of remembering a tuple."""
    from factorio_reforge.core.errors import QueryError
    from factorio_reforge.core.rcon import RconError

    assert issubclass(LuaError, QueryError)
    assert issubclass(RconError, QueryError)
