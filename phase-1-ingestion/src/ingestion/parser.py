"""Markdown parser: frontmatter + heading-aware section split."""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter

from .models import Note, Section

OBSIDIAN_COMMENT_RE = re.compile(r"%%.*?%%", re.DOTALL)
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def strip_obsidian_comments(text: str) -> str:
    return OBSIDIAN_COMMENT_RE.sub("", text)


def split_sections(body: str) -> list[Section]:
    """Split body by H1/H2/H3 headings, ignoring those inside fenced code blocks.

    The first section before any heading carries an empty heading_path.
    """
    lines = body.splitlines()
    sections: list[Section] = []
    # Stack of (level, title); heading_path is just the titles.
    heading_stack: list[tuple[int, str]] = []
    current_path: list[str] = []
    buffer: list[str] = []
    in_fence = False
    fence_marker: str | None = None

    def flush() -> None:
        if buffer or current_path:
            content = "\n".join(buffer).strip("\n")
            sections.append(Section(heading_path=list(current_path), body=content))

    for line in lines:
        stripped = line.lstrip()
        fence_match = FENCE_RE.match(stripped)
        if fence_match:
            marker = fence_match.group(1)[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif fence_marker and stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = None
            buffer.append(line)
            continue

        if not in_fence:
            heading_match = HEADING_RE.match(line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                if level <= 3:
                    flush()
                    buffer = []
                    while heading_stack and heading_stack[-1][0] >= level:
                        heading_stack.pop()
                    heading_stack.append((level, title))
                    current_path = [t for _, t in heading_stack]
                    continue

        buffer.append(line)

    flush()
    # Drop a leading empty pre-heading section if it carries no content.
    if sections and not sections[0].heading_path and not sections[0].body.strip():
        sections = sections[1:]
    return sections


def parse_note(absolute_path: Path, vault_root: Path) -> Note:
    raw = absolute_path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    body = strip_obsidian_comments(post.content)

    relative_path = absolute_path.relative_to(vault_root).as_posix()
    title = absolute_path.stem
    fm = dict(post.metadata)

    sections = split_sections(body)
    return Note(
        relative_path=relative_path,
        title=title,
        frontmatter=fm,
        sections=sections,
    )
