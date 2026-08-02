#!/usr/bin/env python3
"""Check the docs hold together: links resolve, and both languages exist.

Three failure modes this catches, all of which happened during development:
a heading gets reworded and the anchor pointing at it dies silently, a doc is
renamed without its counterpart being updated, and an English page is added
with no Chinese one beside it.

GitHub's anchor slug rules are reproduced exactly, including the detail that
runs of whitespace are *not* collapsed -- ``Part 1 — Running`` becomes
``part-1--running``, with two hyphens, because the em dash is stripped and both
surrounding spaces still become hyphens.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
HTML_LINK = re.compile(r'href="([^"]+)"')
CODE_FENCE = re.compile(r"```.*?```", re.S)


def slug(heading: str) -> str:
    text = re.sub(r"<[^>]+>", "", heading)
    # Emphasis markers are stripped, but not underscores: GitHub keeps
    # those, so a heading like `mod_manager` anchors as #mod_manager.
    text = re.sub(r"[*`]", "", text)
    text = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Every anchor GitHub will generate, including the -1 suffix on duplicates."""
    seen: dict[str, int] = {}
    result: set[str] = set()
    for _, heading in HEADING.findall(text):
        base = slug(heading)
        count = seen.get(base, 0)
        result.add(base if count == 0 else f"{base}-{count}")
        seen[base] = count + 1
    return result


def links(text: str) -> list[str]:
    stripped = CODE_FENCE.sub("", text)
    return MD_LINK.findall(stripped) + HTML_LINK.findall(stripped)


def check(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    own_anchors = anchors(text)
    problems: list[str] = []

    for target in links(text):
        target = target.strip()
        if target.startswith(("http://", "https://", "mailto:")):
            continue

        file_part, _, anchor = target.partition("#")

        if not file_part:
            if anchor and anchor not in own_anchors:
                problems.append(f"{path.relative_to(REPO)}: dead anchor #{anchor}")
            continue

        referenced = (path.parent / file_part).resolve()
        if not referenced.exists():
            problems.append(f"{path.relative_to(REPO)}: missing file {file_part}")
        elif anchor and referenced.suffix == ".md":
            if anchor not in anchors(referenced.read_text(encoding="utf-8")):
                problems.append(
                    f"{path.relative_to(REPO)}: dead anchor {file_part}#{anchor}"
                )
    return problems


def unpaired(docs: list[Path]) -> list[str]:
    """Every page must exist in both languages, as ``x.md`` and ``x_zh.md``.

    The docs are parallel by design, and the way that decays is one language
    quietly gaining a page the other never gets.
    """
    names = {p.parent / p.name for p in docs}
    problems = []
    for path in docs:
        if path.stem.endswith("_zh"):
            counterpart = path.parent / f"{path.stem[:-3]}.md"
            missing = f"{path.stem[:-3]}.md"
        else:
            counterpart = path.parent / f"{path.stem}_zh.md"
            missing = f"{path.stem}_zh.md"
        if counterpart not in names:
            problems.append(
                f"{path.relative_to(REPO)}: has no counterpart {missing}"
            )
    return problems


def main() -> int:
    docs = sorted(
        p for p in [*REPO.glob("*.md"), *(REPO / "docs").glob("*.md")] if p.is_file()
    )
    if not docs:
        print("no markdown files found", file=sys.stderr)
        return 1

    problems: list[str] = unpaired(docs)
    for path in docs:
        problems.extend(check(path))

    for problem in problems:
        print(problem, file=sys.stderr)

    print(f"checked {len(docs)} file(s), {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
