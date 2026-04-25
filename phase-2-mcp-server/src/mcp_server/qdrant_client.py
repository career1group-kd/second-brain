"""Qdrant search wrapper for the MCP server."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import structlog
from qdrant_client import QdrantClient, models

log = structlog.get_logger()

DENSE_VECTOR_NAME = "voyage"
SPARSE_VECTOR_NAME = "bm25"


def _isoformat(d: date | datetime) -> str:
    if isinstance(d, datetime):
        return d.isoformat()
    return datetime.combine(d, datetime.min.time()).isoformat()


class VaultIndex:
    def __init__(
        self,
        url: str,
        collection: str,
        *,
        api_key: str | None = None,
    ) -> None:
        # QdrantClient is constructed eagerly but doesn't actually open a
        # connection until the first request. That keeps boot fast and lets
        # the MCP server come up even if Qdrant is briefly unreachable.
        self.client = QdrantClient(url=url, api_key=api_key, prefer_grpc=False)
        self.collection = collection

    # --- Filters --------------------------------------------------------------

    def build_filter(
        self,
        *,
        type: str | None = None,
        project: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        attendees: list[str] | None = None,
        date_from: date | datetime | None = None,
        date_to: date | datetime | None = None,
        path: str | None = None,
        path_prefix: str | None = None,
        path_excludes: list[str] | None = None,
    ) -> models.Filter | None:
        must: list[models.FieldCondition] = []
        must_not: list[models.FieldCondition] = []

        if type:
            must.append(
                models.FieldCondition(key="type", match=models.MatchValue(value=type))
            )
        if project:
            must.append(
                models.FieldCondition(key="project", match=models.MatchValue(value=project))
            )
        if status:
            must.append(
                models.FieldCondition(key="status", match=models.MatchValue(value=status))
            )
        if tags:
            must.append(
                models.FieldCondition(key="tags", match=models.MatchAny(any=tags))
            )
        if attendees:
            must.append(
                models.FieldCondition(key="attendees", match=models.MatchAny(any=attendees))
            )
        if path:
            must.append(
                models.FieldCondition(key="path", match=models.MatchValue(value=path))
            )
        if path_prefix:
            must.append(
                models.FieldCondition(
                    key="path",
                    match=models.MatchText(text=path_prefix),
                )
            )
        if path_excludes:
            for p in path_excludes:
                must_not.append(
                    models.FieldCondition(key="path", match=models.MatchValue(value=p))
                )
        if date_from or date_to:
            rng_kwargs: dict[str, str] = {}
            if date_from:
                rng_kwargs["gte"] = _isoformat(date_from)
            if date_to:
                rng_kwargs["lte"] = _isoformat(date_to)
            must.append(
                models.FieldCondition(
                    key="updated",
                    range=models.DatetimeRange(**rng_kwargs),
                )
            )

        if not must and not must_not:
            return None
        return models.Filter(must=must or None, must_not=must_not or None)

    # --- Hybrid search --------------------------------------------------------

    def hybrid_search(
        self,
        *,
        dense: list[float],
        sparse: models.SparseVector | None,
        limit: int,
        query_filter: models.Filter | None = None,
    ) -> list[dict[str, Any]]:
        prefetch = [
            models.Prefetch(
                query=dense,
                using=DENSE_VECTOR_NAME,
                limit=limit,
                filter=query_filter,
            ),
        ]
        if sparse is not None:
            prefetch.append(
                models.Prefetch(
                    query=sparse,
                    using=SPARSE_VECTOR_NAME,
                    limit=limit,
                    filter=query_filter,
                )
            )

        result = self.client.query_points(
            collection_name=self.collection,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {"id": p.id, "score": p.score, "payload": p.payload or {}}
            for p in result.points
        ]

    # --- Recent / scrolls -----------------------------------------------------

    def list_recent_paths(
        self,
        *,
        limit: int,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        offset = None
        flt = self.build_filter(type=type) if type else None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=flt,
                limit=256,
                with_payload=True,
                with_vectors=False,
                offset=offset,
                order_by=models.OrderBy(
                    key="updated",
                    direction=models.Direction.DESC,
                ),
            )
            for p in points:
                payload = p.payload or {}
                path = payload.get("path")
                if not path:
                    continue
                if path in seen:
                    continue
                seen[path] = payload
                if len(seen) >= limit:
                    return list(seen.values())
            if offset is None:
                break
        return list(seen.values())

    def scroll_filter(
        self,
        *,
        flt: models.Filter,
        limit: int = 256,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=flt,
                limit=256,
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )
            for p in points:
                out.append(p.payload or {})
                if len(out) >= limit:
                    return out
            if offset is None:
                break
        return out

    def first_chunk_vector(self, path: str) -> list[float] | None:
        """Return the dense vector of chunk_idx=0 for a path."""
        points, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(key="path", match=models.MatchValue(value=path)),
                    models.FieldCondition(key="chunk_idx", match=models.MatchValue(value=0)),
                ]
            ),
            limit=1,
            with_payload=False,
            with_vectors=[DENSE_VECTOR_NAME],
        )
        if not points:
            return None
        vec = points[0].vector
        if isinstance(vec, dict):
            v = vec.get(DENSE_VECTOR_NAME)
            return list(v) if v else None
        return list(vec) if vec else None

    def vector_search(
        self,
        *,
        dense: list[float],
        limit: int,
        query_filter: models.Filter | None = None,
    ) -> list[dict[str, Any]]:
        result = self.client.query_points(
            collection_name=self.collection,
            query=dense,
            using=DENSE_VECTOR_NAME,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {"id": p.id, "score": p.score, "payload": p.payload or {}}
            for p in result.points
        ]
