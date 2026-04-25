# Second Brain

Self-hosted persistent memory for Claude. Obsidian as source of truth, Voyage AI for embeddings + reranking, Qdrant as vector store, custom MCP server connects everything to Claude.ai. MeetGeek delivers meeting transcripts via webhook into the vault. Google Tasks consolidated through the same MCP server.

See [docs/architecture.md](docs/architecture.md) for the full architecture, roadmap, and build prompts.

## Repo layout

| Folder | Phase | Purpose |
|---|---|---|
| `docs/` | – | Architecture document |
| `phase-0-vault/` | 0 | Vault skeleton generator (`init_vault.py`) |
| `phase-1-ingestion/` | 1 | File watcher, chunker, embedder, Qdrant indexer |
| `phase-2-mcp-server/` | 2–5 | MCP server (read + write tools, Google Tasks, MeetGeek webhook) |

Each phase is self-contained and includes its own README, `pyproject.toml`, and Docker setup where applicable.

## Quick start

```bash
# Phase 0: generate a vault
python phase-0-vault/init_vault.py \
  --output ~/Vaults/SecondBrain \
  --projects "ChapterNext,C1G Sidebars,Kay Dollt"

# Phase 1: index the vault
cd phase-1-ingestion
docker compose up -d

# Phase 2-5: run the MCP server
cd ../phase-2-mcp-server
docker compose up -d
```

See each phase's `README.md` for details.
