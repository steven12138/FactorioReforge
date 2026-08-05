"""Say something while a slow thing is happening.

Fetching the mod index takes about fourteen seconds and twenty-two thousand
mods. One line before and one line after leaves the operator staring at a
console that looks hung, and the usual reaction to that is to press Ctrl-C --
which is the one thing that makes it worse.

The shape is deliberately not a spinner. Chat has no cursor addressing: every
update is a *new* line in front of every player, so this reports on a timer
rather than on every byte, and says so in a way that is still readable when the
lines pile up::

    [####------] 40%  8,900 / 22,500 mods

Two rules keep it from becoming noise:

* nothing is emitted until :attr:`quiet_for` has passed, so a fast operation
  says nothing at all -- most of them finish before anyone would have wondered;
* updates are rate limited to :attr:`interval`, whatever the caller does.
"""

from __future__ import annotations

import time
from collections.abc import Callable

BAR_WIDTH = 10
FILLED = "#"
EMPTY = "-"


class Progress:
    """Rate-limited progress reporting for an operation of unknown speed.

    ``report`` receives a rendered line. It is called from wherever
    :meth:`update` is called, so a caller on the event loop stays on it.
    """

    def __init__(
        self,
        report: Callable[[str], None],
        *,
        total: int | None = None,
        unit: str = "",
        interval: float = 3.0,
        quiet_for: float = 2.0,
        now: Callable[[], float] = time.monotonic,
    ):
        self.report = report
        self.total = total if total and total > 0 else None
        self.unit = unit
        self.interval = interval
        self.quiet_for = quiet_for
        self._now = now
        self._started = now()
        self._last = 0.0
        self._current = 0

    def update(self, current: int, total: int | None = None) -> None:
        """Record progress, and emit a line if it is time for one."""
        self._current = current
        if total and total > 0:
            self.total = total

        moment = self._now()
        if moment - self._started < self.quiet_for:
            return
        if self._last and moment - self._last < self.interval:
            return
        self._last = moment
        self.report(self.render())

    def advance(self, by: int = 1) -> None:
        self.update(self._current + by)

    def render(self) -> str:
        elapsed = self._now() - self._started
        if self.total:
            fraction = min(1.0, self._current / self.total)
            filled = int(round(fraction * BAR_WIDTH))
            bar = FILLED * filled + EMPTY * (BAR_WIDTH - filled)
            counts = f"{self._current:,} / {self.total:,}"
            return f"[{bar}] {fraction * 100:3.0f}%  {counts} {self.unit}".rstrip()
        # No total: an elapsed counter is honest, where a fake bar is not.
        if self._current:
            return f"[{EMPTY * BAR_WIDTH}] {self._current:,} {self.unit} ({elapsed:.0f}s)".strip()
        # Nothing to count either -- waiting on a round trip rather than on
        # bytes. Reporting "0" would read as no progress rather than as no
        # counter, so the elapsed time carries the line on its own.
        return f"[{EMPTY * BAR_WIDTH}] {elapsed:.0f}s"

    def done(self, message: str | None = None) -> None:
        """Emit a final line, but only if anything was emitted before it.

        An operation that finished inside the quiet window should leave no
        trace: its result is the only thing worth a line.
        """
        if self._last and message:
            self.report(message)
