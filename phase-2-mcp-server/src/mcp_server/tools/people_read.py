"""People read tools: get_person, find_person, list_recent_interactions, list_people_by_company."""

from __future__ import annotations

from typing import Any

import structlog

from .. import sparse, vault
from ._common import ServerContext, excerpt, fuzzy_match_person

log = structlog.get_logger()


def get_person(ctx: ServerContext, *, name_or_email: str) -> dict[str, Any]:
    matches = fuzzy_match_person(ctx.settings.vault_path, name_or_email)
    if not matches:
        return {"error": "no person matches", "code": "NOT_FOUND"}
    best = matches[0]
    if len(matches) > 1 and matches[1]["score"] >= best["score"] - 5:
        return {
            "candidates": [
                {
                    "path": m["doc"]["path"],
                    "title": m["doc"]["title"],
                    "email": m["doc"]["frontmatter"].get("email"),
                    "score": m["score"],
                }
                for m in matches[:5]
            ]
        }
    note = vault.read_note(ctx.settings.vault_path, best["doc"]["path"])
    return note


def find_person(ctx: ServerContext, *, query: str) -> dict[str, Any]:
    try:
        dense = ctx.voyage.embed_query(query)
    except Exception as e:
        log.exception("embed_query_failed")
        return {"error": str(e), "code": "EMBED_FAILED"}
    sparse_vec = sparse.encode_query(query)
    flt = ctx.index.build_filter(type="person")
    results = ctx.index.hybrid_search(
        dense=dense,
        sparse=sparse_vec,
        limit=5,
        query_filter=flt,
    )
    return {
        "results": [
            {
                "path": r["payload"].get("path"),
                "title": r["payload"].get("title"),
                "score": r["score"],
                "content_excerpt": excerpt(r["payload"].get("content", "")),
            }
            for r in results
        ]
    }


def list_recent_interactions(
    ctx: ServerContext,
    *,
    name: str,
    n: int = 5,
) -> dict[str, Any]:
    person = get_person(ctx, name_or_email=name)
    if "error" in person or "candidates" in person:
        return person
    person_path = person["path"]
    title = person["title"]
    wikilink_variants = [
        f"[[{person_path.removesuffix('.md')}]]",
        f"[[70_People/{title}]]",
        f"[[{title}]]",
    ]
    seen: dict[str, dict[str, Any]] = {}
    for variant in wikilink_variants:
        flt = ctx.index.build_filter(type="meeting", attendees=[variant])
        if flt is None:
            continue
        payloads = ctx.index.scroll_filter(flt=flt, limit=64)
        for p in payloads:
            path = p.get("path")
            if not path or path in seen:
                continue
            seen[path] = {
                "path": path,
                "title": p.get("title"),
                "updated": p.get("updated"),
            }
    items = sorted(seen.values(), key=lambda x: x.get("updated") or "", reverse=True)
    return {"results": items[:n]}


def list_people_by_company(ctx: ServerContext, *, company: str) -> dict[str, Any]:
    """Walks 70_People filesystem-side; company isn't reliably indexed in payload."""
    docs = vault.list_files_with_frontmatter(
        ctx.settings.vault_path, subdir="70_People", type_filter="person"
    )
    needle = company.strip().lower()
    out = []
    for d in docs:
        c = (d["frontmatter"].get("company") or "").lower()
        if needle in c:
            out.append(
                {
                    "path": d["path"],
                    "name": d["title"],
                    "role": d["frontmatter"].get("role"),
                    "email": d["frontmatter"].get("email"),
                    "last_interaction": d["frontmatter"].get("last_interaction"),
                }
            )
    return {"results": out}
