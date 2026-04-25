"""Server boot resilience: /health stays up even when deps aren't configured."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mcp_server.config import Settings
from mcp_server.server import build_app


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        vault_path=tmp_path,
        qdrant_url="http://nonexistent.invalid:6333",
        qdrant_collection="test",
        voyage_api_key="",
        bearer_token="",
        log_level="WARNING",
    )
    base.update(overrides)
    return Settings(**base)


def test_health_route_returns_200_without_voyage_key(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_health_route_bypasses_bearer_auth(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path, bearer_token="secret-xyz"))
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200


def test_protected_paths_reject_without_bearer(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path, bearer_token="secret-xyz"))
    with TestClient(app) as client:
        resp = client.get("/sse")
        assert resp.status_code == 401


def test_health_route_registered_in_router(tmp_path: Path) -> None:
    """The /health route must be in the app's router, not a 404 fallthrough."""
    app = build_app(_settings(tmp_path))
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/health" in paths


def test_oauth_mode_exposes_authorization_endpoints(tmp_path: Path) -> None:
    """When Google OAuth is configured, /authorize + /token must be wired."""
    app = build_app(
        _settings(
            tmp_path,
            google_oauth_client_id="x.apps.googleusercontent.com",
            google_oauth_client_secret="GOCSPX-abc",
            oauth_base_url="https://example.com",
            allowed_emails="kay@example.com",
            oauth_storage_dir=tmp_path / "oauth",
        )
    )
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/authorize" in paths
    assert "/token" in paths


def test_oauth_mode_health_still_public(tmp_path: Path) -> None:
    app = build_app(
        _settings(
            tmp_path,
            google_oauth_client_id="x.apps.googleusercontent.com",
            google_oauth_client_secret="GOCSPX-abc",
            oauth_base_url="https://example.com",
            allowed_emails="kay@example.com",
            oauth_storage_dir=tmp_path / "oauth",
        )
    )
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200