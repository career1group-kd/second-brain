"""Fireflies module tests: payload mapping, resolver, signature verification."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from mcp_server.fireflies.api import to_meeting_payload
from mcp_server.fireflies.renderer import render_meeting
from mcp_server.fireflies.resolver import resolve_meeting, strip_self_attendees
from mcp_server.fireflies.webhook import _verify_signature
from mcp_server.fireflies.matcher import match_attendees
from mcp_server.fireflies.types import MeetingPayload


# ---- payload mapping ----------------------------------------------------


def _ff_transcript(**overrides):
    base = {
        "id": "FF123",
        "title": "Q3 Plan",
        "dateString": "2026-04-25T10:00:00.000Z",
        "duration": 30,
        "organizer_email": "kay@career1group.com",
        "host_email": "kay@career1group.com",
        "participants": ["kay@career1group.com", "anna.schmidt@klarna.com"],
        "meeting_attendees": [
            {"displayName": "Anna Schmidt", "email": "anna.schmidt@klarna.com"},
        ],
        "speakers": [{"id": "0", "name": "Speaker 0"}, {"id": "1", "name": "Speaker 1"}],
        "sentences": [
            {"speaker_name": "Speaker 0", "text": "Hi all.", "start_time": 5},
            {"speaker_name": "Speaker 1", "text": "Hello.", "start_time": 12},
            {"speaker_name": "Anna Schmidt", "text": "Let's start.", "start_time": 20},
        ],
        "summary": {
            "overview": "Anna and Kay aligned on the Q3 plan.",
            "action_items": "- Send draft to Anna\n- Follow up with Kay",
            "keywords": ["Q3", "plan"],
        },
        "audio_url": "https://example.com/x.mp3",
        "calendar_id": "evt-xyz",
    }
    base.update(overrides)
    return base


def test_to_meeting_payload_basic():
    mapped = to_meeting_payload(_ff_transcript())
    assert mapped["meeting_id"] == "FF123"
    assert mapped["title"] == "Q3 Plan"
    assert mapped["duration_seconds"] == 1800
    # Anna by displayName, kay via participants/organizer.
    emails = {a["email"] for a in mapped["attendees"]}
    assert "anna.schmidt@klarna.com" in emails
    assert "kay@career1group.com" in emails
    assert mapped["action_items"] == ["Send draft to Anna", "Follow up with Kay"]
    assert "Q3 plan" in mapped["summary"]
    # Real-named speakers from transcript get added as attendees.
    names = {a["name"] for a in mapped["attendees"]}
    assert "Anna Schmidt" in names
    # Anonymous speakers do NOT.
    assert "Speaker 0" not in names


def test_to_meeting_payload_action_items_list_form():
    t = _ff_transcript()
    t["summary"]["action_items"] = ["Do X", "Do Y"]
    mapped = to_meeting_payload(t)
    assert mapped["action_items"] == ["Do X", "Do Y"]


def test_to_meeting_payload_handles_missing_summary():
    t = _ff_transcript()
    t["summary"] = None
    mapped = to_meeting_payload(t)
    assert mapped["summary"] == ""
    assert mapped["action_items"] == []


# ---- resolver -----------------------------------------------------------


def _payload_with_anonymous_speakers() -> MeetingPayload:
    mapped = to_meeting_payload(
        _ff_transcript(
            sentences=[
                {"speaker_name": "Speaker 0", "text": "Hi.", "start_time": 0},
                {"speaker_name": "Speaker 0", "text": "Long talk by speaker 0.", "start_time": 10},
                {"speaker_name": "Speaker 1", "text": "Short.", "start_time": 20},
            ],
            summary={
                "overview": "Kay opened the meeting. Anna asked questions. Kay answered Anna.",
                "action_items": [],
                "keywords": [],
            },
            meeting_attendees=[
                {"displayName": "Kay Dollt", "email": "kay@career1group.com"},
                {"displayName": "Anna Schmidt", "email": "anna.schmidt@klarna.com"},
            ],
            participants=[],
        )
    )
    return MeetingPayload(**mapped)


def test_resolver_assigns_anonymous_speakers_to_summary_names():
    payload = _payload_with_anonymous_speakers()
    out = resolve_meeting(payload, calendar=None, raw_transcript=None)
    # Speaker 0 talks more characters, "Kay" is mentioned more often → Kay.
    # Speaker 1 → Anna (next most-mentioned).
    assert out.speaker_to_name.get("Speaker 0") == "Kay Dollt"
    assert out.speaker_to_name.get("Speaker 1") == "Anna Schmidt"


def test_resolver_uses_calendar_when_cal_id_present():
    payload = _payload_with_anonymous_speakers()
    cal = MagicMock()
    cal.get_event.return_value = {
        "id": "evt-xyz",
        "summary": "Sync: Q3 Strategy",
        "attendees": [
            {"email": "kay@career1group.com", "displayName": "Kay Dollt"},
            {"email": "anna.schmidt@klarna.com", "displayName": "Anna Schmidt"},
            {"email": "elena@klarna.com", "displayName": "Elena Test"},
        ],
    }
    out = resolve_meeting(
        payload, calendar=cal, raw_transcript={"calendar_id": "evt-xyz"}
    )
    cal.get_event.assert_called_once_with("evt-xyz")
    assert out.title_override == "Sync: Q3 Strategy"
    assert out.calendar_event_id == "evt-xyz"
    # Calendar-provided attendees flow into the merged list.
    emails = {a.email for a in out.attendees if a.email}
    assert "elena@klarna.com" in emails


def test_resolver_falls_back_to_time_window():
    payload = _payload_with_anonymous_speakers()
    cal = MagicMock()
    cal.get_event.return_value = None
    cal.find_event_around.return_value = {
        "id": "evt-window",
        "summary": "Calendar Lookup Hit",
        "attendees": [
            {"email": "kay@career1group.com", "displayName": "Kay Dollt"},
        ],
    }
    out = resolve_meeting(
        payload, calendar=cal, raw_transcript={"calendar_id": "missing"}
    )
    cal.find_event_around.assert_called_once()
    assert out.calendar_event_id == "evt-window"


def test_resolver_no_calendar_no_summary_no_assignment():
    mapped = to_meeting_payload(
        _ff_transcript(
            summary=None,
            sentences=[
                {"speaker_name": "Speaker 0", "text": "Hi.", "start_time": 0},
            ],
        )
    )
    payload = MeetingPayload(**mapped)
    out = resolve_meeting(payload, calendar=None, raw_transcript=None)
    assert out.speaker_to_name == {}


# ---- strip_self_attendees ----------------------------------------------


def test_strip_self_drops_by_email_and_derived_name():
    from mcp_server.fireflies.types import Attendee

    attendees = [
        Attendee(name="Kay Dollt", email="kay.dollt@career1group.com"),
        Attendee(name="kay.dollt@ktd-holding.de", email="kay.dollt@ktd-holding.de"),
        Attendee(name="Kay Dollt", email=None),  # transcript-speaker entry
        Attendee(name="Tim Schendzielorz", email=None),
        Attendee(name="Tristan Otto", email="tristan@example.com"),
    ]
    self_emails = {"kay.dollt@career1group.com", "kay.dollt@ktd-holding.de"}
    out = strip_self_attendees(attendees, self_emails)
    names = [a.name for a in out]
    assert names == ["Tim Schendzielorz", "Tristan Otto"]


def test_strip_self_no_emails_is_passthrough():
    from mcp_server.fireflies.types import Attendee

    attendees = [Attendee(name="Anna Schmidt", email="anna@x.de")]
    assert strip_self_attendees(attendees, set()) == attendees


# ---- renderer -----------------------------------------------------------


def test_render_uses_speaker_to_name_mapping(tmp_path: Path):
    # Minimal vault with no people: matcher returns everyone as unrecognized.
    (tmp_path / "70_People").mkdir()
    payload = _payload_with_anonymous_speakers()
    matches = match_attendees(tmp_path, payload.attendees)
    rel, raw = render_meeting(
        payload,
        matches,
        speaker_to_name={"Speaker 0": "Kay Dollt", "Speaker 1": "Anna Schmidt"},
        calendar_event_id="evt-xyz",
    )
    body = raw.decode("utf-8")
    assert "fireflies_id: FF123" in body
    assert "calendar_event_id: evt-xyz" in body
    assert "**Kay Dollt**" in body
    assert "**Anna Schmidt**" in body
    # The original "Speaker 0" label should not appear.
    assert "**Speaker 0**" not in body


# ---- signature verification --------------------------------------------


def test_verify_signature_accepts_valid_hmac():
    secret = "mysecret"
    body = b'{"event":"meeting.summarized"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert _verify_signature(secret, body, sig) is True


def test_verify_signature_rejects_invalid():
    secret = "mysecret"
    body = b"{}"
    assert _verify_signature(secret, body, "sha256=deadbeef") is False
    assert _verify_signature(secret, body, None) is False


def test_verify_signature_skipped_when_no_secret():
    assert _verify_signature("", b"{}", None) is True
    assert _verify_signature("", b"{}", "sha256=anything") is True
