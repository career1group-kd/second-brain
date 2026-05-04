"""Minimal MeetGeek REST client.

The webhook only delivers a notification (`meeting_id` + `message`); the
actual meeting payload has to be pulled from MeetGeek's API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

BASE_URL = "https://api.meetgeek.ai/v1"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MeetGeekError(Exception):
    pass


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _unwrap(data: Any) -> dict[str, Any] | None:
    """MeetGeek sometimes returns a single object as a one-element list."""
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def _get(client: httpx.Client, path: str, token: str) -> Any:
    resp = client.get(f"{BASE_URL}{path}", headers=_headers(token), timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_meeting_bundle(token: str, meeting_id: str) -> dict[str, Any]:
    """Fetch metadata + (best-effort) transcript / highlights / tasks.

    Returns a dict with keys: `meeting`, `transcript`, `highlights`, `tasks`.
    Only `meeting` is guaranteed; the rest are None on error.
    """
    if not token:
        raise MeetGeekError("MEETGEEK_API_TOKEN not configured")

    with httpx.Client() as client:
        meeting_raw = _get(client, f"/meetings/{meeting_id}", token)
        meeting = _unwrap(meeting_raw)
        if meeting is None:
            raise MeetGeekError(f"meeting {meeting_id} not found")

        out: dict[str, Any] = {
            "meeting": meeting,
            "transcript": None,
            "highlights": None,
            "tasks": None,
        }

        for key, path in (
            ("transcript", f"/meetings/{meeting_id}/transcripts"),
            ("highlights", f"/meetings/{meeting_id}/highlights"),
            ("tasks", f"/meetings/{meeting_id}/tasks"),
        ):
            try:
                out[key] = _get(client, path, token)
            except httpx.HTTPError as e:
                log.info("meetgeek_api_optional_failed", endpoint=path, error=str(e))

        return out


def to_meeting_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    """Map a MeetGeek API bundle to our internal MeetingPayload shape."""
    m = bundle["meeting"]

    started_at = m.get("timestamp_start_utc") or m.get("start_time")
    ended_at = m.get("timestamp_end_utc") or m.get("end_time")
    duration = 0
    if started_at and ended_at:
        try:
            s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration = max(0, int((e - s).total_seconds()))
        except Exception:
            duration = 0

    language = (m.get("language") or "de").split("-")[0]

    attendees: list[dict[str, Any]] = []
    seen: set[str] = set()
    host_email = m.get("host_email")
    if host_email:
        attendees.append({"name": host_email, "email": host_email})
        seen.add(host_email.lower())
    for email in m.get("participant_emails") or []:
        if not email or email.lower() in seen:
            continue
        attendees.append({"name": email, "email": email})
        seen.add(email.lower())
    # `participants` is the alternate field name some API versions use.
    for p in m.get("participants") or []:
        if isinstance(p, dict):
            email = p.get("email")
            if email and email.lower() in seen:
                continue
            attendees.append({"name": p.get("name") or email or "Unknown", "email": email})
            if email:
                seen.add(email.lower())

    summary = ""
    action_items: list[str] = []
    transcript_lines: list[dict[str, Any]] = []

    highlights = bundle.get("highlights")
    if isinstance(highlights, list):
        bullets = [h.get("text") for h in highlights if isinstance(h, dict) and h.get("text")]
        summary = "\n".join(f"- {b}" for b in bullets)
    elif isinstance(highlights, dict):
        summary = highlights.get("summary") or highlights.get("text") or ""

    tasks = bundle.get("tasks")
    if isinstance(tasks, list):
        action_items = [t.get("text") or t.get("title") or "" for t in tasks if isinstance(t, dict)]
        action_items = [a for a in action_items if a]

    transcript = bundle.get("transcript")
    if isinstance(transcript, list):
        for line in transcript:
            if not isinstance(line, dict):
                continue
            transcript_lines.append(
                {
                    "speaker": line.get("speaker") or line.get("speaker_name") or "Unknown",
                    "timestamp": line.get("timestamp") or line.get("start_time"),
                    "text": line.get("text") or line.get("content") or "",
                }
            )
    elif isinstance(transcript, dict):
        for line in transcript.get("segments") or transcript.get("lines") or []:
            if isinstance(line, dict):
                transcript_lines.append(
                    {
                        "speaker": line.get("speaker") or "Unknown",
                        "timestamp": line.get("timestamp") or line.get("start_time"),
                        "text": line.get("text") or "",
                    }
                )

    return {
        "meeting_id": m["meeting_id"],
        "title": m.get("title") or "Untitled meeting",
        "started_at": started_at or datetime.utcnow().isoformat(),
        "ended_at": ended_at,
        "duration_seconds": duration,
        "language": language,
        "meeting_type": (m.get("template") or {}).get("name", "sync") if isinstance(m.get("template"), dict) else "sync",
        "attendees": attendees,
        "summary": summary,
        "action_items": action_items,
        "transcript": transcript_lines,
        "audio_url": None,
    }
