"""Voyage client wrapper: query embeddings + reranking."""

from __future__ import annotations

from typing import Any

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger()


class VoyageClient:
    def __init__(
        self,
        api_key: str,
        query_model: str = "voyage-3.5",
        rerank_model: str = "rerank-2.5",
    ) -> None:
        if not api_key:
            raise ValueError("VOYAGE_API_KEY is required")
        import voyageai

        self.client = voyageai.Client(api_key=api_key)
        self.query_model = query_model
        self.rerank_model = rerank_model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def embed_query(self, query: str) -> list[float]:
        result = self.client.embed(
            texts=[query],
            model=self.query_model,
            input_type="query",
        )
        return list(result.embeddings[0])

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_k: int,
    ) -> list[tuple[int, float]]:
        """Return [(original_index, score), ...] sorted by score desc."""
        if not documents:
            return []
        result = self.client.rerank(
            query=query,
            documents=documents,
            model=self.rerank_model,
            top_k=min(top_k, len(documents)),
        )
        return [(r.index, r.relevance_score) for r in result.results]
