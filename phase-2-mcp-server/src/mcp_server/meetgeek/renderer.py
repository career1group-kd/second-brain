"""Render a MeetGeek payload + match result into a markdown meeting note."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from slugify import slugify

from .. import frontmatter_io
from .types import MatchResult, MeetingPayload


def _today() -> date:
    return date.today()


def output_path(payload: MeetingPayload) -> str:
    started = payload.started_at
    if isinstance(started, datetime):
        d = started.date()
    else:
        d = started
    slug = slugify(payload.title or "meeting")
    return f"50_Daily/meetings/{d.isoformat()}-{slug}.md"


def _wikilink(person_path: str) -> str:
    rel = person_path.removesuffix(".md")
    return f"[[{rel}]]"


def render_meeting(
    payload: MeetingPayload,
    matches: MatchResult,
    *,
    project: str | None = None,
    relative_path: str | None = None,
) -> tuple[str, bytes]:
    """Return (relative_path, raw_bytes)."""
    rel = relative_path or output_path(payload)
    started = payload.started_at
    meeting_date = (
        started.date() if isinstance(started, datetime) else started
    )
    duration_minutes = max(0, payload.duration_seconds // 60)

    attendees_links = [_wikilink(p) for _, p in matches.matched]
    today = _today()
    meta: dict[str, Any] = {
        "title": payload.title,
        "type": "meeting",
        "date": meeting_date,
        "project": project,
        "attendees": attendees_links,
        "unrecognized_attendees": list(matches.unrecognized),
        "meeting_type": payload.meeting_type or "sync",
        "duration_minutes": duration_minutes,
        "meetgeek_id": payload.meeting_id,
        "audio_url": payload.audio_url,
        "language": payload.language or "de",
        "created": today,
        "updated": today,
    }

    body_lines: list[str] = [f"# {payload.title}", ""]
    body_lines.append("## Summary")
    body_lines.append("")
    body_lines.append((payload.summary or "").strip())
    body_lines.append("")
    body_lines.append("## Action Items")
    body_lines.append("")
    if payload.action_items:
        for item in payload.action_items:
            body_lines.append(f"- [ ] {item.strip()}")
    body_lines.append("")
    body_lines.append("## Transcript")
    body_lines.append("")
    body_lines.append("<details>")
    body_lines.append("<summary>Click to expand</summary>")
    body_lines.append("")
    for line in payload.transcript:
        ts = f" [{line.timestamp}]" if line.timestamp else ""
        body_lines.append(f"**{line.speaker}**{ts}: {line.text.strip()}")
        body_lines.append("")
    body_lines.append("</details>")

    body = "\n".join(body_lines).rstrip() + "\n"
    raw = frontmatter_io.render(meta, body)
    return rel, raw
