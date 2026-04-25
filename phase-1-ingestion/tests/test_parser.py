"""Parser tests."""

from __future__ import annotations

from pathlib import Path

from ingestion.parser import parse_note, split_sections, strip_obsidian_comments

FIXTURES = Path(__file__).parent / "fixtures" / "sample-vault"


def test_parse_living_doc_frontmatter() -> None:
    note = parse_note(FIXTURES / "10_Projects" / "ChapterNext.md", FIXTURES)
    assert note.title == "ChapterNext"
    assert note.relative_path == "10_Projects/ChapterNext.md"
    assert note.frontmatter["type"] == "living"
    assert note.frontmatter["project"] == "ChapterNext"
    assert note.frontmatter["status"] == "active"
    assert note.frontmatter["tags"] == ["book", "writing"]


def test_parse_section_split() -> None:
    note = parse_note(FIXTURES / "10_Projects" / "ChapterNext.md", FIXTURES)
    headings = [s.heading_path for s in note.sections]
    # First H1 followed by four H2 sections.
    assert ["ChapterNext"] in headings
    assert ["ChapterNext", "Status & Kontext"] in headings
    assert ["ChapterNext", "Architektur & Entscheidungen"] in headings
    assert ["ChapterNext", "Recent Insights"] in headings
    assert ["ChapterNext", "TODOs"] in headings


def test_obsidian_comments_stripped() -> None:
    note = parse_note(FIXTURES / "10_Projects" / "ChapterNext.md", FIXTURES)
    full = "\n".join(s.body for s in note.sections)
    assert "obsidian-only comment" not in full


def test_strip_obsidian_comments() -> None:
    text = "Before %% private %% after"
    assert strip_obsidian_comments(text) == "Before  after"


def test_code_block_immune_to_heading_split() -> None:
    body = "## Real heading\nbody1\n```\n# fake heading inside fence\n```\nstill body1\n## Second\nbody2"
    sections = split_sections(body)
    bodies = {tuple(s.heading_path): s.body for s in sections}
    assert "fake heading inside fence" in bodies[("Real heading",)]
    assert "still body1" in bodies[("Real heading",)]
    assert "body2" in bodies[("Second",)]


def test_split_sections_drops_empty_preamble() -> None:
    sections = split_sections("\n\n## A\nbody")
    assert len(sections) == 1
    assert sections[0].heading_path == ["A"]


def test_split_sections_preserves_h3_under_h2() -> None:
    body = "## H2\nbefore\n### H3\nafter"
    sections = split_sections(body)
    paths = [s.heading_path for s in sections]
    assert ["H2"] in paths
    assert ["H2", "H3"] in paths
