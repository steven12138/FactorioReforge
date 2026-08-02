"""The startup report.

Factorio's own lines are left verbatim -- these tests are about what
FactorioReforge notices while reading them, and says afterwards.
"""

import pytest

from factorio_reforge.core.handler import FactorioHandler
from factorio_reforge.core.loglens import LogLens, Severity


@pytest.fixture
def parse():
    return FactorioHandler().parse_server_stdout


@pytest.fixture
def lens():
    return LogLens()


def feed(lens, parse, *lines):
    for line in lines:
        lens.observe(parse(line))
    return lens


class TestRoutineNotices:
    """The "not found" lines that make every new operator stop and check."""

    def test_blueprint_storage_fallback_is_reported_as_routine(self, lens, parse):
        feed(lens, parse,
             '   0.537 Blueprint storage "blueprint-storage-2.dat" was not found, '
             'trying to load previous version storage "blueprint-storage.dat"')
        assert [o.key for o in lens.by_severity(Severity.ROUTINE)] == ["blueprint_storage"]

    def test_missing_cloud_player_data_is_routine(self, lens, parse):
        feed(lens, parse, "   0.514 Info PlayerData.cpp:71: Cloud player-data.json unavailable")
        assert lens.by_severity(Severity.ROUTINE)[0].key == "cloud_player_data"

    def test_audio_disabled_is_routine(self, lens, parse):
        feed(lens, parse, "   0.014 Audio is disabled")
        assert lens.by_severity(Severity.ROUTINE)[0].key == "audio_disabled"


class TestProblems:
    def test_a_failed_mod_is_a_problem_and_names_it(self, lens, parse):
        feed(lens, parse, '   0.015 Error Util.cpp:81: Failed to load mod "flib": ')
        problem = lens.by_severity(Severity.PROBLEM)[0]
        assert problem.key == "mod_failed"
        assert problem.values == {"mod": "flib"}

    def test_a_version_mismatch_carries_both_versions(self, lens, parse):
        feed(lens, parse,
             "    • Incompatible Factorio version (current: 2.0, required: 2.1)")
        problem = lens.by_severity(Severity.PROBLEM)[0]
        assert problem.values == {"have": "2.0", "need": "2.1"}

    def test_the_lock_failure_is_a_problem(self, lens, parse):
        feed(lens, parse, "   0.015 Error Util.cpp:81: Couldn't acquire exclusive lock for x")
        assert lens.by_severity(Severity.PROBLEM)[0].key == "locked"

    def test_a_desync_is_a_problem(self, lens, parse):
        feed(lens, parse, "   9.000 Info Foo.cpp:1: Desync detected for player Alice")
        assert lens.by_severity(Severity.PROBLEM)[0].key == "desync"


class TestNotices:
    def test_the_listening_address_is_surfaced(self, lens, parse):
        feed(lens, parse, "   0.539 Hosting game at IP ADDR:({0.0.0.0:34197})")
        notice = lens.by_severity(Severity.NOTICE)[0]
        assert notice.key == "hosting"
        assert notice.values == {"address": "0.0.0.0:34197"}

    def test_the_rcon_bind_is_surfaced_so_it_can_be_checked(self, lens, parse):
        feed(lens, parse,
             "   0.539 Info RemoteCommandProcessor.cpp:126: "
             "Starting RCON interface at IP ADDR:({0.0.0.0:27015})")
        assert lens.by_severity(Severity.NOTICE)[0].values == {"address": "0.0.0.0:27015"}

    def test_bundled_mods_are_not_listed_as_loaded(self, lens, parse):
        """base and space-age load every time; saying so is noise."""
        feed(lens, parse,
             "   0.031 Loading mod base 2.0.77 (data.lua)",
             "   0.115 Loading mod space-age 2.0.77 (data.lua)")
        assert lens.by_severity(Severity.NOTICE) == []

    def test_a_real_mod_is_listed(self, lens, parse):
        feed(lens, parse, "   0.140 Loading mod flib 0.16.5 (data.lua)")
        notice = lens.by_severity(Severity.NOTICE)[0]
        assert notice.values == {"mod": "flib", "version": "0.16.5"}


class TestReporting:
    def test_a_clean_start_says_nothing(self, lens, parse):
        feed(lens, parse, "   0.271 Info ModManager.cpp:449: FeatureFlag quality = true")
        assert lens.summary(lambda key, **kw: key) is None
        assert lens.report(lambda key, **kw: key) == []

    def test_problems_come_first(self, lens, parse):
        feed(lens, parse,
             "   0.014 Audio is disabled",
             "   0.539 Hosting game at IP ADDR:({0.0.0.0:34197})",
             '   0.015 Error Util.cpp:81: Failed to load mod "flib": ')
        severities = [severity for severity, _ in lens.report(lambda k, **kw: k)]
        assert severities == [Severity.PROBLEM, Severity.NOTICE, Severity.ROUTINE]

    def test_a_repeated_line_is_reported_once(self, lens, parse):
        """A message on every tick would otherwise bury the rest of the report."""
        for _ in range(20):
            lens.observe(parse("   1.0 Info Foo.cpp:1: Desync detected"))
        assert len(lens.by_severity(Severity.PROBLEM)) == 1

    def test_reset_clears_it_for_the_next_start(self, lens, parse):
        feed(lens, parse, "   0.014 Audio is disabled")
        lens.reset()
        assert lens.observations == []

    def test_console_input_is_ignored(self, lens):
        info = FactorioHandler().parse_console_input("Audio is disabled")
        lens.observe(info)
        assert lens.observations == []


class TestFactorioOutputIsUntouched:
    """The whole point: the game's lines are not reworded or re-levelled."""

    def test_observing_does_not_modify_the_info(self, lens, parse):
        info = parse("   0.014 Audio is disabled")
        before = (info.raw_content, info.content, info.level, info.kind)
        lens.observe(info)
        assert (info.raw_content, info.content, info.level, info.kind) == before
