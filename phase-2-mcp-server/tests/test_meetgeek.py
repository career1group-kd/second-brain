"""MeetGeek matcher + renderer tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mcp_server.meetgeek.matcher import match_attendees
from mcp_server.meetgeek.renderer import output_path, render_meeting
from mcp_server.meetgeek.types import Attendee, MeetingPayload


def _payload(**overrides) -> MeetingPayload:
    base = {
        "meeting_id": "abc123",
        "title": "Q3 Plan Sync",
        "started_at": datetime(2026, 4, 25, 10, 0, 0),
        "ended_at": datetime(2026, 4, 25, 10, 30, 0),
        "duration_seconds": 1800,
        "language": "de",
        "meeting_type": "sync",
        "attendees": [
            {"name": "Anna Schmidt", "email": "anna.schmidt@klarna.com"},
            {"name": "Stefan Müller", "email": None},
        ],
        "summary": "Q3 plan reviewed.",
        "action_items": ["Send draft to Anna", "Follow up with John"],
        "transcript": [
            {"speaker": "Anna Schmidt", "timestamp": "00:01:23", "text": "Hi all."},
            {"speaker": "Stefan Müller", "timestamp": "00:02:00", "text": "Hello."},
        ],
        "audio_url": None,
    }
    base.update(overrides)
    return MeetingPayload(**base)


def test_match_by_email(fixture_vault: Path) -> None:
    payload = _payload()
    result = match_attendees(fixture_vault, payload.attendees)
    matched_names = {n for n, _ in result.matched}
    assert "Anna Schmidt" in matched_names
    assert "Stefan Müller" in result.unrecognized


def test_match_by_fuzzy_name(fixture_vault: Path) -> None:
    attendees = [Attendee(name="Anna Smith", email=None)]  # close but not exact
    result = match_attendees(fixture_vault, attendees, threshold=70)
    assert any("Anna" in n for n, _ in result.matched)


def test_render_meeting_produces_valid_markdown(fixture_vault: Path) -> None:
    payload = _payload()
    matches = match_attendees(fixture_vault, payload.attendees)
    rel, raw = render_meeting(payload, matches, project="ChapterNext")
    assert rel.startswith("50_Daily/meetings/2026-04-25-")
    body = raw.decode("utf-8")
    assert "type: meeting" in body
    assert "meetgeek_id: abc123" in body
    assert "## Summary" in body
    assert "Q3 plan reviewed." in body
    assert "## Action Items" in body
    assert "- [ ] Send draft to Anna" in body
    assert "## Transcript" in body
    assert "**Anna Schmidt** [00:01:23]: Hi all." in body
    assert "[[70_People/Anna Schmidt]]" in body
    assert "Stefan Müller" in body


def test_output_path_slug(fixture_vault: Path) -> None:
    payload = _payload(title="Brand: Strategy & Vision")
    rel = output_path(payload)
    # slugify drops "&" and collapses punctuation.
    assert rel.startswith("50_Daily/meetings/2026-04-25-")
    assert rel.endswith(".md")
    assert "brand" in rel and "strategy" in rel and "vision" in rel
