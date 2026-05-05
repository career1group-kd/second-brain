"""Meeting-review tools.

Workflow: at the end of the day Claude asks `list_meetings_needing_review`
to find notes whose transcript still has anonymous "Speaker N" labels,
shows utterance samples + the known attendees, then calls
`replace_speaker_in_transcript` per resolved speaker.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import structlog

from .. import frontmatter_io
from ..atomic import atomic_write, file_lock
from ..vault import list_files_with_frontmatter, safe_join
from ._common import ServerContext, fuzzy_match_person
from .vault_write import append_to_person, update_person_meta

log = structlog.get_logger()

_SPEAKER_LINE_RE = re.compile(
    r"^\*\*(Speaker[\s_-]?\d+)\*\*(?:\s+\[(?P<ts>[^\]]+)\])?\s*:\s*(?P<text>.*)$",
    re.MULTILINE,
)
_ANY_SPEAKER_LINE_RE = re.compile(
    r"^\*\*(?P<speaker>[^*]+?)\*\*(?:\s+\[[^\]]+\])?\s*:\s*",
    re.MULTILINE,
)


def _meeting_date(meta: dict[str, Any]) -> date | None:
    raw = meta.get("date")
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _utterance_samples(body: str, speaker: str, *, k: int = 3) -> list[str]:
    """Return the k longest utterances for a given speaker label."""
    pattern = re.compile(
        r"^\*\*"
        + re.escape(speaker)
        + r"\*\*(?:\s+\[[^\]]+\])?\s*:\s*(?P<text>.*)$",
        re.MULTILINE,
    )
    utterances = [m.group("text").strip() for m in pattern.finditer(body)]
    utterances = [u for u in utterances if u]
    utterances.sort(key=len, reverse=True)
    return utterances[:k]


def list_meetings_needing_review(
    ctx: ServerContext,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """List meeting notes with anonymous "Speaker N" labels in the transcript.

    Defaults to today only. Returns one entry per meeting, with three
    longest utterances per anonymous speaker and the list of known
    attendees + summary excerpt — enough context for Claude (or the user)
    to guess who each speaker probably was.
    """
    today = date.today()
    df = date_from or today
    dt = date_to or today

    docs = list_files_with_frontmatter(
        ctx.settings.vault_path, subdir="50_Daily/meetings", type_filter="meeting"
    )

    out: list[dict[str, Any]] = []
    for d in docs:
        mdate = _meeting_date(d["frontmatter"])
        if mdate is None or not (df <= mdate <= dt):
            continue
        abs_path = safe_join(ctx.settings.vault_path, d["path"])
        try:
            raw = abs_path.read_bytes()
        except OSError:
            continue
        _, body = frontmatter_io.parse_bytes(raw)

        anon_speakers = sorted({m.group(1) for m in _SPEAKER_LINE_RE.finditer(body)})
        meta = d["frontmatter"]
        unrecognized = list(meta.get("unrecognized_attendees") or [])
        if not anon_speakers and not unrecognized:
            continue

        samples = {sp: _utterance_samples(body, sp) for sp in anon_speakers}
        # Pull a short summary excerpt for context.
        summary = ""
        m = re.search(r"##\s*Summary\s*\n+(.+?)(?=\n##\s|\Z)", body, re.DOTALL)
        if m:
            summary = m.group(1).strip()[:600]

        out.append(
            {
                "path": d["path"],
                "title": meta.get("title") or d["title"],
                "date": mdate.isoformat(),
                "anonymous_speakers": anon_speakers,
                "samples": samples,
                "unrecognized_attendees": unrecognized,
                "known_attendees": list(meta.get("attendees") or []),
                "summary_excerpt": summary,
            }
        )

    out.sort(key=lambda e: (e["date"], e["title"]), reverse=True)
    return {"results": out, "count": len(out)}


def _replace_speaker_in_body(body: str, old_speaker: str, new_name: str) -> tuple[str, int]:
    pattern = re.compile(
        r"(\*\*)" + re.escape(old_speaker) + r"(\*\*)(?=\s+\[|\s*:)"
    )
    new_body, n = pattern.subn(rf"\1{new_name}\2", body)
    return new_body, n


def replace_speaker_in_transcript(
    ctx: ServerContext,
    *,
    path: str,
    old_speaker: str,
    new_name: str,
) -> dict[str, Any]:
    """Rewrite `**old_speaker**` → `**new_name**` in a meeting transcript.

    Side effects when `new_name` resolves to an existing person note:
    - Adds `[[70_People/<Name>]]` to frontmatter `attendees` (deduped).
    - Drops `old_speaker` from `unrecognized_attendees` if present.
    - Appends a history entry on the person note + bumps last_interaction.
    """
    abs_path = safe_join(ctx.settings.vault_path, path)
    if not abs_path.exists():
        return {"error": "note not found", "code": "NOT_FOUND"}

    person_match = fuzzy_match_person(ctx.settings.vault_path, new_name)
    person_path = person_match[0]["doc"]["path"] if person_match else None
    person_title = Path(person_path).stem if person_path else None

    with file_lock(abs_path):
        raw = abs_path.read_bytes()
        meta, body = frontmatter_io.parse_bytes(raw)
        new_body, replacements = _replace_speaker_in_body(body, old_speaker, new_name)
        if replacements == 0:
            return {
                "ok": False,
                "error": f"no '**{old_speaker}**' lines found in transcript",
                "code": "NO_MATCH",
            }

        attendees = list(meta.get("attendees") or [])
        if person_path:
            wikilink = f"[[{person_path.removesuffix('.md')}]]"
            if wikilink not in attendees:
                attendees.append(wikilink)
        meta["attendees"] = attendees

        unrecognized = list(meta.get("unrecognized_attendees") or [])
        unrecognized = [u for u in unrecognized if u != old_speaker]
        meta["unrecognized_attendees"] = unrecognized

        meta["updated"] = date.today()

        new_raw = frontmatter_io.render(meta, new_body)
        atomic_write(abs_path, new_raw)

    person_linked: str | None = None
    if person_title:
        try:
            append_to_person(
                ctx,
                name=person_title,
                section="History",
                content=f"[[{path.removesuffix('.md')}]]",
            )
            update_person_meta(
                ctx,
                name=person_title,
                fields={"last_interaction": date.today()},
            )
            person_linked = person_title
        except Exception:
            log.exception("meeting_review_person_update_failed", path=person_path)

    return {
        "ok": True,
        "replacements": replacements,
        "person_linked": person_linked,
    }
