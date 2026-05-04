"""Minimal MeetGeek REST client.

The webhook only delivers a notification (`meeting_id` + `message`); the
actual meeting payload has to be pulled from MeetGeek's API.

Endpoints used (all GET, Bearer auth):
- /v1/meetings/{id}            — metadata (title, times, host, participants)
- /v1/meetings/{id}/transcript — sentences[]
- /v1/meetings/{id}/highlights — highlights[] with label="Task" → action items
- /v1/meetings/{id}/summary    — { summary, ai_insights }
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

# EU region — matches the `eu-` prefix on Kay's API key. Override via
# MEETGEEK_API_BASE if a different region is needed.
DEFAULT_BASE_URL = "https://api-eu.meetgeek.ai/v1"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MeetGeekError(Exception):
    pass


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _unwrap(data: Any) -> dict[str, Any] | None:
    """Some MeetGeek endpoints return a single object as a one-element list."""
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def _get(client: httpx.Client, base_url: str, path: str, token: str) -> Any:
    resp = client.get(
        f"{base_url}{path}", headers=_headers(token), timeout=TIMEOUT
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_meeting_bundle(
    token: str, meeting_id: str, base_url: str = DEFAULT_BASE_URL
) -> dict[str, Any]:
    """Fetch metadata + transcript / highlights / summary.

    Only `meeting` is required; the rest are None on error so a partial
    note is still better than nothing.
    """
    if not token:
        raise MeetGeekError("MEETGEEK_API_TOKEN not configured")

    with httpx.Client() as client:
        meeting_raw = _get(client, base_url, f"/meetings/{meeting_id}", token)
        meeting = _unwrap(meeting_raw)
        if meeting is None:
            raise MeetGeekError(f"meeting {meeting_id} not found")

        out: dict[str, Any] = {
            "meeting": meeting,
            "transcript": None,
            "highlights": None,
            "summary": None,
        }

        for key, path in (
            ("transcript", f"/meetings/{meeting_id}/transcript"),
            ("highlights", f"/meetings/{meeting_id}/highlights"),
            ("summary", f"/meetings/{meeting_id}/summary"),
        ):
            try:
                out[key] = _get(client, base_url, path, token)
            except httpx.HTTPError as e:
                log.info(
                    "meetgeek_api_optional_failed",
                    endpoint=path,
                    error=str(e),
                )

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

    # Summary endpoint: { summary, ai_insights }.
    summary = ""
    summary_obj = bundle.get("summary")
    if isinstance(summary_obj, dict):
        parts = []
        if summary_obj.get("summary"):
            parts.append(summary_obj["summary"])
        if summary_obj.get("ai_insights"):
            parts.append(f"### AI Insights\n\n{summary_obj['ai_insights']}")
        summary = "\n\n".join(parts)

    # Highlights endpoint: { highlights: [{highlightText, label}] }. Items
    # labelled "Task" are action items; everything else gets folded into
    # the summary as a bullet list under "Highlights".
    action_items: list[str] = []
    extra_highlights: list[str] = []
    highlights_obj = bundle.get("highlights")
    if isinstance(highlights_obj, dict):
        for h in highlights_obj.get("highlights") or []:
            if not isinstance(h, dict):
                continue
            text = h.get("highlightText") or h.get("text") or ""
            if not text:
                continue
            if (h.get("label") or "").lower() == "task":
                action_items.append(text)
            else:
                extra_highlights.append(text)
    if extra_highlights:
        bullets = "\n".join(f"- {h}" for h in extra_highlights)
        summary = (summary + "\n\n### Highlights\n\n" + bullets).strip()

    # Transcript endpoint: { sentences: [{speaker, timestamp, transcript}] }.
    transcript_lines: list[dict[str, Any]] = []
    transcript_obj = bundle.get("transcript")
    if isinstance(transcript_obj, dict):
        for s in transcript_obj.get("sentences") or []:
            if not isinstance(s, dict):
                continue
            transcript_lines.append(
                {
                    "speaker": s.get("speaker") or "Unknown",
                    "timestamp": s.get("timestamp"),
                    "text": s.get("transcript") or "",
                }
            )

    template = m.get("template")
    meeting_type = (
        template.get("name") if isinstance(template, dict) else None
    ) or "sync"

    return {
        "meeting_id": m["meeting_id"],
        "title": m.get("title") or "Untitled meeting",
        "started_at": started_at or datetime.utcnow().isoformat(),
        "ended_at": ended_at,
        "duration_seconds": duration,
        "language": language,
        "meeting_type": meeting_type,
        "attendees": attendees,
        "summary": summary,
        "action_items": action_items,
        "transcript": transcript_lines,
        "audio_url": None,
    }
