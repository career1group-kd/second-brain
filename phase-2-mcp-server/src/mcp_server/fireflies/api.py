"""Minimal Fireflies GraphQL client.

Webhook delivers `{event, timestamp, meeting_id}`; we pull the full
transcript with one GraphQL query.

API: POST https://api.fireflies.ai/graphql
Auth: Authorization: Bearer <FIREFLIES_API_KEY>
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

DEFAULT_ENDPOINT = "https://api.fireflies.ai/graphql"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

log = structlog.get_logger()

# Fireflies labels unidentified voices "Speaker 0/1/2/...". Anything else
# we treat as a real name. The recording user sometimes shows up as the
# user's own name (good) or as "Me" / their email (filter those).
_PLACEHOLDER_SPEAKERS = {"me", "i", "ich", "unknown"}
_SPEAKER_N_RE = re.compile(r"^speaker[\s_-]*\d+$", re.IGNORECASE)


def _is_real_speaker(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if n.lower() in _PLACEHOLDER_SPEAKERS:
        return False
    if _SPEAKER_N_RE.match(n):
        return False
    return True


# One query covers everything we need from the meeting note.
_TRANSCRIPT_QUERY = """
query Transcript($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    dateString
    duration
    organizer_email
    host_email
    participants
    meeting_attendees { displayName email name }
    speakers { id name }
    sentences { index speaker_id speaker_name text start_time }
    summary {
      overview
      short_summary
      gist
      bullet_gist
      action_items
      keywords
      outline
      shorthand_bullet
    }
    audio_url
    calendar_id
    calendar_type
    meeting_link
  }
}
"""


class FirefliesError(Exception):
    pass


def fetch_transcript(
    api_key: str,
    transcript_id: str,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
) -> dict[str, Any]:
    if not api_key:
        raise FirefliesError("FIREFLIES_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"query": _TRANSCRIPT_QUERY, "variables": {"id": transcript_id}}

    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(endpoint, headers=headers, json=payload)

    if resp.status_code != 200:
        raise FirefliesError(
            f"fireflies http {resp.status_code}: {resp.text[:200]}"
        )
    body = resp.json()
    if body.get("errors"):
        raise FirefliesError(f"fireflies graphql: {body['errors']}")

    transcript = (body.get("data") or {}).get("transcript")
    if not transcript:
        raise FirefliesError(f"transcript {transcript_id} not found")
    return transcript


def _summary_to_markdown(summary: dict[str, Any] | None) -> str:
    if not isinstance(summary, dict):
        return ""
    parts: list[str] = []
    overview = summary.get("overview") or summary.get("short_summary") or summary.get("gist")
    if overview:
        parts.append(str(overview).strip())

    bullet = summary.get("bullet_gist") or summary.get("shorthand_bullet")
    if bullet:
        parts.append("### Highlights\n\n" + str(bullet).strip())

    outline = summary.get("outline")
    if outline:
        parts.append("### Outline\n\n" + str(outline).strip())

    keywords = summary.get("keywords")
    if isinstance(keywords, list) and keywords:
        parts.append("**Keywords:** " + ", ".join(str(k) for k in keywords))

    return "\n\n".join(p for p in parts if p)


def _action_items(summary: dict[str, Any] | None) -> list[str]:
    if not isinstance(summary, dict):
        return []
    raw = summary.get("action_items")
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    # Fireflies sometimes returns a single string with newline-separated items
    # or markdown bullets — normalise to a list of plain lines.
    items: list[str] = []
    for line in str(raw).splitlines():
        s = line.strip()
        if not s:
            continue
        # Drop leading bullet markers / list numbering.
        s = re.sub(r"^[-*•]\s+", "", s)
        s = re.sub(r"^\d+[.)]\s+", "", s)
        if s:
            items.append(s)
    return items


def to_meeting_payload(transcript: dict[str, Any]) -> dict[str, Any]:
    """Map a Fireflies transcript into the same shape as MeetingPayload."""
    started_at: datetime | None = None
    date_str = transcript.get("dateString")
    if date_str:
        try:
            started_at = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        except ValueError:
            started_at = None
    if started_at is None:
        ms = transcript.get("date")
        if isinstance(ms, (int, float)):
            started_at = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    if started_at is None:
        started_at = datetime.now(timezone.utc)

    duration_minutes = transcript.get("duration") or 0
    try:
        duration_seconds = int(float(duration_minutes) * 60)
    except (TypeError, ValueError):
        duration_seconds = 0

    ended_at = None
    if duration_seconds > 0:
        ended_at = started_at + timedelta(seconds=duration_seconds)

    # Attendees: prefer meeting_attendees (rich), fall back to participants
    # (just emails). Names default to email when absent.
    attendees: list[dict[str, Any]] = []
    seen: set[str] = set()
    for att in transcript.get("meeting_attendees") or []:
        if not isinstance(att, dict):
            continue
        email = (att.get("email") or "").strip()
        name = (att.get("displayName") or att.get("name") or email or "").strip()
        if not name:
            continue
        key = (email or name).lower()
        if key in seen:
            continue
        attendees.append({"name": name, "email": email or None})
        seen.add(key)

    for email in transcript.get("participants") or []:
        if not email:
            continue
        if email.lower() in seen:
            continue
        attendees.append({"name": email, "email": email})
        seen.add(email.lower())

    organizer = transcript.get("organizer_email") or transcript.get("host_email")
    if organizer and organizer.lower() not in seen:
        attendees.append({"name": organizer, "email": organizer})
        seen.add(organizer.lower())

    # Transcript lines.
    transcript_lines: list[dict[str, Any]] = []
    speakers_in_transcript: list[str] = []
    for s in transcript.get("sentences") or []:
        if not isinstance(s, dict):
            continue
        speaker = (s.get("speaker_name") or "").strip() or "Unknown"
        text = (s.get("text") or "").strip()
        if not text:
            continue
        ts = None
        start = s.get("start_time")
        if isinstance(start, (int, float)):
            ts = _seconds_to_clock(start)
        transcript_lines.append({"speaker": speaker, "timestamp": ts, "text": text})
        if speaker not in speakers_in_transcript:
            speakers_in_transcript.append(speaker)

    # Add real-named speakers from transcript as attendees if not already there.
    seen_names = {a["name"].lower() for a in attendees}
    for name in speakers_in_transcript:
        if not _is_real_speaker(name):
            continue
        if name.lower() in seen_names:
            continue
        attendees.append({"name": name, "email": None})
        seen_names.add(name.lower())

    summary_md = _summary_to_markdown(transcript.get("summary"))
    action_items = _action_items(transcript.get("summary"))

    return {
        "meeting_id": transcript["id"],
        "title": transcript.get("title") or "Untitled meeting",
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat() if ended_at else None,
        "duration_seconds": duration_seconds,
        "language": "de",  # Fireflies doesn't expose language on the transcript
        "meeting_type": "sync",
        "attendees": attendees,
        "summary": summary_md,
        "action_items": action_items,
        "transcript": transcript_lines,
        "audio_url": transcript.get("audio_url"),
    }


def _seconds_to_clock(seconds: float) -> str:
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"
