"""Vault read tools: search_notes, get_note, get_living_doc, list_recent, find_related, list_active_projects."""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from .. import sparse, vault
from ..vault import NoteNotFoundError, PathTraversalError, parse_sections
from ._common import ServerContext, excerpt, fuzzy_match_living_doc

log = structlog.get_logger()


def search_notes(
    ctx: ServerContext,
    *,
    query: str,
    top_k: int = 10,
    type: str | None = None,
    project: str | None = None,
    tags: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    attendees: list[str] | None = None,
    status: str | None = "active",
) -> dict[str, Any]:
    """Hybrid search + Voyage rerank, returning the top matches with excerpts."""
    try:
        dense = ctx.voyage.embed_query(query)
    except Exception as e:
        log.exception("embed_query_failed")
        return {"error": str(e), "code": "EMBED_FAILED"}

    sparse_vec = sparse.encode_query(query)

    flt = ctx.index.build_filter(
        type=type,
        project=project,
        status=status,
        tags=tags,
        date_from=date_from,
        date_to=date_to,
        attendees=attendees,
    )

    try:
        candidates = ctx.index.hybrid_search(
            dense=dense,
            sparse=sparse_vec,
            limit=30,
            query_filter=flt,
        )
    except Exception as e:
        log.exception("hybrid_search_failed")
        return {"error": str(e), "code": "SEARCH_FAILED"}

    if not candidates:
        return {"results": []}

    docs = [c["payload"].get("content", "") for c in candidates]
    doc_ids = [str(c["id"]) for c in candidates]

    cached = ctx.rerank_cache.get(query, doc_ids)
    if cached is None:
        try:
            ranked = ctx.voyage.rerank(query, docs, top_k=top_k)
        except Exception as e:
            log.exception("rerank_failed")
            ranked = [(i, candidates[i]["score"]) for i in range(min(top_k, len(candidates)))]
        ctx.rerank_cache.set(query, doc_ids, ranked)
    else:
        ranked = cached

    results = []
    for idx, score in ranked[:top_k]:
        c = candidates[idx]
        p = c["payload"]
        results.append(
            {
                "path": p.get("path"),
                "title": p.get("title"),
                "headings": p.get("headings", []),
                "type": p.get("type"),
                "project": p.get("project"),
                "score": float(score),
                "content_excerpt": excerpt(p.get("content", "")),
            }
        )
    return {"results": results}


def get_note(ctx: ServerContext, *, path: str) -> dict[str, Any]:
    try:
        return vault.read_note(ctx.settings.vault_path, path)
    except NoteNotFoundError:
        return {"error": "note not found", "code": "NOT_FOUND"}
    except PathTraversalError as e:
        return {"error": str(e), "code": "INVALID_PATH"}


def get_living_doc(ctx: ServerContext, *, project: str) -> dict[str, Any]:
    matches = fuzzy_match_living_doc(ctx.settings.vault_path, project)
    if not matches:
        return {"error": "no living doc matches project", "code": "NOT_FOUND"}
    best = matches[0]
    if len(matches) > 1 and matches[1]["score"] >= best["score"] - 5:
        return {
            "candidates": [
                {"path": m["doc"]["path"], "title": m["doc"]["title"], "score": m["score"]}
                for m in matches[:5]
            ]
        }
    note = vault.read_note(ctx.settings.vault_path, best["doc"]["path"])
    note["sections"] = parse_sections(note["content"])
    return note


def list_recent(
    ctx: ServerContext,
    *,
    n: int = 10,
    type: str | None = None,
) -> dict[str, Any]:
    payloads = ctx.index.list_recent_paths(limit=n, type=type)
    return {
        "results": [
            {
                "path": p.get("path"),
                "title": p.get("title"),
                "type": p.get("type"),
                "project": p.get("project"),
                "updated": p.get("updated"),
            }
            for p in payloads
        ]
    }


def find_related(ctx: ServerContext, *, path: str, top_k: int = 5) -> dict[str, Any]:
    vec = ctx.index.first_chunk_vector(path)
    if vec is None:
        return {"error": "no embedding found for path", "code": "NOT_FOUND"}
    flt = ctx.index.build_filter(path_excludes=[path])
    candidates = ctx.index.vector_search(dense=vec, limit=top_k * 4, query_filter=flt)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in candidates:
        p = c["payload"].get("path")
        if not p or p == path or p in seen:
            continue
        seen.add(p)
        out.append(
            {
                "path": p,
                "title": c["payload"].get("title"),
                "score": c["score"],
                "headings": c["payload"].get("headings", []),
            }
        )
        if len(out) >= top_k:
            break
    return {"results": out}


def list_active_projects(ctx: ServerContext) -> dict[str, Any]:
    flt = ctx.index.build_filter(type="living", status="active")
    if flt is None:
        return {"results": []}
    payloads = ctx.index.scroll_filter(flt=flt, limit=512)
    by_path: dict[str, dict[str, Any]] = {}
    for p in payloads:
        path = p.get("path")
        if not path or path in by_path:
            continue
        by_path[path] = {
            "path": path,
            "title": p.get("title"),
            "project": p.get("project"),
            "updated": p.get("updated"),
        }
    return {"results": list(by_path.values())}
