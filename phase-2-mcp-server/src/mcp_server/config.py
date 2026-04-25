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

    # --- Google OAuth (per-user authentication for Claude.ai) -----------
    # When set, the SSE endpoint requires Google login instead of (or in
    # addition to) BEARER_TOKEN. Only emails listed in `allowed_emails`
    # may complete the flow; others are rejected at token issuance.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    oauth_base_url: str = ""
    # Comma-separated list of allowed Google account emails. Empty means
    # the OAuth flow is disabled (no whitelist = nobody) when an OAuth
    # client_id is configured.
    allowed_emails: str = ""
    # Persistent storage for DCR client registrations. Survives redeploys
    # so Claude.ai doesn't have to re-register on every container restart.
    oauth_storage_dir: Path = Path("/data/state/oauth")

    # Phase 4 — Google Tasks
    google_client_secrets_path: Path = Path("/data/secrets/google_client_secret.json")
    gtasks_token_path: Path = Path("/data/secrets/gtasks_token.enc")
    gtasks_token_key: str = ""

    # Phase 5 — MeetGeek
    meetgeek_webhook_secret: str = ""
    meetgeek_api_token: str = ""

    # When True (default) the container crashes immediately if tool
    # registration fails, making Railway show the traceback right away.
    # Set to False only for emergency deploys where /health must stay up
    # even with a broken tool layer.
    fail_fast_on_tool_registration: bool = True

    @property
    def allowed_emails_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_oauth_client_id and self.google_oauth_client_secret)


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
