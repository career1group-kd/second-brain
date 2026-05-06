# Second Brain

Self-hosted persistent memory for Claude. Obsidian is the source of truth,
Voyage AI does embeddings + reranking, Qdrant stores the vectors, and a
custom MCP server connects it all to Claude.ai. Fireflies delivers meeting
transcripts via webhook into the vault; Google Tasks runs through the same
MCP server.

See [docs/architecture.md](docs/architecture.md) for the full architecture
and roadmap.

## Repo layout

| Folder | Purpose |
|---|---|
| `phase-0-vault/` | Vault skeleton generator (`init_vault.py`) |
| `phase-1-ingestion/` | File watcher, chunker, embedder, Qdrant indexer |
| `phase-2-mcp-server/` | MCP server (vault read/write, people, Google Tasks, Fireflies webhook) |
| `phase-6-livesync-bridge/` | CouchDB ↔ filesystem sync for obsidian-livesync |
| `deploy/railway/` | Railway deployment artifacts (combined Dockerfile, supervisord) |
| `docs/` | Architecture document |

Each phase is self-contained and ships its own README, `pyproject.toml`,
and Docker setup where applicable.

## Quick start (local development)

```bash
# Phase 0: generate a vault
python phase-0-vault/init_vault.py \
  --output ~/Vaults/SecondBrain \
  --projects "ChapterNext,C1G Sidebars,Kay Dollt"

# Phase 1: index the vault
cd phase-1-ingestion && docker compose up -d

# Phase 2: run the MCP server
cd ../phase-2-mcp-server && docker compose up -d
```

## Deploying on Railway

Production deployment with multi-device LiveSync is documented in
[`deploy/railway/README.md`](deploy/railway/README.md): the three Railway
services (Qdrant, CouchDB, combined `second-brain`), Obsidian LiveSync
setup, Claude.ai connector registration, and Fireflies wiring.

See each phase's `README.md` for details.
