"""Let the server decide things it should not decide alone.

Restarting, restoring a backup and turning the lights off are all things one
admin can do and several players have to live with. A vote is the cheap way to
make that a group decision without an admin having to be awake.

The counting rules are the whole plugin, and they are the part with opinions:

* **only players online when the vote started may vote.** Someone who joins
  midway did not hear the question, and letting them in lets a vote be won by
  inviting friends.
* **a vote can end early**, as soon as the remaining votes cannot change the
  outcome. Waiting out a timer whose result is already decided is how people
  learn to ignore votes.
* **abstention is a no.** A quorum expressed as "most of the people here" only
  means something if silence counts as not agreeing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time

from factorio_reforge.command.builder import GreedyText, Literal
from factorio_reforge.core.errors import QueryError
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "vote",
    "version": "1.0.0",
    "name": "Vote",
    "description": "Put a question to the players, with a timer and a quorum",
    "author": "FactorioReforge",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

DEFAULT_CONFIG = {
    #: Who may start one. Voting itself is open to everyone online.
    "start_permission": "user",
    "duration_seconds": 120,
    #: Fraction of eligible voters that must say yes for it to pass.
    "majority": 0.5,
    #: Below this many players online, a vote is meaningless -- one person
    #: agreeing with themselves is not consent.
    "minimum_voters": 2,
    "announce_every_vote": True,
}

_state: dict = {}


def on_load(server, prev):
    config = server.load_config_simple("config.json", DEFAULT_CONFIG)
    _state.clear()
    _state.update(config=config, poll=None, task=None)

    start = _parse_level(config.get("start_permission", "user"), server)
    server.register_command(
        Literal("!!vote")
        .requires(PermissionLevel.USER)
        .runs(_cmd_status)
        .then(Literal("yes").runs(_cmd_yes))
        .then(Literal("no").runs(_cmd_no))
        .then(Literal("cancel").requires(PermissionLevel.ADMIN).runs(_cmd_cancel))
        .then(Literal("start").requires(start)
              .then(GreedyText("question").runs(_cmd_start)))
    )
    server.register_help_message(
        "!!vote", server.tr("help"), PermissionLevel.USER,
        detail=(server.tr("detail.start"), server.tr("detail.rules")),
    )


async def on_unload(server):
    task = _state.get("task")
    if task is not None:
        task.cancel()
    _state.clear()


def _parse_level(value, server) -> PermissionLevel:
    try:
        return PermissionLevel.parse(value)
    except ValueError:
        server.logger.warning("start_permission %r is not a level; using user", value)
        return PermissionLevel.USER


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Poll:
    question: str
    started_by: str
    eligible: set[str]
    """Who was online when it started. Latecomers did not hear the question."""

    ends_at: float
    majority: float = 0.5
    votes: dict[str, bool] = dataclasses.field(default_factory=dict)

    @property
    def needed(self) -> int:
        """Yes votes required. Strictly more than the fraction, so 0.5 of 2 is 2."""
        import math
        return max(1, math.floor(len(self.eligible) * self.majority) + 1)

    @property
    def yes(self) -> int:
        return sum(1 for value in self.votes.values() if value)

    @property
    def no(self) -> int:
        return sum(1 for value in self.votes.values() if not value)

    @property
    def outstanding(self) -> int:
        return len(self.eligible) - len(self.votes)

    def cast(self, player: str, value: bool) -> bool:
        """Record a vote. False means this player was not eligible."""
        if player not in self.eligible:
            return False
        self.votes[player] = value
        return True

    def decided(self) -> bool | None:
        """The result, if it can no longer change. None means keep waiting.

        Ending early is not a nicety: a vote whose outcome is settled but whose
        timer has two minutes left teaches everyone that votes are theatre.
        """
        if self.yes >= self.needed:
            return True
        if self.yes + self.outstanding < self.needed:
            return False
        return None

    def result(self) -> bool:
        """The outcome at expiry: silence counts as no."""
        return self.yes >= self.needed

    def describe(self) -> str:
        return f"{self.yes}/{self.needed} yes, {self.no} no, {self.outstanding} silent"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def _cmd_start(source, ctx):
    server = source.server
    config = _state["config"]
    if _state.get("poll") is not None:
        await source.reply(server.tr("error.already_running"))
        return

    try:
        online = await server.get_online_players()
    except QueryError as exc:
        await source.reply(server.tr("error.no_players", error=exc))
        return

    minimum = int(config.get("minimum_voters", 2))
    if len(online) < minimum:
        await source.reply(server.tr("error.too_few", online=len(online), needed=minimum))
        return

    duration = max(15, int(config.get("duration_seconds", 120)))
    poll = Poll(
        question=ctx["question"].strip(),
        started_by=str(source.player or server.tr("common.console")),
        eligible=set(online),
        ends_at=time.monotonic() + duration,
        majority=float(config.get("majority", 0.5)),
    )
    _state["poll"] = poll

    await _announce(server, server.tr(
        "start.announced", who=poll.started_by, question=poll.question,
        seconds=duration, needed=poll.needed, voters=len(poll.eligible),
    ))
    # The starter is not counted as voting yes: starting a vote is asking a
    # question, and assuming the answer is how a vote becomes a formality.
    _state["task"] = asyncio.create_task(_countdown(server, poll, duration))


async def _cmd_yes(source):
    await _record(source, True)


async def _cmd_no(source):
    await _record(source, False)


async def _record(source, value: bool):
    server = source.server
    poll: Poll | None = _state.get("poll")
    if poll is None:
        await source.reply(server.tr("error.nothing_running"))
        return
    if source.player is None:
        await source.reply(server.tr("error.console_cannot_vote"))
        return
    if not poll.cast(source.player, value):
        await source.reply(server.tr("error.not_eligible"))
        return

    # Not `vote.yes` / `vote.no`: YAML reads those keys as booleans, so the
    # catalogue would store them as `vote.True` and every lookup would print
    # the key. A test enforces this.
    await source.reply(server.tr("vote.counted", vote=server.tr(
        "vote.approve" if value else "vote.reject")))
    if _state["config"].get("announce_every_vote", True):
        await _announce(server, server.tr("vote.tally", tally=poll.describe()))

    decided = poll.decided()
    if decided is not None:
        await _finish(server, poll, decided, early=True)


async def _cmd_status(source):
    server = source.server
    poll: Poll | None = _state.get("poll")
    if poll is None:
        await source.reply(server.tr("status.none"))
        return
    remaining = max(0, int(poll.ends_at - time.monotonic()))
    await source.reply(server.tr("status.running", question=poll.question, seconds=remaining))
    await source.reply(server.tr("vote.tally", tally=poll.describe()))


async def _cmd_cancel(source):
    server = source.server
    if _state.get("poll") is None:
        await source.reply(server.tr("error.nothing_running"))
        return
    _clear()
    await _announce(server, server.tr("cancel.done", who=source.player or "console"))


# ---------------------------------------------------------------------------

async def _countdown(server, poll: Poll, duration: float):
    try:
        await asyncio.sleep(duration)
    except asyncio.CancelledError:
        return
    if _state.get("poll") is poll:
        await _finish(server, poll, poll.result(), early=False)


async def _finish(server, poll: Poll, passed: bool, *, early: bool):
    _clear()
    await _announce(server, server.tr(
        "result.passed" if passed else "result.failed",
        question=poll.question, tally=poll.describe(),
        how=server.tr("result.early" if early else "result.on_time"),
    ))
    # The decision is announced, not acted on: this plugin counts votes, and
    # wiring "passed" to a restart is a separate, deliberate choice.
    await server.dispatch_event(
        "vote.finished", poll.question, passed, dict(poll.votes)
    )


def _clear() -> None:
    task = _state.get("task")
    if task is not None and not task.done():
        task.cancel()
    _state["poll"] = None
    _state["task"] = None


async def _announce(server, message: str) -> None:
    server.logger.info(message)
    try:
        await server.game_print(message)
    except QueryError:
        pass


# -- for other plugins -------------------------------------------------------

def current() -> Poll | None:
    """The running poll, for a plugin that wants to act on ``vote.finished``."""
    return _state.get("poll")
