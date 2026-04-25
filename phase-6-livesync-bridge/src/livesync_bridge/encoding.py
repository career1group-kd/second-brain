"""Encode/decode helpers for obsidian-livesync's CouchDB document format.

obsidian-livesync stores notes either:

1. As a single document with `data: <utf-8 string>` (`type: plain` or `newnote`
   with no chunking).
2. As a "head" document referencing chunk children (`type: newnote`,
   `children: [...]`), where each child is a separate document of
   `type: leaf` carrying a slice of the content in `data`.
3. For binary files (`type: plain` with the binary bit set), `data` is
   base64-encoded.

Different plugin versions name fields slightly differently. The helpers below
are tolerant: they accept the union of seen shapes and decode best-effort.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Iterable

# An obsidian-livesync ID for a *file* document.
FILE_ID_PREFIXES = ("f:", "p:", "ps:")
CHUNK_ID_PREFIX = "h:"


def is_file_doc(doc_id: str) -> bool:
    return any(doc_id.startswith(p) for p in FILE_ID_PREFIXES)


def is_chunk_doc(doc_id: str) -> bool:
    return doc_id.startswith(CHUNK_ID_PREFIX)


def doc_id_to_path(doc_id: str) -> str | None:
    """Strip the LiveSync prefix; return None if the ID isn't a file doc."""
    for p in FILE_ID_PREFIXES:
        if doc_id.startswith(p):
            return doc_id[len(p) :]
    return None


def path_to_doc_id(path: str, *, prefix: str = "f:") -> str:
    return f"{prefix}{path}"


def _decode_data(data: Any, *, is_binary: bool) -> bytes:
    if data is None:
        return b""
    if isinstance(data, bytes):
        return data
    s = str(data)
    if is_binary:
        # Sometimes the data is wrapped in a `data:...;base64,` URI prefix.
        m = re.match(r"^data:[^;]+;base64,(.+)$", s)
        if m:
            s = m.group(1)
        try:
            return base64.b64decode(s, validate=False)
        except Exception:
            return s.encode("utf-8")
    return s.encode("utf-8")


def doc_is_binary(doc: dict[str, Any]) -> bool:
    if doc.get("datatype") == "newnote_b" or doc.get("type") == "plain_b":
        return True
    if doc.get("type") == "plain" and doc.get("mimetype", "").startswith(
        ("application/", "image/", "audio/", "video/")
    ):
        return True
    return bool(doc.get("isBinary"))


def reassemble(
    head: dict[str, Any],
    *,
    chunk_resolver,
) -> bytes:
    """Return the full content bytes for a head doc.

    `chunk_resolver(chunk_id) -> dict` looks up a chunk doc by ID and may
    raise if the chunk is missing.
    """
    binary = doc_is_binary(head)

    children = head.get("children")
    if isinstance(children, list) and children:
        parts: list[bytes] = []
        for chunk_id in children:
            chunk = chunk_resolver(chunk_id)
            if chunk is None:
                continue
            parts.append(_decode_data(chunk.get("data"), is_binary=binary))
        return b"".join(parts)

    if "data" in head:
        return _decode_data(head["data"], is_binary=binary)

    return b""


def render_plain(content: bytes) -> dict[str, Any]:
    """Build a single-doc payload (no chunking) for a markdown note.

    The Obsidian plugin will accept this shape for sync; on the next edit
    from a device, the plugin may rewrite into chunked form.
    """
    text = content.decode("utf-8", errors="replace")
    return {
        "type": "plain",
        "data": text,
        "size": len(content),
        "ctime": None,
        "mtime": None,
    }


def is_markdown_path(path: str) -> bool:
    return path.lower().endswith(".md")
