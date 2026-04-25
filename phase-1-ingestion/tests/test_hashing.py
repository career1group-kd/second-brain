"""Hashing tests."""

from __future__ import annotations

from ingestion.hashing import chunk_hash, chunk_id


def test_chunk_id_is_stable() -> None:
    a = chunk_id("10_Projects/X.md", 3)
    b = chunk_id("10_Projects/X.md", 3)
    assert a == b


def test_chunk_id_changes_with_path_or_idx() -> None:
    base = chunk_id("10_Projects/X.md", 3)
    assert chunk_id("10_Projects/Y.md", 3) != base
    assert chunk_id("10_Projects/X.md", 4) != base


def test_chunk_hash_is_stable_for_same_input() -> None:
    a = chunk_hash(["A", "B"], "content")
    b = chunk_hash(["A", "B"], "content")
    assert a == b


def test_chunk_hash_changes_with_heading_or_content() -> None:
    base = chunk_hash(["A", "B"], "content")
    assert chunk_hash(["A", "C"], "content") != base
    assert chunk_hash(["A", "B"], "content!") != base
