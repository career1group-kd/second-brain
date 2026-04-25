"""Stable hashing utilities for chunk identity and idempotency."""

from __future__ import annotations

import hashlib
import uuid


def chunk_id(relative_path: str, chunk_idx: int) -> str:
    """Deterministic UUID5 ID for a chunk slot in a note.

    Qdrant requires UUID or unsigned integer IDs. Using UUID5 over a fixed
    namespace keeps IDs stable across reruns.
    """
    namespace = uuid.UUID("6f8a7c2e-1d3f-4f82-9b4c-2c3d4e5f6a7b")
    return str(uuid.uuid5(namespace, f"{relative_path}::{chunk_idx}"))


def chunk_hash(heading_path: list[str], content: str) -> str:
    """SHA256 of the canonical chunk representation."""
    payload = " > ".join(heading_path) + "\n\n" + content
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
