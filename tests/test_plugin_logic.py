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
    spec = importlib.util.spec_from_file_location(module_name, PLUGINS / f"{name}.py")
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
        assert "flib" in result.summary
        assert "!!mod remove flib" in result.fix
        assert "2.1" in result.detail

    def test_missing_dependency(self, doctor):
        result = doctor.diagnose(["Dependency flib >= 0.16 is not satisfied"])
        assert "dependency is missing" in result.summary
        assert "!!mod install" in result.fix

    def test_port_in_use(self, doctor):
        result = doctor.diagnose(["Error: bind: Address already in use"])
        assert "port is already taken" in result.summary

    def test_corrupt_save_points_at_rollback(self, doctor):
        result = doctor.diagnose(["Error Zip.cpp:100: the archive is corrupt"])
        assert "save file" in result.summary
        assert "!!save back" in result.fix

    def test_out_of_memory(self, doctor):
        result = doctor.diagnose(["terminate called after throwing std::bad_alloc"])
        assert "out of memory" in result.summary

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
        assert "krastorio2" in doctor.diagnose(lines).summary


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
