"""FastAPI router for MeetGeek webhook delivery."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from .. import frontmatter_io, vault
from ..atomic import atomic_write
from ..tools._common import ServerContext
from ..tools.vault_read import list_active_projects
from ..tools.vault_write import append_to_person, update_person_meta
from .api import MeetGeekError, fetch_meeting_bundle, to_meeting_payload
from .matcher import match_attendees
from .renderer import render_meeting
from .types import MeetingPayload

log = structlog.get_logger()

router = APIRouter(prefix="/meetgeek", tags=["meetgeek"])


def _existing_meeting_path(vault_root: Path, meetgeek_id: str) -> str | None:
    """Walk 50_Daily/meetings looking for a note with matching meetgeek_id."""
    base = vault_root / "50_Daily" / "meetings"
    if not base.is_dir():
        return None
    for path in base.rglob("*.md"):
        try:
            raw = path.read_bytes()
            meta, _ = frontmatter_io.parse_bytes(raw)
        except Exception:
            continue
        if str(meta.get("meetgeek_id") or "") == meetgeek_id:
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


def make_router(ctx: ServerContext) -> APIRouter:
    settings = ctx.settings

    @router.post("/webhook")
    async def webhook(payload: dict) -> dict[str, Any]:
        log.info(
            "meetgeek_received",
            keys=sorted(payload.keys()) if isinstance(payload, dict) else None,
            payload_type=type(payload).__name__,
        )
        try:
            return _process(ctx, settings, payload)
        except HTTPException:
            raise
        except Exception as e:
            log.exception("meetgeek_unhandled", error=str(e))
            raise HTTPException(
                status_code=500, detail=f"{type(e).__name__}: {e}"
            )

    return router


def _process(ctx: ServerContext, settings, payload: dict) -> dict[str, Any]:
    meeting_id = payload.get("meeting_id") if isinstance(payload, dict) else None
    if not meeting_id:
        raise HTTPException(status_code=400, detail="meeting_id required")

    # MeetGeek delivers a notification only — fetch the actual meeting via API.
    try:
        bundle = fetch_meeting_bundle(settings.meetgeek_api_token, meeting_id)
    except MeetGeekError as e:
        log.warning("meetgeek_fetch_failed", meeting_id=meeting_id, error=str(e))
        raise HTTPException(status_code=502, detail=str(e))

    mapped = to_meeting_payload(bundle)
    log.info(
        "meetgeek_fetched",
        meeting_id=meeting_id,
        title=mapped.get("title"),
        attendees=len(mapped.get("attendees") or []),
        has_transcript=bool(mapped.get("transcript")),
        has_summary=bool(mapped.get("summary")),
    )

    try:
        meeting = MeetingPayload(**mapped)
    except Exception as e:
        log.warning("meetgeek_invalid_payload", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    if not meeting.attendees:
        # Notifications without attendees still get a stub note — better than
        # losing the trigger entirely.
        log.info("meetgeek_no_attendees", meeting_id=meeting_id)

    matches = match_attendees(settings.vault_path, meeting.attendees)

    existing_rel = _existing_meeting_path(settings.vault_path, meeting.meeting_id)
    project = _infer_project(ctx, meeting.title)

    rel_path, raw = render_meeting(
        meeting,
        matches,
        project=project,
        relative_path=existing_rel,
    )

    # Preserve `created` for in-place updates.
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
            log.exception("meetgeek_existing_meta_failed", path=existing_rel)

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
        "meetgeek_processed",
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
    }
