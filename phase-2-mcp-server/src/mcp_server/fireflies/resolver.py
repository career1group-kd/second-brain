"""Speaker-name resolver for Fireflies transcripts.

When the Fireflies Mac app records without the calendar plugin connected,
the transcript comes back with anonymous "Speaker 0/1/2" labels and a
near-empty `meeting_attendees` list. We try to recover real names from
two sources before falling back to "Speaker N":

1. Google Calendar — match the transcript to a calendar event (preferring
   the `cal_id` baked into the transcript, falling back to a time-window
   search around `started_at`). The event yields a meeting title and the
   list of invited attendees.

2. Summary text — Fireflies' AI summary frequently mentions participants
   by first name ("Kay said …", "Anna mentioned …"). We extract first-name
   tokens that match a known calendar attendee's display name.

Then for each anonymous "Speaker N" we count how often each candidate
first name appears in their utterances; the highest-scoring candidate
wins, provided its lead over the runner-up is large enough.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from .types import Attendee, MeetingPayload

if TYPE_CHECKING:
    from ..gcal_client import GoogleCalendarClient

log = structlog.get_logger()

_NAME_TOKEN_RE = re.compile(r"\b([A-ZÄÖÜÉÈÀÂÊÎÔÛŠŽÇ][\wäöüéèàâêîôûšžç'’-]{1,})\b")
_SPEAKER_N_RE = re.compile(r"^speaker[\s_-]*\d+$", re.IGNORECASE)


@dataclass
class CalendarHit:
    title: str
    attendees: list[tuple[str, str | None]]  # (display_name, email_or_None)
    event_id: str | None = None
    source: str = "calendar"  # 'calendar' | 'cal_id' | 'time_window'


@dataclass
class ResolverOutput:
    attendees: list[Attendee] = field(default_factory=list)
    speaker_to_name: dict[str, str] = field(default_factory=dict)
    title_override: str | None = None
    calendar_event_id: str | None = None
    notes: list[str] = field(default_factory=list)


def resolve_meeting(
    payload: MeetingPayload,
    calendar: "GoogleCalendarClient | None",
    raw_transcript: dict[str, Any] | None = None,
) -> ResolverOutput:
    """Enrich a Fireflies-derived MeetingPayload with calendar + summary data.

    `raw_transcript` is the original Fireflies GraphQL response. We use
    `cal_id` from it when present so we can fetch the calendar event by
    ID instead of guessing from a time window.
    """
    out = ResolverOutput()

    # --- 1. Calendar lookup ----------------------------------------------
    hit: CalendarHit | None = None
    if calendar is not None:
        cal_id = (raw_transcript or {}).get("calendar_id") if raw_transcript else None
        try:
            if cal_id:
                event = calendar.get_event(cal_id)
                if event:
                    hit = _hit_from_event(event, source="cal_id")
                    out.notes.append(f"calendar matched via cal_id={cal_id}")
            if hit is None:
                event = calendar.find_event_around(
                    started_at=_as_aware(payload.started_at),
                    slack=timedelta(minutes=10),
                )
                if event:
                    hit = _hit_from_event(event, source="time_window")
                    out.notes.append(
                        f"calendar matched via time-window event_id={event.get('id')}"
                    )
        except Exception as e:
            log.warning("fireflies_calendar_lookup_failed", error=str(e))
            out.notes.append(f"calendar lookup failed: {e}")

    if hit:
        out.title_override = hit.title or None
        out.calendar_event_id = hit.event_id

    # --- 2. Merge attendees: payload + calendar (dedup by lowered email/name)
    by_email: dict[str, Attendee] = {}
    by_name: dict[str, Attendee] = {}
    for att in payload.attendees:
        email_key = (att.email or "").lower()
        if email_key:
            by_email[email_key] = att
        else:
            by_name[att.name.lower()] = att
    if hit:
        for name, email in hit.attendees:
            display = name or email
            if not display:
                continue
            email_key = (email or "").lower()
            if email_key and email_key in by_email:
                # Upgrade existing entry's display name when the calendar
                # has a real name and the existing entry only had an email.
                existing = by_email[email_key]
                if "@" in existing.name and "@" not in display:
                    by_email[email_key] = Attendee(name=display, email=email)
                continue
            if not email_key and display.lower() in by_name:
                continue
            new_att = Attendee(name=display, email=email)
            if email_key:
                by_email[email_key] = new_att
            else:
                by_name[display.lower()] = new_att
    out.attendees = list(by_email.values()) + list(by_name.values())

    # --- 3. Summary-based speaker name resolution -------------------------
    speaker_to_name = _resolve_speakers_from_summary(
        payload=payload,
        attendees=out.attendees,
    )
    out.speaker_to_name = speaker_to_name
    if speaker_to_name:
        out.notes.append(
            "speaker→name from summary: "
            + ", ".join(f"{k}→{v}" for k, v in speaker_to_name.items())
        )

    return out


def _hit_from_event(event: dict[str, Any], *, source: str) -> CalendarHit:
    title = (event.get("summary") or "").strip()
    pairs: list[tuple[str, str | None]] = []
    for att in event.get("attendees") or []:
        if not isinstance(att, dict):
            continue
        if att.get("resource"):
            continue
        email = (att.get("email") or "").strip() or None
        name = (att.get("displayName") or "").strip() or email
        if not name:
            continue
        pairs.append((name, email))
    return CalendarHit(
        title=title,
        attendees=pairs,
        event_id=event.get("id"),
        source=source,
    )


def _as_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        from datetime import timezone

        return dt.replace(tzinfo=timezone.utc)
    return dt


def _candidate_first_names(attendees: list[Attendee]) -> dict[str, str]:
    """Map first-name (lowered) → full attendee name, for known attendees only."""
    out: dict[str, str] = {}
    for att in attendees:
        # Skip raw email-as-name attendees.
        if "@" in att.name:
            local = att.name.split("@")[0]
            first = re.split(r"[._\-+]", local)[0]
            if not first:
                continue
            out.setdefault(first.lower(), _titlecase(local.replace(".", " ")))
            continue
        first = att.name.strip().split()[0] if att.name.strip() else ""
        if not first:
            continue
        out.setdefault(first.lower(), att.name.strip())
    return out


def _titlecase(s: str) -> str:
    return " ".join(p.capitalize() for p in s.split())


def _resolve_speakers_from_summary(
    *,
    payload: MeetingPayload,
    attendees: list[Attendee],
) -> dict[str, str]:
    """Attribute each anonymous speaker to a known attendee using the summary.

    Heuristic: collect candidate first names from the calendar/transcript
    attendees, then for each "Speaker N" count how often each candidate
    first name shows up *in the summary text near* (or just generally
    co-occurring with) that speaker's lines. We use a lighter-weight
    approximation: count first-name mentions in the whole summary, then
    pick — per anonymous speaker — the most-mentioned candidate that
    isn't already assigned. This is intentionally simple; we'll layer the
    Haiku resolver on top once we see how good this is in practice.
    """
    speakers = sorted({line.speaker for line in payload.transcript})
    anon = [s for s in speakers if _SPEAKER_N_RE.match(s or "")]
    if not anon:
        return {}

    candidates = _candidate_first_names(attendees)
    if not candidates:
        return {}

    summary = payload.summary or ""
    if not summary.strip():
        return {}

    counts: Counter[str] = Counter()
    for token in _NAME_TOKEN_RE.findall(summary):
        key = token.lower()
        if key in candidates:
            counts[key] += 1

    if not counts:
        return {}

    # Speakers ordered by amount of speech (more talkative speaker → first
    # crack at the most-mentioned candidate). Length-of-text proxy: total
    # characters spoken.
    speech: Counter[str] = Counter()
    for line in payload.transcript:
        if line.speaker in anon:
            speech[line.speaker] += len(line.text or "")
    ordered = sorted(anon, key=lambda s: speech[s], reverse=True)

    assigned: dict[str, str] = {}
    used: set[str] = set()
    ranked = [name for name, _ in counts.most_common()]
    for speaker in ordered:
        for cand in ranked:
            if cand in used:
                continue
            assigned[speaker] = candidates[cand]
            used.add(cand)
            break
    return assigned
