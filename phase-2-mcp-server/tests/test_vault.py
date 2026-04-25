"""Vault filesystem helpers tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.vault import (
    NoteNotFoundError,
    PathTraversalError,
    list_files_with_frontmatter,
    parse_sections,
    read_note,
    safe_join,
)


def test_safe_join_inside_root(fixture_vault: Path) -> None:
    out = safe_join(fixture_vault, "10_Projects/ChapterNext.md")
    assert out.is_file()


def test_safe_join_rejects_traversal(fixture_vault: Path) -> None:
    with pytest.raises(PathTraversalError):
        safe_join(fixture_vault, "../etc/passwd")


def test_safe_join_rejects_absolute_escape(fixture_vault: Path) -> None:
    with pytest.raises(PathTraversalError):
        safe_join(fixture_vault, "/etc/passwd")


def test_read_note(fixture_vault: Path) -> None:
    note = read_note(fixture_vault, "10_Projects/ChapterNext.md")
    assert note["frontmatter"]["type"] == "living"
    assert note["frontmatter"]["project"] == "ChapterNext"
    assert "Manuskript" in note["content"]


def test_read_note_missing(fixture_vault: Path) -> None:
    with pytest.raises(NoteNotFoundError):
        read_note(fixture_vault, "10_Projects/Nope.md")


def test_parse_sections() -> None:
    body = "## A\nbody A\n## B\nbody B\n"
    out = parse_sections(body)
    assert out == {"A": "body A", "B": "body B"}


def test_list_files_with_frontmatter(fixture_vault: Path) -> None:
    docs = list_files_with_frontmatter(
        fixture_vault, subdir="70_People", type_filter="person"
    )
    titles = sorted(d["title"] for d in docs)
    assert titles == ["Anna Schmidt", "John Doe"]
