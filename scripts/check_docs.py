#!/usr/bin/env python3
"""Check that every link in the Markdown docs actually goes somewhere.

Two failure modes this catches, both of which happened during development:
a heading gets reworded and the table-of-contents anchor pointing at it dies
silently, and a doc is renamed without its counterpart being updated.

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
    text = re.sub(r"[*_`]", "", text)
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


def main() -> int:
    docs = sorted(
        p for p in [*REPO.glob("*.md"), *(REPO / "docs").glob("*.md")] if p.is_file()
    )
    if not docs:
        print("no markdown files found", file=sys.stderr)
        return 1

    problems: list[str] = []
    for path in docs:
        problems.extend(check(path))

    for problem in problems:
        print(problem, file=sys.stderr)

    print(f"checked {len(docs)} file(s), {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
