"""Shared helpers for tool implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from rapidfuzz import fuzz, process

from ..config import Settings
from ..qdrant_client import VaultIndex
from ..rerank_cache import RerankCache
from ..vault import list_files_with_frontmatter
from ..voyage import VoyageClient

log = structlog.get_logger()


@dataclass
class ServerContext:
    settings: Settings
    index: VaultIndex
    voyage: VoyageClient
    rerank_cache: RerankCache


def excerpt(content: str, max_chars: int = 320) -> str:
    text = content.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


def fuzzy_match_living_doc(
    vault_root: Path,
    project: str,
    threshold: int = 75,
) -> list[dict[str, Any]]:
    docs = list_files_with_frontmatter(vault_root, subdir="10_Projects", type_filter="living")
    if not docs:
        return []
    titles = [d["title"] for d in docs]
    project_names = [d["frontmatter"].get("project") or d["title"] for d in docs]

    candidates: list[tuple[dict[str, Any], int]] = []
    for d, name in zip(docs, project_names):
        score = max(
            fuzz.WRatio(project, name),
            fuzz.WRatio(project, d["title"]),
        )
        if score >= threshold:
            candidates.append((d, int(score)))
    candidates.sort(key=lambda t: -t[1])
    return [{"doc": d, "score": s} for d, s in candidates]


def fuzzy_match_person(
    vault_root: Path,
    name_or_email: str,
    threshold: int = 80,
) -> list[dict[str, Any]]:
    docs = list_files_with_frontmatter(vault_root, subdir="70_People", type_filter="person")
    if not docs:
        return []
    if "@" in name_or_email:
        email_lower = name_or_email.strip().lower()
        return [
            {"doc": d, "score": 100}
            for d in docs
            if (d["frontmatter"].get("email") or "").lower() == email_lower
        ]

    candidates: list[tuple[dict[str, Any], int]] = []
    for d in docs:
        score = fuzz.WRatio(name_or_email, d["title"])
        if score >= threshold:
            candidates.append((d, int(score)))
    candidates.sort(key=lambda t: -t[1])
    return [{"doc": d, "score": s} for d, s in candidates]
