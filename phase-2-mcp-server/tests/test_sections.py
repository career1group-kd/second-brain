"""Section detection + append tests."""

from __future__ import annotations

from mcp_server.sections import append_to_section, find_section, find_sections

DOC = b"""# Title

## Status

state body

## TODOs

- [ ] one

## History

- 2026-04-25: stuff
"""


def test_find_sections_basic() -> None:
    s = find_sections(DOC)
    names = [x.name for x in s]
    assert names == ["Status", "TODOs", "History"]


def test_find_section_case_insensitive() -> None:
    s = find_section(DOC, "todos")
    assert s is not None
    assert s.name == "TODOs"


def test_find_section_missing_returns_none() -> None:
    assert find_section(DOC, "Missing") is None


def test_append_to_section_existing() -> None:
    out = append_to_section(DOC, "TODOs", "- [ ] two\n")
    assert b"- [ ] one\n- [ ] two" in out
    # Ensure History is preserved.
    assert b"## History" in out


def test_append_to_section_creates_when_missing() -> None:
    out = append_to_section(DOC, "Recent Insights", "- 2026-04-25: ping\n")
    assert b"## Recent Insights" in out
    assert b"- 2026-04-25: ping" in out


def test_append_preserves_h1() -> None:
    out = append_to_section(DOC, "Status", "added\n")
    assert out.startswith(b"# Title")
