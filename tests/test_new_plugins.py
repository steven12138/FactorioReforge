"""Pure logic from the seven plugins added for monitoring and coordination.

No server: each of these is the decision the plugin exists to make -- when to
call a factory slow, when a vote is over, what counts as an attack -- and every
one of them is a rule that would be wrong in a way nobody notices until it
matters.
"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


def load(name: str):
    module_name = f"_test_plugin_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, PLUGINS / name / "__init__.py",
        submodule_search_locations=[str(PLUGINS / name)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# ups_watch
# ---------------------------------------------------------------------------

class TestUpsWatch:
    @pytest.fixture
    def ups(self):
        return load("ups_watch")

    def monitor(self, ups, **overrides):
        config = {"window": 2, "warn_below_ups": 55, "critical_below_ups": 45}
        config.update(overrides)
        return ups.Monitor(config)

    def test_a_full_rate_is_sixty_ticks_a_second(self, ups):
        monitor = self.monitor(ups)
        monitor.add(ups.Sample(at=0.0, tick=0, players=1))
        assert monitor.add(ups.Sample(at=1.0, tick=60, players=1)) == 60.0

    def test_a_paused_server_is_not_a_slow_one(self, ups):
        """Measured on 2.0.77: an idle auto-paused server reads 0.5 ticks/s.

        Reporting that as a collapse is how a watchdog trains its operator to
        ignore it, so a sample with nobody connected is not a sample.
        """
        monitor = self.monitor(ups)
        monitor.add(ups.Sample(at=0.0, tick=0, players=0))
        assert monitor.add(ups.Sample(at=2.0, tick=1, players=0)) is None
        assert monitor.rates == []

    def test_a_window_that_starts_paused_is_discarded(self, ups):
        monitor = self.monitor(ups)
        monitor.add(ups.Sample(at=0.0, tick=0, players=0))
        assert monitor.add(ups.Sample(at=1.0, tick=60, players=3)) is None

    def test_it_warns_once_and_only_on_the_way_down(self, ups):
        monitor = self.monitor(ups)
        for index in range(4):
            monitor.add(ups.Sample(at=index, tick=index * 50, players=1))
        assert monitor.verdict() == ("warn", 50.0)
        assert monitor.verdict() is None, "a base sitting at 50 is not news twice"

    def test_recovery_is_announced(self, ups):
        monitor = self.monitor(ups)
        for index in range(4):
            monitor.add(ups.Sample(at=index, tick=index * 50, players=1))
        assert monitor.verdict()[0] == "warn"
        for index in range(4, 8):
            monitor.add(ups.Sample(at=index, tick=monitor.samples[-1].tick + 60, players=1))
        assert monitor.verdict() == ("", 60.0)

    def test_one_slow_sample_is_not_a_trend(self, ups):
        """An autosave or a chunk-generation burst dips exactly one sample.

        The window is judged by its median for this reason: the mean of
        60, 30, 60 is 50, which would be announced as a slow factory.
        """
        monitor = self.monitor(ups, window=3)
        for index, tick in enumerate([0, 60, 90, 150, 210]):
            monitor.add(ups.Sample(at=index, tick=tick, players=1))
        assert monitor.average == 60.0
        assert monitor.verdict() is None

    def test_describe_leads_with_the_percentage_people_compare(self, ups):
        assert ups.describe(52.35) == "52.4 UPS (87%)"


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------

class TestAlerts:
    @pytest.fixture
    def alerts(self):
        return load("alerts")

    def test_a_wall_going_down_is_a_loss(self, alerts):
        before = {"wall": 400, "ammo-turret": 30}
        after = {"wall": 360, "ammo-turret": 24}
        assert alerts.losses(before, after, 5) == {"wall": 40, "ammo-turret": 6}

    def test_a_player_dismantling_one_thing_is_not_an_attack(self, alerts):
        assert alerts.losses({"wall": 400}, {"wall": 399}, 5) == {}

    def test_growth_is_never_a_loss(self, alerts):
        assert alerts.losses({"wall": 100}, {"wall": 200}, 1) == {}

    def test_the_first_poll_has_nothing_to_compare(self, alerts):
        assert alerts.losses({}, {"wall": 100}, 1) == {}

    def test_alerts_are_deduplicated_by_chunk(self, alerts):
        """The same turret reports at slightly different coordinates."""
        seen = set()
        first = [{"type": "no_ammo", "position": {"x": 100, "y": 100}}]
        second = [{"type": "no_ammo", "position": {"x": 101, "y": 103}}]
        assert len(alerts.new_alerts(first, seen, [])) == 1
        assert alerts.new_alerts(second, seen, []) == []

    def test_a_different_place_is_a_different_alert(self, alerts):
        seen = set()
        alerts.new_alerts([{"type": "no_ammo", "position": {"x": 0, "y": 0}}], seen, [])
        assert len(alerts.new_alerts(
            [{"type": "no_ammo", "position": {"x": 500, "y": 500}}], seen, [])) == 1

    def test_noisy_alert_types_are_dropped(self, alerts):
        """These fire constantly on a working base with construction robots."""
        seen = set()
        raw = [{"type": "no_material_for_construction", "position": {"x": 0, "y": 0}}]
        assert alerts.new_alerts(raw, seen, ["no_material_for_construction"]) == []


# ---------------------------------------------------------------------------
# trains
# ---------------------------------------------------------------------------

class TestTrains:
    @pytest.fixture
    def trains(self):
        return load("trains")

    def test_no_path_is_wrong_immediately(self, trains):
        broken, stalled = trains.classify(
            [{"id": 1, "state": 2}], {}, now=0.0, stuck_after=600
        )
        assert [t["id"] for t in broken] == [1]
        assert stalled == []

    def test_waiting_at_a_signal_is_normal_until_it_is_not(self, trains):
        since: dict = {}
        waiting = [{"id": 7, "state": 4}]
        broken, stalled = trains.classify(waiting, since, now=0.0, stuck_after=600)
        assert (broken, stalled) == ([], [])

        broken, stalled = trains.classify(waiting, since, now=700.0, stuck_after=600)
        assert [t["id"] for t in stalled] == [7]
        assert stalled[0]["waited"] == 700.0

    def test_changing_state_resets_the_clock(self, trains):
        """A train that moved is not stuck, even if it stops again."""
        since: dict = {}
        trains.classify([{"id": 7, "state": 4}], since, now=0.0, stuck_after=600)
        trains.classify([{"id": 7, "state": 0}], since, now=500.0, stuck_after=600)
        _, stalled = trains.classify([{"id": 7, "state": 4}], since, now=700.0, stuck_after=600)
        assert stalled == []

    def test_a_train_that_vanished_is_forgotten(self, trains):
        since: dict = {}
        trains.classify([{"id": 7, "state": 4}], since, now=0.0, stuck_after=600)
        trains.classify([], since, now=10.0, stuck_after=600)
        assert since == {}

    def test_a_moving_train_is_never_reported(self, trains):
        broken, stalled = trains.classify(
            [{"id": 1, "state": 0}], {}, now=9999.0, stuck_after=1
        )
        assert (broken, stalled) == ([], [])

    def test_the_state_number_is_named(self, trains):
        assert trains.STATE_NAMES[2] == "no_path"
        described = trains.describe({"id": 3, "station": "iron", "state_name": "no_path"})
        assert described == "#3 iron no_path"


# ---------------------------------------------------------------------------
# power
# ---------------------------------------------------------------------------

class TestPower:
    @pytest.fixture
    def power(self):
        return load("power")

    def test_crossing_down_reports_once(self, power):
        below = set()
        assert power.crossings(0.25, [0.3, 0.1], below) == [(0.3, True)]
        assert power.crossings(0.24, [0.3, 0.1], below) == []

    def test_crossing_back_up_reports_recovery(self, power):
        below = {0.3}
        assert power.crossings(0.5, [0.3, 0.1], below) == [(0.3, False)]
        assert below == set()

    def test_falling_past_two_thresholds_reports_both(self, power):
        below = set()
        events = power.crossings(0.05, [0.3, 0.1], below)
        assert events == [(0.3, True), (0.1, True)]

    def test_the_bar_is_readable_at_the_extremes(self, power):
        assert power.charge_bar(0.0) == "[----------]"
        assert power.charge_bar(1.0) == "[##########]"
        assert power.charge_bar(0.5) == "[#####-----]"

    @pytest.mark.parametrize("watts,expected", [
        (1_500_000_000, "1.5 GW"), (2_400_000, "2.4 MW"), (900, "900 W"),
    ])
    def test_watts_are_scaled(self, power, watts, expected):
        assert power.format_watts(watts) == expected


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------

class TestResearch:
    @pytest.fixture
    def research(self):
        return load("research")

    def test_names_are_forgiving(self, research):
        assert research.normalise("  Logistics 3 ") == "logistics-3"

    def test_an_eta_needs_something_to_divide_by(self, research):
        """An infinite ETA is worse than none: it still looks like a number."""
        assert research.format_eta(1000, 0) is None
        assert research.format_eta(0, 10) is None

    @pytest.mark.parametrize("remaining,rate,expected", [
        (100, 10, "10s"), (600, 1, "10m00s"), (7200, 1, "2h00m"),
    ])
    def test_eta_formats(self, research, remaining, rate, expected):
        assert research.format_eta(remaining, rate) == expected


# ---------------------------------------------------------------------------
# vote
# ---------------------------------------------------------------------------

class TestVote:
    @pytest.fixture
    def vote(self):
        return load("vote")

    def poll(self, vote, voters, majority=0.5):
        return vote.Poll(
            question="restart?", started_by="alice",
            eligible=set(voters), ends_at=time.monotonic() + 60, majority=majority,
        )

    def test_a_majority_of_two_is_two(self, vote):
        """Strictly more than half: one of two is a tie, not a mandate."""
        poll = self.poll(vote, ["alice", "bob"])
        assert poll.needed == 2

    def test_a_majority_of_five_is_three(self, vote):
        assert self.poll(vote, list("abcde")).needed == 3

    def test_only_players_who_were_there_may_vote(self, vote):
        poll = self.poll(vote, ["alice", "bob"])
        assert poll.cast("alice", True) is True
        assert poll.cast("latecomer", True) is False
        assert poll.yes == 1

    def test_it_ends_as_soon_as_the_rest_cannot_change_it(self, vote):
        poll = self.poll(vote, list("abcde"))
        for name in "abc":
            poll.cast(name, True)
        assert poll.decided() is True

    def test_it_fails_early_when_yes_becomes_unreachable(self, vote):
        poll = self.poll(vote, list("abcde"))
        for name in "abc":
            poll.cast(name, False)
        assert poll.decided() is False

    def test_it_waits_while_the_outcome_is_open(self, vote):
        poll = self.poll(vote, list("abcde"))
        poll.cast("a", True)
        assert poll.decided() is None

    def test_silence_counts_as_no(self, vote):
        poll = self.poll(vote, list("abcde"))
        poll.cast("a", True)
        poll.cast("b", True)
        assert poll.result() is False

    def test_changing_your_mind_replaces_your_vote(self, vote):
        poll = self.poll(vote, ["alice", "bob"])
        poll.cast("alice", True)
        poll.cast("alice", False)
        assert (poll.yes, poll.no) == (0, 1)


# ---------------------------------------------------------------------------
# mail
# ---------------------------------------------------------------------------

class TestMail:
    @pytest.fixture
    def mail(self):
        return load("mail")

    def test_mailboxes_ignore_capitalisation(self, mail):
        box: dict = {}
        mail.deliver_to(box, "Alice", {"text": "hi"}, 10)
        assert len(mail.take(box, "alice")) == 1

    def test_reading_empties_the_box(self, mail):
        box: dict = {}
        mail.deliver_to(box, "alice", {"text": "hi"}, 10)
        mail.take(box, "alice")
        assert mail.take(box, "alice") == []

    def test_the_oldest_is_dropped_not_the_newest(self, mail):
        """A full mailbox is usually somebody who has not logged in for a month."""
        box: dict = {}
        for index in range(5):
            mail.deliver_to(box, "alice", {"text": str(index)}, 3)
        assert [m["text"] for m in mail.take(box, "alice")] == ["2", "3", "4"]

    @pytest.mark.parametrize("seconds,expected", [
        (90, "1m"), (3600 * 5 + 120, "5h02m"), (86400 * 2 + 3600, "2d01h"),
    ])
    def test_ages_read_naturally(self, mail, seconds, expected):
        assert mail.format_age(seconds) == expected


# ---------------------------------------------------------------------------
# progress reporting, shared
# ---------------------------------------------------------------------------

class Clock:
    """A hand-wound clock, so the rate limiting is tested rather than waited on."""

    def __init__(self, at: float = 0.0):
        self.at = at

    def __call__(self) -> float:
        return self.at


class TestProgress:
    @pytest.fixture
    def Progress(self):
        from factorio_reforge.core.progress import Progress
        return Progress

    def test_a_fast_operation_says_nothing(self, Progress):
        """Most finish before anyone would have wondered."""
        lines = []
        clock = Clock()
        bar = Progress(lines.append, total=100, now=clock)
        clock.at = 0.5
        bar.update(50)
        clock.at = 1.0
        bar.update(100)
        assert lines == []

    def test_a_slow_one_reports(self, Progress):
        lines = []
        clock = Clock()
        bar = Progress(lines.append, total=100, unit="mods", now=clock)
        clock.at = 5.0
        bar.update(40)
        clock.at = 6.0
        bar.update(50)   # too soon after the last line
        clock.at = 9.0
        bar.update(90)
        assert len(lines) == 2
        assert lines[0] == "[####------]  40%  40 / 100 mods"

    def test_without_a_total_it_counts_rather_than_faking_a_bar(self, Progress):
        lines = []
        clock = Clock()
        bar = Progress(lines.append, unit="KiB", now=clock)
        clock.at = 5.0
        bar.update(1234)
        assert lines == ["[----------] 1,234 KiB (5s)"]

    def test_the_closing_line_is_skipped_when_nothing_was_said(self, Progress):
        lines = []
        bar = Progress(lines.append, total=10, now=lambda: 0.0)
        bar.update(10)
        bar.done("finished")
        assert lines == []
