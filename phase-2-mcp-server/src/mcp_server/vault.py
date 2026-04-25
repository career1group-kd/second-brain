"""Safe filesystem access to the Obsidian vault."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class PathTraversalError(ValueError):
    pass


class NoteNotFoundError(FileNotFoundError):
    pass


def safe_join(vault_root: Path, relative: str) -> Path:
    """Resolve a relative path against the vault root, rejecting any escape."""
    if not relative:
        raise PathTraversalError("empty path")
    candidate = (vault_root / relative).resolve()
    root = vault_root.resolve()
    if not str(candidate).startswith(str(root) + "/") and candidate != root:
        raise PathTraversalError(f"path escapes vault: {relative}")
    return candidate


def read_note(vault_root: Path, relative: str) -> dict[str, Any]:
    path = safe_join(vault_root, relative)
    if not path.is_file():
        raise NoteNotFoundError(relative)
    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    return {
        "path": Path(relative).as_posix(),
        "title": path.stem,
        "frontmatter": dict(post.metadata),
        "content": post.content,
    }


def parse_sections(content: str) -> dict[str, str]:
    """Map H2 heading text → section body (excluding the heading line)."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in content.splitlines():
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) == 2:
            if current is not None:
                sections[current] = "\n".join(buffer).strip("\n")
            current = m.group(2).strip()
            buffer = []
            continue
        buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip("\n")
    return sections


def list_files_with_frontmatter(
    vault_root: Path,
    *,
    subdir: str,
    type_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Walk vault_root/subdir and yield notes whose frontmatter matches."""
    base = safe_join(vault_root, subdir)
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in base.rglob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            post = frontmatter.loads(raw)
        except Exception:
            continue
        meta = dict(post.metadata)
        if type_filter and meta.get("type") != type_filter:
            continue
        rel = path.relative_to(vault_root).as_posix()
        out.append(
            {
                "path": rel,
                "title": path.stem,
                "frontmatter": meta,
            }
        )
    return out
