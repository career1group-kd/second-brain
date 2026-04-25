"""Chunker tests."""

from __future__ import annotations

from ingestion.chunker import chunk_note, count_tokens
from ingestion.models import Note, Section


def _note(sections: list[tuple[list[str], str]], title: str = "Test") -> Note:
    return Note(
        relative_path=f"10_Projects/{title}.md",
        title=title,
        frontmatter={"type": "living"},
        sections=[Section(heading_path=hp, body=body) for hp, body in sections],
    )


def test_short_section_yields_one_chunk() -> None:
    note = _note([(["Test", "Status"], "kurzer text")])
    chunks = chunk_note(note)
    assert len(chunks) == 1
    assert chunks[0].chunk_idx == 0
    assert chunks[0].content == "kurzer text"
    assert chunks[0].embed_text.startswith("Test > Status\n\n")


def test_long_section_window_split() -> None:
    long_text = ("token " * 2000).strip()
    note = _note([(["Test", "Body"], long_text)])
    chunks = chunk_note(
        note,
        max_tokens=400,
        window_tokens=300,
        overlap_tokens=50,
    )
    assert len(chunks) > 1
    for c in chunks:
        assert count_tokens(c.content) <= 320  # window + a small slack
    indices = [c.chunk_idx for c in chunks]
    assert indices == list(range(len(chunks)))


def test_empty_section_skipped() -> None:
    note = _note([(["Test", "Empty"], ""), (["Test", "Real"], "content")])
    chunks = chunk_note(note)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ["Test", "Real"]


def test_heading_prefix_includes_path() -> None:
    note = _note([(["Project", "H2", "H3"], "content")])
    chunks = chunk_note(note)
    assert chunks[0].embed_text == "Test > Project > H2 > H3\n\ncontent"


def test_chunk_indices_are_contiguous_across_sections() -> None:
    note = _note(
        [
            (["A"], "alpha"),
            (["B"], "beta"),
            (["C"], "gamma"),
        ]
    )
    chunks = chunk_note(note)
    assert [c.chunk_idx for c in chunks] == [0, 1, 2]
