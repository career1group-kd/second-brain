# Phase 2–5 — MCP Server

The single connector between [Claude.ai](https://claude.ai) and the Second
Brain. Exposes vault read + write tools, person tools, Google Tasks tools,
and accepts MeetGeek webhooks. Talks Streamable HTTP on `/mcp` with Bearer
auth, fronted by Caddy for auto-TLS.

## Phase coverage

| Phase | Module(s) |
|---|---|
| 2 — read MVP | `tools/vault_read.py`, `tools/people_read.py`, `qdrant_client.py`, `voyage.py`, `vault.py` |
| 3 — write + quality | `tools/vault_write.py`, `sections.py`, `atomic.py`, `frontmatter_io.py`, `schemas.py`, `rerank_cache.py` |
| 4 — Google Tasks | `gtasks_client.py`, `gtasks_cli.py`, `tools/gtasks.py` |
| 5 — MeetGeek webhook | `meetgeek/webhook.py`, `meetgeek/matcher.py`, `meetgeek/renderer.py`, `meetgeek/types.py` |

## Tools registered

**Vault read** (Phase 2):
`search_notes`, `get_note`, `get_living_doc`, `list_recent`, `find_related`,
`list_active_projects`.

**People read** (Phase 2):
`get_person`, `find_person`, `list_recent_interactions`,
`list_people_by_company`.

**Vault write** (Phase 3):
`append_to_living_doc`, `update_section`, `create_note`, `append_to_person`,
`update_person_meta`, `create_person`.

**Google Tasks** (Phase 4 — only registered when a token is present):
`list_task_lists`, `list_tasks`, `create_task`, `complete_task`,
`update_task`, `resolve_task_list`.

**HTTP routes**:
- `POST /meetgeek/webhook` (Phase 5) — MeetGeek delivery.
- `GET /health` — liveness probe.
- `/mcp` — MCP Streamable HTTP transport for Claude.ai and the Claude mobile apps.

## Setup

### 1. Configure

Copy `.env.example` to `.env` and fill in:

| Required | Var |
|---|---|
| ✓ | `VAULT_PATH`, `BEARER_TOKEN`, `VOYAGE_API_KEY`, `QDRANT_URL`, `QDRANT_COLLECTION` |
| Phase 4 | `GOOGLE_CLIENT_SECRETS_PATH`, `GTASKS_TOKEN_KEY` (Fernet) |
| Phase 5 | `MEETGEEK_WEBHOOK_SECRET` |

Generate a Fernet key for Google Tasks:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set as `GTASKS_TOKEN_KEY`. Then run a one-time auth flow:

```bash
gtasks-auth auth
```

This opens a browser, completes the OAuth flow with the
`https://www.googleapis.com/auth/tasks` scope, and persists an encrypted
token at `GTASKS_TOKEN_PATH`.

### 2. Run locally

```bash
pip install -e ".[dev]"
mcp-server  # binds 0.0.0.0:8000
```

### 3. Run in Docker (with Caddy + auto-TLS)

```bash
PUBLIC_DOMAIN=mcp.example.com docker compose -f docker/docker-compose.yml up -d
```

Caddy issues a Let's Encrypt cert and reverse-proxies `mcp_server:8000`.

### 4. Register in Claude.ai

Settings → Connectors → Add custom connector:

- URL: `https://<PUBLIC_DOMAIN>/mcp`
- Auth: Bearer
- Token: value of `BEARER_TOKEN`

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

Coverage:

- `test_sections.py` — section detection + auto-create
- `test_atomic.py` — concurrent writes, mtime conflict detection
- `test_schemas.py` — frontmatter validation per type
- `test_rerank_cache.py` — LRU + TTL
- `test_vault.py` — path safety, frontmatter listing
- `test_vault_write.py` — append/update flows + 100-thread concurrent appends
- `test_meetgeek.py` — speaker matching, markdown rendering

## Compound flows

Some flows live in Claude's calling pattern, not in server-side compound
tools:

- **Create a task linked to a Living Doc**: Claude calls `resolve_task_list`,
  `create_task`, then `append_to_living_doc(... → gtask:<task_id>)`.
- **Mark a task done**: Claude calls `complete_task`, then `update_section`
  to flip the checkbox.

## Operations

- **Logs**: structlog JSON to stdout — collect with any log shipper.
- **Token rotation**: regenerate `BEARER_TOKEN`, redeploy, re-register in
  Claude.ai. Generate a fresh `GTASKS_TOKEN_KEY` only if you also re-run
  `gtasks-auth auth`.
- **Health check**: `GET /health` returns 200.
