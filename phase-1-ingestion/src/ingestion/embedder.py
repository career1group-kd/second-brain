"""Voyage AI embedding client wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from .models import Chunk

log = structlog.get_logger()

# Soft API limits per call.
MAX_CHUNKS_PER_CALL = 1000
MAX_TOKENS_PER_CALL = 320_000


class VoyageEmbedder:
    """Embeds chunks via Voyage's contextualized embeddings endpoint."""

    def __init__(self, api_key: str, model: str = "voyage-context-3", dim: int = 1024) -> None:
        if not api_key:
            raise ValueError("VOYAGE_API_KEY is required")
        import voyageai

        self.client = voyageai.Client(api_key=api_key)
        self.model = model
        self.dim = dim

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = self.client.contextualized_embed(
            inputs=[texts],
            model=self.model,
            input_type="document",
        )
        # contextualized_embed returns one ContextualizedEmbeddingsObject per
        # input list; flatten its embeddings to a list[list[float]].
        return list(result.results[0].embeddings)

    def embed_chunks(self, chunks: list[Chunk]) -> list[list[float]]:
        """Return one vector per chunk, in input order."""
        if not chunks:
            return []
        if len(chunks) > MAX_CHUNKS_PER_CALL:
            log.warning(
                "too_many_chunks_truncating",
                count=len(chunks),
                limit=MAX_CHUNKS_PER_CALL,
            )
            chunks = chunks[:MAX_CHUNKS_PER_CALL]
        texts = [c.embed_text for c in chunks]
        return self._embed_documents(texts)
