"""Pure logic from the bundled plugins, tested without a server.

Plugins are loaded by path, the way the plugin manager does it, so these run
against the real files rather than a copy.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


def load(name: str):
    """Import a plugin file directly, as PluginManager would."""
    module_name = f"_test_plugin_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, PLUGINS / name / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# crash_doctor
# ---------------------------------------------------------------------------

class TestCrashDiagnosis:
    @pytest.fixture
    def doctor(self):
        return load("crash_doctor")

    def test_incompatible_mod_is_recognised_with_a_fix(self, doctor):
        """The exact failure that took the server down during development."""
        lines = [
            "   0.015 Error Util.cpp:81: Failed to load mod \"flib\": ",
            "• flib",
            "    • Incompatible Factorio version (current: 2.0, required: 2.1)",
            "    • Dependency base >= 2.1.0 is not satisfied (active: base 2.0.77)",
        ]
        result = doctor.diagnose(lines)
        assert result is not None
        assert result.key == "mod_load_failed"
        assert result.values == {"mod": "flib"}
        assert "2.1" in result.detail

    def test_missing_dependency(self, doctor):
        result = doctor.diagnose(["Dependency flib >= 0.16 is not satisfied"])
        assert result.key == "missing_dependency"
        assert result.values == {"dep": "flib >= 0.16"}

    def test_port_in_use(self, doctor):
        result = doctor.diagnose(["Error: bind: Address already in use"])
        assert result.key == "port_in_use"

    def test_corrupt_save_points_at_rollback(self, doctor):
        result = doctor.diagnose(["Error Zip.cpp:100: the archive is corrupt"])
        assert result.key == "corrupt_save"

    def test_out_of_memory(self, doctor):
        result = doctor.diagnose(["terminate called after throwing std::bad_alloc"])
        assert result.key == "out_of_memory"

    def test_unknown_output_returns_none_rather_than_guessing(self, doctor):
        assert doctor.diagnose(["   0.1 Info Everything is fine"]) is None

    def test_empty_buffer_is_safe(self, doctor):
        assert doctor.diagnose([]) is None

    def test_the_newest_failure_wins_over_an_older_one(self, doctor):
        """A stale error from a previous start must not shadow the fresh one."""
        lines = [
            "Error: bind: Address already in use",
            "   0.1 Info restarted",
            'Error Util.cpp:81: Failed to load mod "krastorio2": ',
        ]
        assert doctor.diagnose(lines).values == {"mod": "krastorio2"}


# ---------------------------------------------------------------------------
# production
# ---------------------------------------------------------------------------

class TestSparkline:
    @pytest.fixture
    def production(self):
        return load("production")

    def test_empty_series(self, production):
        assert production.sparkline([]) == ""

    def test_rising_series_ends_higher_than_it_starts(self, production):
        spark = production.sparkline([0, 1, 2, 3, 4, 5])
        assert len(spark) == 6
        assert production.SPARK.index(spark[-1]) > production.SPARK.index(spark[0])

    def test_a_flat_nonzero_series_is_drawn_flat_not_scaled_into_noise(self, production):
        spark = production.sparkline([100, 100, 100])
        assert len(set(spark)) == 1

    def test_an_all_zero_series_uses_the_lowest_block(self, production):
        assert production.sparkline([0, 0, 0]) == production.SPARK[0] * 3

    def test_only_the_last_width_samples_are_drawn(self, production):
        assert len(production.sparkline(list(range(100)), width=10)) == 10


class TestSvgChart:
    @pytest.fixture
    def production(self):
        return load("production")

    def test_no_data_still_produces_valid_svg(self, production):
        svg = production.svg_chart("iron-plate", [])
        assert svg.startswith("<svg") and svg.endswith("</svg>")

    def test_chart_contains_a_polyline_and_the_peak(self, production):
        series = [[0, 10, 0], [1, 50, 0], [2, 30, 0]]
        svg = production.svg_chart("iron-plate", series)
        assert "<polyline" in svg
        assert "iron-plate" in svg
        assert "peak 50" in svg

    def test_a_flat_zero_series_does_not_divide_by_zero(self, production):
        svg = production.svg_chart("x", [[0, 0, 0], [1, 0, 0]])
        assert "<polyline" in svg


# ---------------------------------------------------------------------------
# world_watch
# ---------------------------------------------------------------------------

class TestThresholdCrossings:
    @pytest.fixture
    def watch(self):
        return load("world_watch")

    def test_only_thresholds_newly_passed_are_returned(self, watch):
        assert watch._crossings([0.25, 0.5, 0.75], 0.3, 0.8) == [0.5, 0.75]

    def test_nothing_fires_when_the_value_has_not_moved_past_one(self, watch):
        assert watch._crossings([0.25, 0.5], 0.3, 0.4) == []

    def test_a_threshold_already_passed_does_not_fire_again(self, watch):
        """Evolution sitting at 51% is not news every single poll."""
        assert watch._crossings([0.5], 0.51, 0.52) == []

    def test_a_jump_past_several_thresholds_reports_all_of_them(self, watch):
        assert watch._crossings([0.25, 0.5, 0.75, 0.9], 0.0, 0.95) == [0.25, 0.5, 0.75, 0.9]

    def test_landing_exactly_on_a_threshold_counts(self, watch):
        assert watch._crossings([0.5], 0.4, 0.5) == [0.5]


# ---------------------------------------------------------------------------
# shared duration formatting
# ---------------------------------------------------------------------------

class TestDurations:
    @pytest.mark.parametrize(
        "ticks, expected",
        [
            (0, "0m"),
            (3600, "1m"),        # 60 ticks/second
            (3600 * 90, "1h30m"),
            (3600 * 60 * 25, "1d01h"),
        ],
    )
    def test_ticks_render_as_human_time(self, ticks, expected):
        assert load("leaderboard")._ticks_to_text(ticks) == expected


class TestLockFileDiagnosis:
    """A second FactorioReforge on the same install fails on the lock, not the port."""

    def test_another_instance_is_recognised(self):
        doctor = load("crash_doctor")
        result = doctor.diagnose([
            "/srv/factorio/.lock: Resource temporarily unavailable.",
            "Is another instance already running?",
        ])
        assert result is not None
        assert result.key == "another_instance"


class TestNoCheatCommands:
    """FactorioReforge must never issue /c, which permanently flags a save.

    Everything it runs goes through /sc (silent-command). This is a guarantee
    worth asserting rather than remembering: one /c anywhere in the tree marks
    the world for good.
    """

    ROOTS = [
        Path(__file__).resolve().parent.parent / "factorio_reforge",
        Path(__file__).resolve().parent.parent / "plugins",
    ]

    def test_no_source_file_issues_a_cheat_command(self):
        import re

        # "/c " or "/command " as a command being sent, not as prose.
        pattern = re.compile(r'["\']/(?:c|command)\s')
        offenders = []
        for root in self.ROOTS:
            for path in root.rglob("*.py"):
                if pattern.search(path.read_text(encoding="utf-8")):
                    offenders.append(str(path))
        assert not offenders, f"these issue /c: {offenders}"

    def test_the_lua_layer_uses_silent_command(self):
        from factorio_reforge.core import lua

        assert lua.json_query("game.tick")
        # The interface is what actually prefixes it; assert the contract here.
        import inspect

        from factorio_reforge.plugin.interface import ServerInterface

        source = inspect.getsource(ServerInterface.lua)
        assert "/sc " in source
        assert "/c " not in source


class TestProgressWithNothingToCount:
    """Waiting on a round trip rather than on bytes.

    ``!!version check`` is twelve kilobytes over five seconds: there is no
    byte count worth a bar, and rendering "0" would read as no progress rather
    than as no counter.
    """

    def bar(self, clock):
        from factorio_reforge.core.progress import Progress

        lines = []
        return Progress(lines.append, quiet_for=1.0, interval=1.0, now=clock), lines

    def test_elapsed_alone_when_there_is_nothing_to_count(self):
        now = [0.0]
        bar, lines = self.bar(lambda: now[0])
        now[0] = 3.0
        bar.update(0)
        assert lines == ["[----------] 3s"]

    def test_a_counter_still_wins_when_there_is_one(self):
        now = [0.0]
        bar, lines = self.bar(lambda: now[0])
        now[0] = 3.0
        bar.update(7)
        assert "7" in lines[0]

    def test_a_quick_answer_says_nothing_at_all(self):
        now = [0.0]
        bar, lines = self.bar(lambda: now[0])
        now[0] = 0.5
        bar.update(0)
        assert lines == []
