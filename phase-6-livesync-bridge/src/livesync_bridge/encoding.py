"""Encode/decode helpers for obsidian-livesync's CouchDB document format.

Modern obsidian-livesync (>= 0.23) stores notes as a head document with
`type: "plain"` and a `children: ["h:..."]` array referencing one or more
`type: "leaf"` chunk documents that hold the actual `data`. Binary blobs
use the same shape but with `datatype: "newnote_b"` (or `type: "plain_b"`)
on the head; their leaf data is base64-encoded.

Older plugin versions used `type: "newnote"` with prefixed file IDs
(`f:`, `p:`, `ps:`). Reads are tolerant of the union of all seen shapes;
writes default to the modern shape (single content-addressed leaf per file —
the plugin will rechunk on the next edit from a device).
"""

from __future__ import annotations

import base64
import hashlib
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


def _content_chunk_id(content: bytes) -> str:
    """Stable content-addressed chunk ID (`h:<12 base32 chars>`).

    Same content → same ID, so re-writing an unchanged file does not
    create duplicate leaf docs. The format mirrors what obsidian-livesync
    produces (lowercase alphanumeric after `h:`).
    """
    digest = hashlib.sha256(content).digest()
    s = base64.b32encode(digest).decode("ascii").rstrip("=").lower()
    return f"h:{s[:12]}"


def render_plain(
    content: bytes, *, path: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build (leaf_docs, head_doc) for a markdown note.

    The head doc has `type: "plain"` and references its content via
    `children: ["h:..."]` — matching the shape obsidian-livesync writes
    itself. Inline `data` on the head, or `type: "newnote"`, makes modern
    plugin versions throw `Failed to gather content` in
    `ReplicateResultProcessor` during replication.

    The caller MUST write the leaf docs before the head doc so the head
    never references a missing chunk. `ctime`/`mtime` are ms-since-epoch
    ints; `null` confuses the plugin.
    """
    text = content.decode("utf-8", errors="replace")
    chunk_id = _content_chunk_id(content)
    now_ms = int(time.time() * 1000)

    leaf: dict[str, Any] = {
        "_id": chunk_id,
        "type": "leaf",
        "data": text,
    }
    head: dict[str, Any] = {
        "type": "plain",
        "children": [chunk_id],
        "size": len(content),
        "ctime": now_ms,
        "mtime": now_ms,
        "eden": {},
    }
    if path is not None:
        head["path"] = path
    return [leaf], head


def is_markdown_path(path: str) -> bool:
    return path.lower().endswith(".md")
