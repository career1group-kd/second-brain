"""Tests for the Google OAuth provider builder + email allowlist."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from mcp_server.config import Settings
from mcp_server.oauth import _decode_jwt_payload, _extract_email, build_oauth_provider


def _settings(tmp_path: Path, **overrides) -> Settings:
    base: dict = dict(
        vault_path=tmp_path / "vault",
        qdrant_url="http://nonexistent.invalid:6333",
        voyage_api_key="",
        bearer_token="",
        log_level="WARNING",
        oauth_storage_dir=tmp_path / "oauth",
    )
    base.update(overrides)
    return Settings(**base)


def _fake_id_token(claims: dict) -> str:
    """Build a fake unsigned JWT (header.payload.signature) for tests."""
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64(claims)}.fakesig"


def test_build_oauth_provider_returns_none_when_disabled(tmp_path: Path) -> None:
    assert build_oauth_provider(_settings(tmp_path)) is None


def test_build_oauth_provider_raises_without_allowlist(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        google_oauth_client_id="x.apps.googleusercontent.com",
        google_oauth_client_secret="GOCSPX-abc",
        oauth_base_url="https://example.com",
        allowed_emails="",
    )
    with pytest.raises(ValueError, match="allowed_emails"):
        build_oauth_provider(s)


def test_build_oauth_provider_raises_without_base_url(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        google_oauth_client_id="x.apps.googleusercontent.com",
        google_oauth_client_secret="GOCSPX-abc",
        oauth_base_url="",
        allowed_emails="me@example.com",
    )
    with pytest.raises(ValueError, match="oauth_base_url"):
        build_oauth_provider(s)


def test_build_oauth_provider_constructs_when_configured(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        google_oauth_client_id="x.apps.googleusercontent.com",
        google_oauth_client_secret="GOCSPX-abc",
        oauth_base_url="https://example.com",
        allowed_emails="kay@example.com",
    )
    provider = build_oauth_provider(s)
    assert provider is not None
    paths = {getattr(r, "path", None) for r in provider.get_routes()}
    # Must expose the OAuth 2.1 endpoints Claude.ai's connector expects.
    assert "/authorize" in paths
    assert "/token" in paths
    assert "/register" in paths
    assert "/auth/callback" in paths


def test_decode_jwt_payload_round_trip() -> None:
    claims = {"email": "kay@example.com", "sub": "123"}
    decoded = _decode_jwt_payload(_fake_id_token(claims))
    assert decoded == claims


def test_decode_jwt_payload_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        _decode_jwt_payload("not-a-jwt")


def test_extract_email_lowercases() -> None:
    tokens = {"id_token": _fake_id_token({"email": "Kay.Dollt@Example.com"})}
    assert _extract_email(tokens) == "kay.dollt@example.com"


def test_extract_email_returns_none_when_missing() -> None:
    tokens = {"id_token": _fake_id_token({"sub": "123"})}
    assert _extract_email(tokens) is None


def test_extract_email_returns_none_without_id_token() -> None:
    assert _extract_email({}) is None
    assert _extract_email({"id_token": ""}) is None
    assert _extract_email({"id_token": "garbage"}) is None


def test_settings_allowed_emails_set_normalises() -> None:
    s = Settings(allowed_emails=" kay@x.com,  Other@Y.com ,, ")
    assert s.allowed_emails_set == {"kay@x.com", "other@y.com"}


@pytest.mark.asyncio
async def test_provider_rejects_email_not_in_allowlist(tmp_path: Path) -> None:
    from mcp.server.auth.provider import TokenError

    s = _settings(
        tmp_path,
        google_oauth_client_id="x.apps.googleusercontent.com",
        google_oauth_client_secret="GOCSPX-abc",
        oauth_base_url="https://example.com",
        allowed_emails="allowed@example.com",
    )
    provider = build_oauth_provider(s)
    assert provider is not None
    tokens = {"id_token": _fake_id_token({"email": "intruder@example.com"})}
    with pytest.raises(TokenError):
        await provider._extract_upstream_claims(tokens)


@pytest.mark.asyncio
async def test_provider_admits_email_in_allowlist(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        google_oauth_client_id="x.apps.googleusercontent.com",
        google_oauth_client_secret="GOCSPX-abc",
        oauth_base_url="https://example.com",
        allowed_emails="kay@example.com,other@example.com",
    )
    provider = build_oauth_provider(s)
    assert provider is not None
    tokens = {"id_token": _fake_id_token({"email": "Kay@example.com"})}
    claims = await provider._extract_upstream_claims(tokens)
    assert claims == {"email": "kay@example.com"}


@pytest.mark.asyncio
async def test_provider_rejects_token_without_email(tmp_path: Path) -> None:
    from mcp.server.auth.provider import TokenError

    s = _settings(
        tmp_path,
        google_oauth_client_id="x.apps.googleusercontent.com",
        google_oauth_client_secret="GOCSPX-abc",
        oauth_base_url="https://example.com",
        allowed_emails="kay@example.com",
    )
    provider = build_oauth_provider(s)
    assert provider is not None
    tokens = {"id_token": _fake_id_token({"sub": "no-email-here"})}
    with pytest.raises(TokenError):
        await provider._extract_upstream_claims(tokens)
