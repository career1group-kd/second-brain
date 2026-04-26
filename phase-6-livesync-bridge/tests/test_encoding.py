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


def test_doc_id_to_path_returns_none_for_system_docs() -> None:
    assert encoding.doc_id_to_path("_local/something") is None
    assert encoding.doc_id_to_path("_design/foo") is None
    assert encoding.doc_id_to_path("obsydian_livesync_version") is None
    assert encoding.doc_id_to_path("obsydian_livesync_milestone") is None


def test_doc_id_to_path_bareformat_uses_doc_path_field() -> None:
    # New livesync stores the lowercased path as ID and the original-case
    # path in the doc body. Prefer the body field.
    doc = {"_id": "willkommen.md", "path": "Willkommen.md", "type": "plain"}
    assert encoding.doc_id_to_path("willkommen.md", doc) == "Willkommen.md"


def test_doc_id_to_path_bareformat_falls_back_to_id() -> None:
    assert encoding.doc_id_to_path("willkommen.md") == "willkommen.md"
    assert encoding.doc_id_to_path("willkommen.md", {}) == "willkommen.md"


def test_path_to_doc_id_default_no_prefix() -> None:
    # Modern livesync expects the bare lowercased path as ID.
    assert encoding.path_to_doc_id("A/B.md") == "a/b.md"
    assert encoding.path_to_doc_id("Notes/Foo.md") == "notes/foo.md"


def test_path_to_doc_id_with_legacy_prefix() -> None:
    assert encoding.path_to_doc_id("a/b.md", prefix="f:") == "f:a/b.md"


def test_is_file_and_chunk_doc() -> None:
    assert encoding.is_file_doc("f:foo.md")
    assert not encoding.is_file_doc("h:bar")
    assert encoding.is_chunk_doc("h:bar")


def test_is_file_doc_recognizes_bare_path_format() -> None:
    assert encoding.is_file_doc("willkommen.md")
    assert encoding.is_file_doc("10_projects/x.md")
    assert encoding.is_file_doc("weitere notiz.md")


def test_is_file_doc_rejects_system_and_metadata() -> None:
    assert not encoding.is_file_doc("")
    assert not encoding.is_file_doc("h:abc123")
    assert not encoding.is_file_doc("_local/foo")
    assert not encoding.is_file_doc("_design/bar")
    assert not encoding.is_file_doc("obsydian_livesync_version")
    assert not encoding.is_file_doc("obsidian_livesync_anything")


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


def test_render_plain_emits_chunked_head_and_leaf() -> None:
    # Modern obsidian-livesync expects markdown as `type: "plain"` with
    # a `children: ["h:..."]` chunk reference; the head must NOT carry
    # inline `data`. Inline-data heads (or `type: "newnote"`) cause
    # `Failed to gather content` in the plugin's ReplicateResultProcessor.
    leaves, head = encoding.render_plain(b"# Title\n\nbody")
    assert head["type"] == "plain"
    assert "data" not in head
    assert isinstance(head["children"], list) and len(head["children"]) == 1
    assert head["size"] == len(b"# Title\n\nbody")
    assert head["eden"] == {}
    assert "path" not in head
    assert isinstance(head["mtime"], int) and head["mtime"] > 0
    assert isinstance(head["ctime"], int) and head["ctime"] > 0

    assert len(leaves) == 1
    leaf = leaves[0]
    assert leaf["_id"] == head["children"][0]
    assert leaf["_id"].startswith("h:")
    assert leaf["type"] == "leaf"
    assert leaf["data"] == "# Title\n\nbody"


def test_render_plain_includes_path_when_given() -> None:
    _, head = encoding.render_plain(b"x", path="Notes/Foo.md")
    assert head["path"] == "Notes/Foo.md"


def test_render_plain_chunk_id_is_content_addressed() -> None:
    # Same content → same chunk ID, so re-writing an unchanged file
    # does not produce a duplicate leaf doc.
    a_leaves, _ = encoding.render_plain(b"hello")
    b_leaves, _ = encoding.render_plain(b"hello")
    c_leaves, _ = encoding.render_plain(b"world")
    assert a_leaves[0]["_id"] == b_leaves[0]["_id"]
    assert a_leaves[0]["_id"] != c_leaves[0]["_id"]


def test_render_plain_round_trips_via_reassemble() -> None:
    # Sanity: what render_plain emits must decode back to the same bytes
    # when the head is reassembled with its leaves as the chunk source.
    original = b"# Title\n\nbody with umlauts: \xc3\xa4\xc3\xb6"
    leaves, head = encoding.render_plain(original)
    by_id = {leaf["_id"]: leaf for leaf in leaves}
    out = encoding.reassemble(head, chunk_resolver=lambda cid: by_id.get(cid))
    assert out == original


def test_is_markdown_path() -> None:
    assert encoding.is_markdown_path("a/b.md")
    assert encoding.is_markdown_path("a/b.MD")
    assert not encoding.is_markdown_path("a/b.png")
