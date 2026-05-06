"""Starlette handler for Fireflies webhook delivery.

Webhook v2 sends `{event, timestamp, meeting_id, client_reference_id?}`
and signs the body with `X-Hub-Signature: sha256=<hmac>` when a signing
secret is configured. We verify, fetch the transcript via GraphQL, run
the calendar+summary resolver to enrich speaker data, then render and
write the meeting note (and update People notes).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import frontmatter_io, vault
from ..atomic import atomic_write
from .matcher import match_attendees
from .types import MeetingPayload

if TYPE_CHECKING:
    from ..gcal_client import GoogleCalendarClient
from ..tools._common import ServerContext
from ..tools.vault_read import list_active_projects
from ..tools.vault_write import append_to_person, update_person_meta
from .api import FirefliesError, fetch_transcript, to_meeting_payload
from .renderer import render_meeting
from .resolver import resolve_meeting

log = structlog.get_logger()


class _ClientError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail


def _existing_meeting_path(vault_root: Path, fireflies_id: str) -> str | None:
    base = vault_root / "50_Daily" / "meetings"
    if not base.is_dir():
        return None
    for path in base.rglob("*.md"):
        try:
            raw = path.read_bytes()
            meta, _ = frontmatter_io.parse_bytes(raw)
        except Exception:
            continue
        if str(meta.get("fireflies_id") or "") == fireflies_id:
            return path.relative_to(vault_root).as_posix()
    return None


def _infer_project(ctx: ServerContext, title: str) -> str | None:
    response = list_active_projects(ctx)
    projects = response.get("results", [])
    matches = []
    title_lower = title.lower()
    for entry in projects:
        name = (entry.get("project") or entry.get("title") or "").strip()
        if name and name.lower() in title_lower:
            matches.append(name)
    if len(matches) == 1:
        return matches[0]
    return None


def _verify_signature(secret: str, raw_body: bytes, signature: str | None) -> bool:
    """Verify Fireflies' `X-Hub-Signature: sha256=<hex>` header.

    Returns True if no secret is configured (signature optional) or the
    signature matches; False on mismatch.
    """
    if not secret:
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature
    if provided.startswith("sha256="):
        provided = provided.split("=", 1)[1]
    return hmac.compare_digest(expected, provided.strip())


def make_handler(
    ctx: ServerContext,
    *,
    calendar: "GoogleCalendarClient | None" = None,
) -> Callable:
    settings = ctx.settings

    async def webhook(request: Request) -> JSONResponse:
        raw_body = await request.body()
        signature = request.headers.get("x-hub-signature") or request.headers.get(
            "X-Hub-Signature"
        )
        if not _verify_signature(
            settings.fireflies_webhook_secret, raw_body, signature
        ):
            log.warning("fireflies_signature_invalid")
            return JSONResponse({"detail": "invalid signature"}, status_code=401)

        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError as e:
            log.warning("fireflies_invalid_json", error=str(e))
            return JSONResponse({"detail": f"invalid json: {e}"}, status_code=400)

        log.info(
            "fireflies_received",
            ff_event=payload.get("event") if isinstance(payload, dict) else None,
            meeting_id=payload.get("meeting_id") if isinstance(payload, dict) else None,
        )

        try:
            result = _process(ctx, settings, calendar, payload)
            return JSONResponse(result, status_code=200)
        except _ClientError as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status)
        except Exception as e:
            log.exception("fireflies_unhandled", error=str(e))
            return JSONResponse(
                {"detail": f"{type(e).__name__}: {e}"}, status_code=500
            )

    return webhook


def _process(
    ctx: ServerContext,
    settings,
    calendar: "GoogleCalendarClient | None",
    payload: dict,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _ClientError(400, "payload must be an object")
    meeting_id = payload.get("meeting_id")
    if not meeting_id:
        raise _ClientError(400, "meeting_id required")

    # Only act on `meeting.summarized`. `meeting.transcribed` fires earlier
    # but the summary isn't ready yet; we'd write a note with no speaker
    # context. Acknowledge other events with 200 so Fireflies stops
    # retrying.
    event = payload.get("event") or ""
    if event and event != "meeting.summarized":
        log.info("fireflies_skip_event", ff_event=event, meeting_id=meeting_id)
        return {"ok": True, "skipped": True, "event": event}

    try:
        transcript = fetch_transcript(settings.fireflies_api_key, meeting_id)
    except FirefliesError as e:
        log.warning("fireflies_fetch_failed", meeting_id=meeting_id, error=str(e))
        raise _ClientError(502, str(e))

    mapped = to_meeting_payload(transcript)
    log.info(
        "fireflies_fetched",
        meeting_id=meeting_id,
        title=mapped.get("title"),
        attendees=len(mapped.get("attendees") or []),
        has_transcript=bool(mapped.get("transcript")),
        has_summary=bool(mapped.get("summary")),
    )

    try:
        meeting = MeetingPayload(**mapped)
    except Exception as e:
        log.warning("fireflies_invalid_payload", error=str(e))
        raise _ClientError(400, str(e))

    resolved = resolve_meeting(meeting, calendar=calendar, raw_transcript=transcript)
    if resolved.title_override:
        meeting = meeting.model_copy(update={"title": resolved.title_override})
    if resolved.attendees:
        meeting = meeting.model_copy(update={"attendees": resolved.attendees})

    log.info(
        "fireflies_resolved",
        meeting_id=meeting_id,
        title=meeting.title,
        attendees_total=len(meeting.attendees),
        speaker_to_name=resolved.speaker_to_name,
        notes=resolved.notes,
    )

    matches = match_attendees(settings.vault_path, meeting.attendees)
    existing_rel = _existing_meeting_path(settings.vault_path, meeting.meeting_id)
    project = _infer_project(ctx, meeting.title)

    rel_path, raw = render_meeting(
        meeting,
        matches,
        project=project,
        relative_path=existing_rel,
        speaker_to_name=resolved.speaker_to_name,
        calendar_event_id=resolved.calendar_event_id,
    )

    # Preserve `created` if we're updating an existing note.
    if existing_rel:
        existing_abs = vault.safe_join(settings.vault_path, existing_rel)
        try:
            old_raw = existing_abs.read_bytes()
            old_meta, _ = frontmatter_io.parse_bytes(old_raw)
            if "created" in old_meta:
                new_meta, body = frontmatter_io.parse_bytes(raw)
                new_meta["created"] = old_meta["created"]
                raw = frontmatter_io.render(new_meta, body)
        except Exception:
            log.exception("fireflies_existing_meta_failed", path=existing_rel)

    absolute = vault.safe_join(settings.vault_path, rel_path)
    atomic_write(absolute, raw)

    for attendee_name, person_path in matches.matched:
        person_title = Path(person_path).stem
        try:
            append_to_person(
                ctx,
                name=person_title,
                section="History",
                content=f"[[{rel_path.removesuffix('.md')}]]",
            )
        except Exception:
            log.exception("person_history_failed", path=person_path)
        try:
            update_person_meta(
                ctx,
                name=person_title,
                fields={"last_interaction": date.today()},
            )
        except Exception:
            log.exception("person_meta_failed", path=person_path)

    log.info(
        "fireflies_processed",
        meeting_id=meeting.meeting_id,
        attendees_total=len(meeting.attendees),
        matched=len(matches.matched),
        unrecognized=len(matches.unrecognized),
        file_path=rel_path,
        replaced=bool(existing_rel),
    )
    return {
        "ok": True,
        "path": rel_path,
        "matched": len(matches.matched),
        "unrecognized": len(matches.unrecognized),
        "replaced": bool(existing_rel),
        "speaker_resolution": resolved.speaker_to_name,
        "calendar_event_id": resolved.calendar_event_id,
    }
