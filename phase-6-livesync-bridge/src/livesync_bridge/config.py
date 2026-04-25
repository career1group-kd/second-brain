"""Bridge configuration."""

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

    couchdb_url: str = "http://couchdb:5984"
    couchdb_user: str = ""
    couchdb_password: str = ""
    couchdb_db: str = "obsidian"

    vault_path: Path = Field(default=Path("/data/vault"))

    # File where the last-applied CouchDB sequence is persisted.
    state_path: Path = Field(default=Path("/data/state/livesync.json"))

    log_level: str = "INFO"

    # Skip syncing files under these top-level dirs (matches Obsidian's
    # `.obsidian` config dir, plus our internal scratch areas).
    fs_exclude_top_level: str = ".obsidian,99_Meta/Templates"

    debounce_seconds: float = 1.0

    # How long an outbound write is remembered for echo suppression.
    echo_suppress_seconds: float = 10.0

    # Soft cap on initial reconciliation per cycle.
    reconcile_batch: int = 200

    @property
    def excluded_top_level(self) -> list[str]:
        return [s.strip() for s in self.fs_exclude_top_level.split(",") if s.strip()]


def get_settings() -> Settings:
    return Settings()
