"""Encoding helpers."""

from __future__ import annotations

import base64

from livesync_bridge import encoding


def test_doc_id_to_path_strips_known_prefixes() -> None:
    assert encoding.doc_id_to_path("f:10_Projects/X.md") == "10_Projects/X.md"
    assert encoding.doc_id_to_path("p:notes/y.md") == "notes/y.md"
    assert encoding.doc_id_to_path("ps:obfuscated") == "obfuscated"


def test_doc_id_to_path_returns_none_for_chunks() -> None:
    assert encoding.doc_id_to_path("h:abc123") is None


def test_path_to_doc_id_default_prefix() -> None:
    assert encoding.path_to_doc_id("a/b.md") == "f:a/b.md"


def test_is_file_and_chunk_doc() -> None:
    assert encoding.is_file_doc("f:foo.md")
    assert not encoding.is_file_doc("h:bar")
    assert encoding.is_chunk_doc("h:bar")


def test_reassemble_single_doc_plain() -> None:
    doc = {"type": "plain", "data": "hello"}
    out = encoding.reassemble(doc, chunk_resolver=lambda _: None)
    assert out == b"hello"


def test_reassemble_chunked_concatenates() -> None:
    chunks = {
        "h:1": {"_id": "h:1", "data": "alpha "},
        "h:2": {"_id": "h:2", "data": "beta"},
    }
    head = {
        "type": "newnote",
        "children": ["h:1", "h:2"],
    }
    out = encoding.reassemble(head, chunk_resolver=lambda cid: chunks.get(cid))
    assert out == b"alpha beta"


def test_reassemble_skips_missing_chunk() -> None:
    chunks = {"h:1": {"_id": "h:1", "data": "alpha"}}
    head = {"type": "newnote", "children": ["h:1", "h:2"]}
    out = encoding.reassemble(head, chunk_resolver=lambda cid: chunks.get(cid))
    assert out == b"alpha"


def test_reassemble_binary_decodes_base64() -> None:
    raw = b"\x89PNGbinary"
    encoded = base64.b64encode(raw).decode("ascii")
    doc = {"type": "plain_b", "data": encoded, "isBinary": True}
    out = encoding.reassemble(doc, chunk_resolver=lambda _: None)
    assert out == raw


def test_render_plain_round_trips() -> None:
    payload = encoding.render_plain(b"# Title\n\nbody")
    assert payload["type"] == "plain"
    assert payload["data"] == "# Title\n\nbody"
    assert payload["size"] == len(b"# Title\n\nbody")


def test_is_markdown_path() -> None:
    assert encoding.is_markdown_path("a/b.md")
    assert encoding.is_markdown_path("a/b.MD")
    assert not encoding.is_markdown_path("a/b.png")
