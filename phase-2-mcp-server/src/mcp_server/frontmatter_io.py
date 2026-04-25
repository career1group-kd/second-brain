"""Frontmatter serialization helpers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import frontmatter
import yaml


def _yaml_default(data: Any) -> str:
    return yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def render(meta: dict[str, Any], body: str) -> bytes:
    """Render frontmatter + body to bytes (newline at end)."""
    post = frontmatter.Post(body, **meta)
    text = frontmatter.dumps(post, handler=frontmatter.YAMLHandler())
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def parse_bytes(raw: bytes) -> tuple[dict[str, Any], str]:
    post = frontmatter.loads(raw.decode("utf-8"))
    return dict(post.metadata), post.content


def merge_meta(
    existing: dict[str, Any],
    updates: dict[str, Any],
    *,
    list_merge_fields: tuple[str, ...] = ("tags",),
) -> dict[str, Any]:
    out = dict(existing)
    for k, v in updates.items():
        if k in list_merge_fields and isinstance(v, list):
            current = out.get(k) or []
            if not isinstance(current, list):
                current = [current]
            seen = set()
            merged = []
            for item in [*current, *v]:
                if item in seen:
                    continue
                seen.add(item)
                merged.append(item)
            out[k] = merged
        else:
            out[k] = v
    today = date.today()
    out["updated"] = today
    return out


def normalize_dates(meta: dict[str, Any]) -> dict[str, Any]:
    """Coerce ISO-string dates to date objects for stable serialization."""
    out = dict(meta)
    for k, v in list(out.items()):
        if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
            try:
                out[k] = date.fromisoformat(v)
            except ValueError:
                pass
        elif isinstance(v, datetime):
            out[k] = v.date()
    return out
