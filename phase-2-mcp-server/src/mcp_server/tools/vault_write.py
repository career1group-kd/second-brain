"""Vault write tools (Phase 3)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import structlog
from slugify import slugify

from .. import frontmatter_io, sections
from ..atomic import ConflictError, file_lock, safe_overwrite
from ..schemas import validate_frontmatter
from ..vault import PathTraversalError, safe_join
from ._common import ServerContext, fuzzy_match_living_doc, fuzzy_match_person

log = structlog.get_logger()


def _format_entry(content: str) -> str:
    today = date.today().isoformat()
    if "\n" in content.strip():
        return content.rstrip() + "\n"
    return f"- {today}: {content.strip()}\n"


def _read_with_meta(path: Path) -> tuple[bytes, int]:
    raw = path.read_bytes()
    return raw, path.stat().st_mtime_ns


def _rewrite(path: Path, raw: bytes, captured_mtime: int) -> None:
    with file_lock(path):
        safe_overwrite(path, raw, captured_mtime_ns=captured_mtime)


def _locked_modify(path: Path, transform) -> dict[str, Any]:
    """Run `transform(raw_bytes) -> new_bytes` under a fcntl lock.

    Re-reads the file inside the lock so concurrent callers serialize cleanly.
    Returns {"ok": True, "bytes_added": int}.
    """
    from ..atomic import atomic_write, file_lock as _file_lock

    if not path.exists():
        return {"error": "note not found", "code": "NOT_FOUND"}
    with _file_lock(path):
        old = path.read_bytes()
        new = transform(old)
        atomic_write(path, new)
    return {"ok": True, "bytes_added": len(new) - len(old)}


def _bump_updated(meta: dict[str, Any]) -> dict[str, Any]:
    out = dict(meta)
    out["updated"] = date.today()
    return out


def append_to_living_doc(
    ctx: ServerContext,
    *,
    project: str,
    section: str,
    content: str,
) -> dict[str, Any]:
    matches = fuzzy_match_living_doc(ctx.settings.vault_path, project)
    if not matches:
        return {"error": "no living doc matches project", "code": "NOT_FOUND"}
    best = matches[0]
    rel_path = best["doc"]["path"]
    abs_path = safe_join(ctx.settings.vault_path, rel_path)

    def transform(raw: bytes) -> bytes:
        meta, body = frontmatter_io.parse_bytes(raw)
        body_bytes = body.encode("utf-8")
        new_body = sections.append_to_section(
            body_bytes, section, _format_entry(content)
        )
        new_meta = _bump_updated(frontmatter_io.normalize_dates(meta))
        return frontmatter_io.render(new_meta, new_body.decode("utf-8"))

    result = _locked_modify(abs_path, transform)
    if "ok" in result:
        log.info(
            "living_doc_appended",
            path=rel_path,
            section=section,
            bytes_added=result.get("bytes_added", 0),
        )
        return {"ok": True, "path": rel_path, "section": section}
    return result


def update_section(
    ctx: ServerContext,
    *,
    path: str,
    section: str,
    content: str,
) -> dict[str, Any]:
    try:
        abs_path = safe_join(ctx.settings.vault_path, path)
    except PathTraversalError as e:
        return {"error": str(e), "code": "INVALID_PATH"}
    if not abs_path.is_file():
        return {"error": "note not found", "code": "NOT_FOUND"}

    def transform(raw: bytes) -> bytes:
        meta, body = frontmatter_io.parse_bytes(raw)
        body_bytes = body.encode("utf-8")
        found = sections.find_section(body_bytes, section)
        if found is None:
            new_body = sections.append_to_section(
                body_bytes, section, content + "\n"
            )
        else:
            new_body = (
                body_bytes[: found.body_start]
                + content.rstrip("\n").encode("utf-8")
                + b"\n"
                + body_bytes[found.body_end :]
            )
        new_meta = _bump_updated(frontmatter_io.normalize_dates(meta))
        return frontmatter_io.render(new_meta, new_body.decode("utf-8"))

    result = _locked_modify(abs_path, transform)
    if "ok" in result:
        return {"ok": True, "path": path, "section": section}
    return result


def create_note(
    ctx: ServerContext,
    *,
    path: str,
    frontmatter_data: dict,
    content: str = "",
    force: bool = False,
) -> dict[str, Any]:
    try:
        abs_path = safe_join(ctx.settings.vault_path, path)
    except PathTraversalError as e:
        return {"error": str(e), "code": "INVALID_PATH"}
    if abs_path.exists() and not force:
        return {"error": "note already exists", "code": "EXISTS"}

    type_ = frontmatter_data.get("type")
    if not type_:
        return {"error": "frontmatter.type is required", "code": "INVALID_FRONTMATTER"}
    try:
        validated = validate_frontmatter(type_, frontmatter_data)
    except Exception as e:
        return {"error": str(e), "code": "INVALID_FRONTMATTER"}

    raw = frontmatter_io.render(frontmatter_io.normalize_dates(validated), content)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(raw)
    return {"ok": True, "path": path}


def append_to_person(
    ctx: ServerContext,
    *,
    name: str,
    section: str,
    content: str,
) -> dict[str, Any]:
    matches = fuzzy_match_person(ctx.settings.vault_path, name)
    if not matches:
        return {"error": "no person matches", "code": "NOT_FOUND"}
    rel_path = matches[0]["doc"]["path"]
    abs_path = safe_join(ctx.settings.vault_path, rel_path)

    def transform(raw: bytes) -> bytes:
        meta, body = frontmatter_io.parse_bytes(raw)
        body_bytes = body.encode("utf-8")
        new_body = sections.append_to_section(
            body_bytes, section, _format_entry(content)
        )
        new_meta = _bump_updated(frontmatter_io.normalize_dates(meta))
        new_meta["last_interaction"] = date.today()
        return frontmatter_io.render(new_meta, new_body.decode("utf-8"))

    result = _locked_modify(abs_path, transform)
    if "ok" in result:
        return {"ok": True, "path": rel_path, "section": section}
    return result


def update_person_meta(
    ctx: ServerContext,
    *,
    name: str,
    fields: dict,
) -> dict[str, Any]:
    matches = fuzzy_match_person(ctx.settings.vault_path, name)
    if not matches:
        return {"error": "no person matches", "code": "NOT_FOUND"}
    rel_path = matches[0]["doc"]["path"]
    abs_path = safe_join(ctx.settings.vault_path, rel_path)

    def transform(raw: bytes) -> bytes:
        meta, body = frontmatter_io.parse_bytes(raw)
        new_meta = frontmatter_io.merge_meta(
            frontmatter_io.normalize_dates(meta), fields
        )
        return frontmatter_io.render(new_meta, body)

    result = _locked_modify(abs_path, transform)
    if "ok" in result:
        return {"ok": True, "path": rel_path}
    return result


def create_person(
    ctx: ServerContext,
    *,
    name: str,
    frontmatter_data: dict | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    fm = dict(frontmatter_data or {})
    fm.setdefault("type", "person")
    today = date.today()
    fm.setdefault("last_interaction", today)
    fm.setdefault("created", today)
    fm.setdefault("updated", today)

    title_safe = name.strip()
    filename = f"{title_safe}.md"
    rel_path = f"70_People/{filename}"
    body = content or f"# {title_safe}\n\n## Kontext\n\n## History\n"
    return create_note(
        ctx,
        path=rel_path,
        frontmatter_data=fm,
        content=body,
        force=False,
    )
