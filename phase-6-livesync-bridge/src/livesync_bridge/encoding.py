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
import time
from typing import Any, Iterable

# Older obsidian-livesync versions stored file docs with one of these
# prefixes. Newer versions (>= 0.23 roughly) store the (lowercased) vault
# path directly as the doc ID, with a separate `path` field carrying the
# original casing. We support both shapes on read; writes default to the
# new no-prefix format and can be overridden via Settings.
FILE_ID_PREFIXES = ("f:", "p:", "ps:")
CHUNK_ID_PREFIX = "h:"

# Doc IDs that are definitely NOT vault files: chunk leaves, CouchDB
# system docs (`_local/...`, `_design/...`), LiveSync's own metadata
# (`obsydian_livesync_version`, milestone markers, sync params, ...).
_NON_FILE_PREFIXES = (
    CHUNK_ID_PREFIX,
    "_",
    "obsydian_livesync_",
    "obsidian_livesync_",
)


def is_file_doc(doc_id: str) -> bool:
    """True if `doc_id` looks like a vault-file document.

    Accepts both the old prefixed format (`f:`, `p:`, `ps:`) and the new
    bare-path format used by recent obsidian-livesync versions.
    """
    if not doc_id:
        return False
    if any(doc_id.startswith(p) for p in FILE_ID_PREFIXES):
        return True
    if any(doc_id.startswith(p) for p in _NON_FILE_PREFIXES):
        return False
    return True


def is_chunk_doc(doc_id: str) -> bool:
    return doc_id.startswith(CHUNK_ID_PREFIX)


def doc_id_to_path(doc_id: str, doc: dict[str, Any] | None = None) -> str | None:
    """Strip the LiveSync prefix or use the doc's `path` field.

    For the old prefixed format the prefix is stripped from the ID. For
    the new bare-path format the doc body's `path` field is preferred
    (preserves original casing), falling back to the ID itself.
    Returns None if the ID is not a file doc.
    """
    if not is_file_doc(doc_id):
        return None
    for p in FILE_ID_PREFIXES:
        if doc_id.startswith(p):
            return doc_id[len(p) :]
    if doc is not None:
        path = doc.get("path")
        if isinstance(path, str) and path:
            return path
    return doc_id


def path_to_doc_id(path: str, *, prefix: str = "") -> str:
    """Build a CouchDB doc ID from a vault path.

    Default prefix is empty to match modern obsidian-livesync. Override
    via the `couchdb_file_prefix` setting if you target an older plugin.
    Bare-path IDs are lowercased (LiveSync's convention).
    """
    if prefix:
        return f"{prefix}{path}"
    return path.lower()


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


def render_plain(content: bytes, *, path: str | None = None) -> dict[str, Any]:
    """Build a single-doc payload (no chunking) for a markdown note.

    The Obsidian plugin accepts this shape and will rewrite into chunked
    form on the next edit from a device. `type: "newnote"` is the markdown
    type — `"plain"` is reserved for binary blobs in modern plugin versions
    and routes the doc through the base64 decode path, which fails for
    text content with `Failed to gather content` in ReplicateResultProcessor.
    `ctime`/`mtime` must be ms-since-epoch ints; `null` confuses the plugin.
    """
    text = content.decode("utf-8", errors="replace")
    now_ms = int(time.time() * 1000)
    body: dict[str, Any] = {
        "type": "newnote",
        "data": text,
        "size": len(content),
        "ctime": now_ms,
        "mtime": now_ms,
    }
    if path is not None:
        body["path"] = path
    return body


def is_markdown_path(path: str) -> bool:
    return path.lower().endswith(".md")
