"""Orchestrator: parse → chunk → embed → store, with hash-based idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from .chunker import chunk_note
from .config import Settings
from .embedder import VoyageEmbedder
from .hashing import chunk_hash
from .models import ChunkPayload, Note
from .parser import parse_note
from .sparse import encode_sparse
from .store import VaultStore

log = structlog.get_logger()


class Indexer:
    def __init__(
        self,
        settings: Settings,
        store: VaultStore,
        embedder: VoyageEmbedder,
    ) -> None:
        self.settings = settings
        self.store = store
        self.embedder = embedder

    def is_indexable(self, absolute_path: Path) -> bool:
        if absolute_path.suffix.lower() != ".md":
            return False
        try:
            rel = absolute_path.relative_to(self.settings.vault_path)
        except ValueError:
            return False
        parts = rel.parts
        if not parts:
            return False
        top = parts[0]
        if any(top == d or top.startswith(d + "/") for d in self.settings.exclude_dirs):
            return False
        if self.settings.include_dirs and top not in self.settings.include_dirs:
            return False
        return True

    def index_file(self, absolute_path: Path) -> dict:
        if not absolute_path.is_file():
            return {"status": "missing", "path": str(absolute_path)}
        note = parse_note(absolute_path, self.settings.vault_path)
        return self.index_note(note)

    def index_note(self, note: Note) -> dict:
        chunks = chunk_note(
            note,
            max_tokens=self.settings.chunk_max_tokens,
            window_tokens=self.settings.chunk_window_tokens,
            overlap_tokens=self.settings.chunk_overlap_tokens,
        )
        if not chunks:
            self.store.delete_chunks_for_path(note.relative_path)
            return {"status": "empty", "path": note.relative_path, "chunks": 0}

        existing = self.store.existing_hashes(note.relative_path)
        now = datetime.now(timezone.utc)

        payloads_to_upsert: list[ChunkPayload] = []
        chunks_to_embed = []
        for c in chunks:
            h = chunk_hash(c.heading_path, c.content)
            payload = ChunkPayload.from_chunk(c, note, h, now)
            if existing.get(c.chunk_idx) == h:
                continue
            payloads_to_upsert.append(payload)
            chunks_to_embed.append(c)

        upserted = 0
        if chunks_to_embed:
            dense = self.embedder.embed_chunks(chunks_to_embed)
            sparse = encode_sparse([c.embed_text for c in chunks_to_embed])
            self.store.upsert_chunks(
                note.relative_path,
                payloads_to_upsert,
                dense,
                sparse,
            )
            upserted = len(chunks_to_embed)

        # Drop stale chunks if the note shrank.
        max_idx = chunks[-1].chunk_idx
        self.store.delete_chunks_with_idx_above(note.relative_path, max_idx)

        log.info(
            "note_indexed",
            path=note.relative_path,
            chunks_total=len(chunks),
            chunks_upserted=upserted,
            chunks_unchanged=len(chunks) - upserted,
        )
        return {
            "status": "ok",
            "path": note.relative_path,
            "chunks": len(chunks),
            "upserted": upserted,
        }

    def delete_file(self, relative_path: str) -> None:
        self.store.delete_chunks_for_path(relative_path)
        log.info("note_deleted", path=relative_path)

    def reindex_all(self) -> dict:
        total = 0
        ok = 0
        for absolute in self.settings.vault_path.rglob("*.md"):
            if not self.is_indexable(absolute):
                continue
            total += 1
            try:
                self.index_file(absolute)
                ok += 1
            except Exception:
                log.exception("reindex_failed", path=str(absolute))
        log.info("reindex_complete", total=total, ok=ok)
        return {"total": total, "ok": ok}
