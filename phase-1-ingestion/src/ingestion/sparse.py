"""BM25 sparse vector generation via fastembed."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client.models import SparseVector


@lru_cache(maxsize=1)
def _bm25_model():
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(model_name="Qdrant/bm25")


def encode_sparse(texts: list[str]) -> list[SparseVector]:
    from qdrant_client.models import SparseVector

    if not texts:
        return []
    model = _bm25_model()
    out: list[SparseVector] = []
    for emb in model.embed(texts):
        out.append(SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist()))
    return out
