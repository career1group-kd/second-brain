"""BM25 sparse vector for queries (mirror of phase-1 sparse module)."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client.models import SparseVector


@lru_cache(maxsize=1)
def _bm25_model():
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(model_name="Qdrant/bm25")


def encode_query(text: str) -> SparseVector | None:
    from qdrant_client.models import SparseVector

    try:
        model = _bm25_model()
        emb = next(iter(model.query_embed([text])))
        return SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist())
    except Exception:
        return None
