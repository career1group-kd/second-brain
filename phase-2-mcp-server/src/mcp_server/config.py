"""MCP server configuration."""

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
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "vault_chunks"
    voyage_api_key: str = ""
    query_model: str = "voyage-3.5"
    rerank_model: str = "rerank-2.5"
    embed_dim: int = 1024

    bearer_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    public_domain: str = "mcp.example.com"
    log_level: str = "INFO"

    # Phase 4 — Google Tasks
    google_client_secrets_path: Path = Path("/data/secrets/google_client_secret.json")
    gtasks_token_path: Path = Path("/data/secrets/gtasks_token.enc")
    gtasks_token_key: str = ""

    # Phase 5 — MeetGeek
    meetgeek_webhook_secret: str = ""
    meetgeek_api_token: str = ""


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """For tests: drop the cached settings instance."""
    global _settings
    _settings = None
