"""Speaker → person matcher.

Strategy:
1. If the attendee has an email, match exactly (case-insensitive) against
   `frontmatter.email` of any person note.
2. Otherwise, fuzzy-match the attendee name against the note title with
   a rapidfuzz threshold of 85.
"""

from __future__ import annotations

from pathlib import Path

from rapidfuzz import fuzz

from ..vault import list_files_with_frontmatter
from .types import Attendee, MatchResult


def match_attendees(
    vault_root: Path,
    attendees: list[Attendee],
    *,
    threshold: int = 85,
) -> MatchResult:
    docs = list_files_with_frontmatter(vault_root, subdir="70_People", type_filter="person")
    by_email: dict[str, str] = {}
    titles: list[tuple[str, str]] = []  # (title, path)
    for d in docs:
        email = (d["frontmatter"].get("email") or "").strip().lower()
        if email:
            by_email[email] = d["path"]
        titles.append((d["title"], d["path"]))

    matched: list[tuple[str, str]] = []
    unrecognized: list[str] = []

    for att in attendees:
        if att.email and att.email.lower() in by_email:
            matched.append((att.name, by_email[att.email.lower()]))
            continue
        best_score = 0
        best_path: str | None = None
        for title, path in titles:
            score = fuzz.WRatio(att.name, title)
            if score > best_score:
                best_score = score
                best_path = path
        if best_path is not None and best_score >= threshold:
            matched.append((att.name, best_path))
        else:
            unrecognized.append(att.name)

    return MatchResult(matched=matched, unrecognized=unrecognized)
