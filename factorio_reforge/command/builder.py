"""A small command tree, in the shape MCDReforged plugin authors expect.

    Literal('!!qb')
        .then(Literal('make').then(GreedyText('comment').runs(make)).runs(make))
        .then(Literal('back').then(Integer('id').runs(back)))

Deliberately a fraction of MCDR's builder: literal / text / greedy-text /
integer nodes, ``requires`` for gating, ``runs`` for the callback. No redirects,
no suggestion engine -- those earn their complexity in a chat client with
tab-completion, which a Factorio server has no way to offer.
"""

from __future__ import annotations

import abc
import inspect
import shlex
from collections.abc import Callable, Sequence
from typing import Any

from factorio_reforge.command.source import CommandSource
from factorio_reforge.permission import PermissionLevel

Callback = Callable[..., Any]


class CommandError(Exception):
    """Raised for input the user can fix; the message is shown to them."""


class PermissionDenied(CommandError):
    pass


class ParseFailure(CommandError):
    """Input that did not match. ``depth`` is how far into the tokens it got.

    The tree tries every branch, so several branches fail for every command
    that succeeds. Ranking failures by depth is what lets the reply point at
    the branch the user was most likely aiming for.
    """

    def __init__(self, message: str, depth: int = 0):
        super().__init__(message)
        self.depth = depth


class CommandContext(dict):
    """Parsed arguments, keyed by node name."""

    def __init__(self, source: CommandSource, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source = source


class ArgumentNode(abc.ABC):
    def __init__(self, name: str):
        self.name = name
        self._children: list[ArgumentNode] = []
        self._callback: Callback | None = None
        self._requirement: Callable[[CommandSource], bool] | None = None
        self._requirement_message: str = "Permission denied"

    # -- building ------------------------------------------------------------

    def then(self, node: ArgumentNode) -> ArgumentNode:
        self._children.append(node)
        return self

    def runs(self, callback: Callback) -> ArgumentNode:
        self._callback = callback
        return self

    def requires(
        self,
        predicate: Callable[[CommandSource], bool] | int | PermissionLevel,
        message: str | None = None,
    ) -> ArgumentNode:
        if isinstance(predicate, (int, PermissionLevel)):
            level = PermissionLevel.parse(predicate)
            self._requirement = lambda src: src.has_permission(level)
            self._requirement_message = message or f"Requires permission {level.label}"
        else:
            self._requirement = predicate
            self._requirement_message = message or "Permission denied"
        return self

    # -- parsing -------------------------------------------------------------

    @abc.abstractmethod
    def _match(self, tokens: Sequence[str]) -> tuple[Any, int] | None:
        """Return ``(value, tokens_consumed)`` or ``None`` if this node does not apply."""

    async def execute(self, source: CommandSource, tokens: Sequence[str], ctx: CommandContext):
        matched = self._match(tokens)
        if matched is None:
            raise ParseFailure(self._mismatch_message(tokens))
        value, consumed = matched

        if self._requirement is not None and not self._requirement(source):
            raise PermissionDenied(self._requirement_message)

        if self.name and not isinstance(self, Literal):
            ctx[self.name] = value

        rest = tokens[consumed:]
        if rest:
            # Report the failure that got *furthest* into the input, not the
            # first child that happened to be tried. Reporting the first one
            # produces answers like "Expected 'help', got 'lang'" for
            # `!!FR lang zh_cn`, which points at an unrelated branch.
            deepest: ParseFailure | None = None
            for child in self._children:
                try:
                    return await child.execute(source, rest, ctx)
                except ParseFailure as exc:
                    exc.depth += consumed
                    if deepest is None or exc.depth > deepest.depth:
                        deepest = exc

            if self._children:
                raise self._unknown_argument(rest) if _all_literals(
                    self._children
                ) else deepest or ParseFailure(f"Unexpected {rest[0]!r}")
            # No children at all: a trailing argument this node simply does not
            # take. Running the callback anyway would silently ignore it.
            raise ParseFailure(
                f"{self._label()} takes no further arguments, but got {rest[0]!r}"
            )

        if self._callback is None:
            raise ParseFailure(
                f"Incomplete command. {self._expected_next()}"
                if self._children else f"Incomplete command near {self._label()!r}"
            )
        return await _call(self._callback, source, ctx)

    def _unknown_argument(self, rest: Sequence[str]) -> ParseFailure:
        """All this node's children are keywords, so name them.

        Listing the alternatives is the single most useful thing to say when
        someone guesses a subcommand that does not exist.
        """
        return ParseFailure(f"Unknown option {rest[0]!r}. {self._expected_next()}")

    def _expected_next(self) -> str:
        options = [c.name for c in self._children if isinstance(c, Literal)]
        arguments = [f"<{c.name}>" for c in self._children if not isinstance(c, Literal)]
        parts = options + arguments
        if not parts:
            return ""
        return f"Expected one of: {', '.join(parts)}"

    def _mismatch_message(self, tokens: Sequence[str]) -> str:
        got = tokens[0] if tokens else "<nothing>"
        return f"Expected {self._label()}, got {got!r}"

    def _label(self) -> str:
        return f"<{self.name}>"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


class Literal(ArgumentNode):
    """An exact keyword. Contributes nothing to the context."""

    def _match(self, tokens):
        if tokens and tokens[0] == self.name:
            return self.name, 1
        return None

    def _label(self) -> str:
        return repr(self.name)


class Text(ArgumentNode):
    """One whitespace-delimited token."""

    def _match(self, tokens):
        return (tokens[0], 1) if tokens else None


class GreedyText(ArgumentNode):
    """Everything left, joined back together. Must be the last node."""

    def _match(self, tokens):
        return (" ".join(tokens), len(tokens)) if tokens else None


class Integer(ArgumentNode):
    def _match(self, tokens):
        if not tokens:
            return None
        try:
            return int(tokens[0]), 1
        except ValueError:
            return None

    def _mismatch_message(self, tokens):
        got = tokens[0] if tokens else "<nothing>"
        return f"{self.name} must be a whole number, got {got!r}"


async def _call(callback: Callback, source: CommandSource, ctx: CommandContext):
    """Call with (source, context), (source,) or () -- whichever it accepts."""
    params = len(inspect.signature(callback).parameters)
    args = (source, ctx)[:params]
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def _all_literals(children: Sequence[ArgumentNode]) -> bool:
    return bool(children) and all(isinstance(c, Literal) for c in children)


def tokenize(line: str) -> list[str]:
    """Split a command line, tolerating unbalanced quotes rather than raising."""
    try:
        return shlex.split(line)
    except ValueError:
        return line.split()
