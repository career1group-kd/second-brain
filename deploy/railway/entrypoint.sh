#!/usr/bin/env bash
# Entrypoint for the combined Railway container. Ensures vault + state dirs
# exist on the volume, then runs Qdrant collection bootstrap, then hands off
# to supervisord.

set -euo pipefail

VAULT_PATH="${VAULT_PATH:-/data/vault}"
STATE_PATH_DIR="$(dirname "${LIVESYNC_STATE_PATH:-/data/state/livesync.json}")"
SECRETS_DIR="${SECRETS_DIR:-/data/secrets}"

mkdir -p "$VAULT_PATH" "$STATE_PATH_DIR" "$SECRETS_DIR"

# If a base64-encoded gtasks token was injected via env var, decode it once
# into the secrets dir. Convenient for Railway, where you cannot mount files.
if [[ -n "${GTASKS_TOKEN_B64:-}" ]]; then
    echo "$GTASKS_TOKEN_B64" | base64 -d > "${GTASKS_TOKEN_PATH:-/data/secrets/gtasks_token.enc}"
    chmod 600 "${GTASKS_TOKEN_PATH:-/data/secrets/gtasks_token.enc}"
fi

# Same trick for the OAuth client secrets JSON.
if [[ -n "${GOOGLE_CLIENT_SECRETS_B64:-}" ]]; then
    echo "$GOOGLE_CLIENT_SECRETS_B64" | base64 -d \
        > "${GOOGLE_CLIENT_SECRETS_PATH:-/data/secrets/google_client_secret.json}"
    chmod 600 "${GOOGLE_CLIENT_SECRETS_PATH:-/data/secrets/google_client_secret.json}"
fi

# Phase 5b — Calendar OAuth token (encrypted) and client secrets, for the
# Fireflies webhook. Same base64-via-env trick because Railway can't mount
# files.
if [[ -n "${GCAL_TOKEN_B64:-}" ]]; then
    echo "$GCAL_TOKEN_B64" | base64 -d > "${GCAL_TOKEN_PATH:-/data/secrets/gcal_token.enc}"
    chmod 600 "${GCAL_TOKEN_PATH:-/data/secrets/gcal_token.enc}"
fi

if [[ -n "${GCAL_CLIENT_SECRETS_B64:-}" ]]; then
    echo "$GCAL_CLIENT_SECRETS_B64" | base64 -d \
        > "${GCAL_CLIENT_SECRETS_PATH:-/data/secrets/gcal_client_secret.json}"
    chmod 600 "${GCAL_CLIENT_SECRETS_PATH:-/data/secrets/gcal_client_secret.json}"
fi

# Bootstrap the Qdrant collection idempotently.
python -m ingestion.cli init || echo "WARN: qdrant init failed; will retry from watcher"

exec "$@"
