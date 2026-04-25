"""Configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vault_path: Path = Field(default=Path("/data/vault"))
    voyage_api_key: str = ""
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "vault_chunks"
    log_level: str = "INFO"

    index_include_dirs: str = "10_Projects,20_Areas,30_Resources,50_Daily,60_MOCs,70_People"
    index_exclude_dirs: str = "00_Inbox,40_Archive,99_Meta,.obsidian"

    chunk_max_tokens: int = 800
    chunk_window_tokens: int = 500
    chunk_overlap_tokens: int = 80

    embed_model: str = "voyage-context-3"
    query_model: str = "voyage-3.5"
    rerank_model: str = "rerank-2.5"
    embed_dim: int = 1024

    debounce_seconds: float = 2.0

    @property
    def include_dirs(self) -> list[str]:
        return [d.strip() for d in self.index_include_dirs.split(",") if d.strip()]

    @property
    def exclude_dirs(self) -> list[str]:
        return [d.strip() for d in self.index_exclude_dirs.split(",") if d.strip()]


def get_settings() -> Settings:
    return Settings()
