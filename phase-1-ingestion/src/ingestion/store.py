"""Qdrant store wrapper: hybrid (dense + sparse) collection management."""

from __future__ import annotations

import structlog
from qdrant_client import QdrantClient, models

from .hashing import chunk_id
from .models import ChunkPayload

log = structlog.get_logger()

DENSE_VECTOR_NAME = "voyage"
SPARSE_VECTOR_NAME = "bm25"


class VaultStore:
    def __init__(
        self,
        url: str,
        collection: str,
        *,
        api_key: str | None = None,
        dense_dim: int = 1024,
    ) -> None:
        self.client = QdrantClient(url=url, api_key=api_key)
        self.collection = collection
        self.dense_dim = dense_dim

    # --- Collection lifecycle -------------------------------------------------

    def ensure_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            return

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(
                    size=self.dense_dim,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                ),
            },
        )

        for field, schema in [
            ("type", models.PayloadSchemaType.KEYWORD),
            ("project", models.PayloadSchemaType.KEYWORD),
            ("status", models.PayloadSchemaType.KEYWORD),
            ("tags", models.PayloadSchemaType.KEYWORD),
            ("attendees", models.PayloadSchemaType.KEYWORD),
            ("path", models.PayloadSchemaType.KEYWORD),
            ("updated", models.PayloadSchemaType.DATETIME),
        ]:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=schema,
            )

        log.info("collection_created", name=self.collection, dim=self.dense_dim)

    # --- Lookups --------------------------------------------------------------

    def existing_hashes(self, relative_path: str) -> dict[int, str]:
        """Return {chunk_idx: hash} for existing points of a path."""
        out: dict[int, str] = {}
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="path",
                            match=models.MatchValue(value=relative_path),
                        )
                    ]
                ),
                limit=128,
                with_payload=["chunk_idx", "hash"],
                with_vectors=False,
                offset=offset,
            )
            for p in points:
                payload = p.payload or {}
                idx = payload.get("chunk_idx")
                h = payload.get("hash")
                if idx is not None and h is not None:
                    out[int(idx)] = str(h)
            if offset is None:
                break
        return out

    # --- Mutations ------------------------------------------------------------

    def upsert_chunks(
        self,
        relative_path: str,
        payloads: list[ChunkPayload],
        dense_vectors: list[list[float]],
        sparse_vectors: list[models.SparseVector],
    ) -> None:
        if not payloads:
            return
        points = []
        for payload, dense, sparse in zip(payloads, dense_vectors, sparse_vectors, strict=True):
            points.append(
                models.PointStruct(
                    id=chunk_id(relative_path, payload.chunk_idx),
                    vector={
                        DENSE_VECTOR_NAME: dense,
                        SPARSE_VECTOR_NAME: sparse,
                    },
                    payload=payload.model_dump(exclude_none=True),
                )
            )
        self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def delete_chunks_for_path(self, relative_path: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="path",
                            match=models.MatchValue(value=relative_path),
                        )
                    ]
                )
            ),
            wait=True,
        )

    def delete_chunks_with_idx_above(self, relative_path: str, max_kept_idx: int) -> None:
        """Drop stale chunk slots (e.g. when a note shrinks)."""
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="path",
                            match=models.MatchValue(value=relative_path),
                        ),
                        models.FieldCondition(
                            key="chunk_idx",
                            range=models.Range(gt=max_kept_idx),
                        ),
                    ]
                )
            ),
            wait=True,
        )
