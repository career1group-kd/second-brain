"""Google OAuth provider with email allowlist enforcement.

Wraps FastMCP's :class:`GoogleProvider` so that only Google accounts whose
email is in `Settings.allowed_emails` can complete the OAuth flow. Any
other account triggers a TokenError at issuance time and the user sees
an authentication failure in Claude.ai instead of getting a usable
session token.

Build with :func:`build_oauth_provider` from a populated
:class:`Settings`. Returns ``None`` if OAuth is not configured, in which
case the caller should fall back to bearer-token middleware.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import structlog
from key_value.aio.stores.filetree import FileTreeStore
from mcp.server.auth.provider import TokenError

from .config import Settings

log = structlog.get_logger()


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode a JWT's payload segment without verifying the signature.

    Safe here because we only call this on tokens received synchronously
    from Google's TLS-secured token endpoint inside the same OAuth flow.
    """
    try:
        _header, payload_b64, _sig = token.split(".")
    except ValueError as exc:
        raise ValueError("malformed JWT") from exc
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))


def _extract_email(idp_tokens: dict[str, Any]) -> str | None:
    """Pull the user's email out of Google's id_token (or userinfo)."""
    id_token = idp_tokens.get("id_token")
    if isinstance(id_token, str) and id_token:
        try:
            claims = _decode_jwt_payload(id_token)
        except Exception:
            log.exception("oauth_id_token_decode_failed")
            return None
        email = claims.get("email")
        if isinstance(email, str) and email:
            return email.lower()
    return None


def build_oauth_provider(settings: Settings):
    """Construct a Google OAuth provider with email allowlist enforcement.

    Returns ``None`` if OAuth is not configured (no client_id / secret).
    Raises ValueError if OAuth is half-configured (e.g. client_id without
    allowed_emails) — fail loud, never silently let everyone in.
    """
    if not settings.google_oauth_enabled:
        return None

    if not settings.allowed_emails_set:
        raise ValueError(
            "google_oauth is configured but allowed_emails is empty. "
            "Refusing to start: set ALLOWED_EMAILS to a comma-separated "
            "list of permitted Google account emails."
        )
    if not settings.oauth_base_url:
        raise ValueError(
            "google_oauth is configured but oauth_base_url is empty. "
            "Set OAUTH_BASE_URL to the public https URL of this server."
        )

    # Imported lazily so that environments without the OAuth deps (e.g.
    # ingestion-only) can still import this module without pulling in
    # FastMCP's auth tree.
    from fastmcp.server.auth.providers.google import GoogleProvider

    allowlist = settings.allowed_emails_set
    log.info(
        "oauth_provider_init",
        allowlist_size=len(allowlist),
        base_url=settings.oauth_base_url,
    )

    storage_dir = settings.oauth_storage_dir
    storage_dir.mkdir(parents=True, exist_ok=True)
    client_storage = FileTreeStore(data_directory=storage_dir)

    class AllowlistGoogleProvider(GoogleProvider):
        """GoogleProvider that rejects accounts not in the allowlist."""

        async def _extract_upstream_claims(
            self, idp_tokens: dict[str, Any]
        ) -> dict[str, Any] | None:
            email = _extract_email(idp_tokens)
            if email is None:
                log.warning("oauth_no_email_in_id_token")
                raise TokenError(
                    "access_denied",
                    "Could not determine email from Google account.",
                )
            if email not in allowlist:
                log.warning("oauth_email_not_allowed", email=email)
                raise TokenError(
                    "access_denied",
                    f"Account '{email}' is not authorized for this server.",
                )
            log.info("oauth_email_allowed", email=email)
            return {"email": email}

    return AllowlistGoogleProvider(
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        base_url=settings.oauth_base_url,
        required_scopes=["openid", "email"],
        # Claude.ai's MCP connector callback. Restrict to claude.ai to
        # prevent a malicious client from using our provider as an open
        # redirector.
        allowed_client_redirect_uris=[
            "https://claude.ai/api/mcp/auth_callback",
            "https://claude.ai/*",
        ],
        client_storage=client_storage,
    )
