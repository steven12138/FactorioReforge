"""Watch Factorio's startup output and report on it afterwards.

Factorio's log is left **exactly as it is** -- not reworded, not annotated, not
re-levelled. Anyone comparing the console against ``factorio-current.log`` or a
forum post should see the same text.

What this does instead is read along, and once the server is up, print a
separate FactorioReforge summary: what was noticed, which of it is routine, and
which of it wants attention. Several Factorio lines say "not found" as a matter
of course -- the blueprint-storage fallback, the absent Steam cloud data -- and
without somewhere to say so, every operator investigates them once.

Every pattern here was taken from output observed on a real 2.0.77 server.
"""

from __future__ import annotations

import dataclasses
import enum
import re

from factorio_reforge.core.info import Info


class Severity(enum.IntEnum):
    ROUTINE = 0
    """Looks like a fault, is not. Reported so nobody goes looking."""
    NOTICE = 1
    """Worth knowing about, but the server runs."""
    PROBLEM = 2
    """Wants attention."""


@dataclasses.dataclass(frozen=True)
class Observation:
    severity: Severity
    #: Translation key under ``startup.`` for the one-line explanation.
    key: str
    values: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern
    severity: Severity
    key: str
    #: Named groups to carry into the message.
    fields: tuple[str, ...] = ()


_RULES: tuple[_Rule, ...] = (
    # -- routine: the "not found" lines that are simply how it works
    _Rule(
        re.compile(r'Blueprint storage "(?P<current>[^"]+)" was not found, '
                   r'trying to load previous version storage "(?P<previous>[^"]+)"'),
        Severity.ROUTINE, "blueprint_storage", ("current", "previous"),
    ),
    _Rule(
        re.compile(r"Cloud player-data\.json unavailable"),
        Severity.ROUTINE, "cloud_player_data",
    ),
    _Rule(re.compile(r"Audio is disabled"), Severity.ROUTINE, "audio_disabled"),

    # -- notices: true, useful, and easy to miss in the scroll
    _Rule(
        re.compile(r"Hosting game at IP ADDR:\(\{(?P<address>[^}]+)\}\)"),
        Severity.NOTICE, "hosting", ("address",),
    ),
    # Split in two so an exposed bind is a problem rather than a note. RCON is
    # plaintext: reaching the port is controlling the server.
    _Rule(
        re.compile(r"Starting RCON interface at IP ADDR:\("
                   r"\{(?P<address>(?:127\.0\.0\.1|localhost|::1)[^}]*)\}\)"),
        Severity.NOTICE, "rcon_local", ("address",),
    ),
    _Rule(
        re.compile(r"Starting RCON interface at IP ADDR:\(\{(?P<address>[^}]+)\}\)"),
        Severity.PROBLEM, "rcon_exposed", ("address",),
    ),
    _Rule(
        re.compile(r"Loading mod (?P<mod>\S+) (?P<version>\S+) \(data\.lua\)"),
        Severity.NOTICE, "mod_loaded", ("mod", "version"),
    ),

    # -- problems
    _Rule(
        re.compile(r'Failed to load mod "(?P<mod>[^"]+)"'),
        Severity.PROBLEM, "mod_failed", ("mod",),
    ),
    _Rule(
        re.compile(r"Incompatible Factorio version \(current: (?P<have>[\d.]+), "
                   r"required: (?P<need>[\d.]+)\)"),
        Severity.PROBLEM, "version_mismatch", ("have", "need"),
    ),
    _Rule(
        re.compile(r"Dependency (?P<dep>.+?) is not satisfied"),
        Severity.PROBLEM, "dependency", ("dep",),
    ),
    _Rule(
        re.compile(r"Couldn't acquire exclusive lock"),
        Severity.PROBLEM, "locked",
    ),
    _Rule(
        re.compile(r"Address already in use|Failed to bind"),
        Severity.PROBLEM, "port_in_use",
    ),
    _Rule(re.compile(r"[Dd]esync|out of sync"), Severity.PROBLEM, "desync"),
    _Rule(
        re.compile(r"Saving failed|Failed to save"), Severity.PROBLEM, "save_failed"
    ),
)

#: Mods that ship with the game; listing them as "loaded" is noise.
_BUILTIN_MODS = frozenset({"core", "base", "elevated-rails", "quality", "space-age"})


class LogLens:
    """Accumulates observations while the server starts.

    Deliberately does not touch the lines it reads. It only remembers, and the
    report is emitted separately once startup finishes.
    """

    def __init__(self) -> None:
        self._seen: list[Observation] = []
        self._keys: set[tuple[str, tuple]] = set()

    def observe(self, info: Info) -> None:
        if not info.is_from_server:
            return
        text = info.content or info.raw_content

        for rule in _RULES:
            match = rule.pattern.search(text)
            if match is None:
                continue
            values = {field: match.group(field) for field in rule.fields}
            if rule.key == "mod_loaded" and values.get("mod") in _BUILTIN_MODS:
                return
            # One entry per distinct thing: a message repeated on every tick
            # would otherwise bury the rest of the report.
            identity = (rule.key, tuple(sorted(values.items())))
            if identity not in self._keys:
                self._keys.add(identity)
                self._seen.append(Observation(rule.severity, rule.key, values))
            return

    def reset(self) -> None:
        self._seen.clear()
        self._keys.clear()

    @property
    def observations(self) -> list[Observation]:
        return list(self._seen)

    def by_severity(self, severity: Severity) -> list[Observation]:
        return [o for o in self._seen if o.severity is severity]

    def report(self, tr) -> list[tuple[Severity, str]]:
        """Render the report as ``(severity, line)`` pairs, worst first.

        Empty when there is nothing to say, so a clean start stays quiet.
        """
        lines: list[tuple[Severity, str]] = []
        for severity in (Severity.PROBLEM, Severity.NOTICE, Severity.ROUTINE):
            for observation in self.by_severity(severity):
                lines.append(
                    (severity, tr(f"startup.{observation.key}", **observation.values))
                )
        return lines

    def summary(self, tr) -> str | None:
        """One headline line, or None when there is nothing worth saying."""
        problems = len(self.by_severity(Severity.PROBLEM))
        notices = len(self.by_severity(Severity.NOTICE))
        routine = len(self.by_severity(Severity.ROUTINE))
        if not (problems or notices or routine):
            return None
        return tr(
            "startup.summary", problems=problems, notices=notices, routine=routine
        )
