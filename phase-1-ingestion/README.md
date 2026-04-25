# Phase 1 — Ingestion Pipeline

Watches the Obsidian vault, parses markdown with heading-aware section
splitting, generates contextualized embeddings via Voyage AI, and indexes
chunks in a hybrid (dense + BM25) Qdrant collection. Idempotent: only
re-embeds chunks whose content hash changed.

## Layout

```
src/ingestion/
├── config.py          # Pydantic settings from env / .env
├── models.py          # Note, Section, Chunk, ChunkPayload
├── hashing.py         # Stable IDs + content hashes
├── parser.py          # frontmatter + heading-aware splitter
├── chunker.py         # Section + sliding-window chunking
├── embedder.py        # Voyage contextualized embeddings
├── sparse.py          # fastembed BM25
├── store.py           # Qdrant client wrapper
├── indexer.py         # Orchestrator with hash-based idempotency
├── watcher.py         # watchdog handler with debounce
├── logging_setup.py   # structlog JSON logging
└── cli.py             # init / reindex-all / reindex / watch
tests/
├── test_parser.py
├── test_chunker.py
├── test_hashing.py
└── fixtures/sample-vault/
```

## Configuration

Copy `.env.example` to `.env` and fill in:

| Var | Purpose |
|---|---|
| `VAULT_PATH` | Absolute path to the vault root |
| `VOYAGE_API_KEY` | Voyage AI API key |
| `QDRANT_URL` | Qdrant URL (default `http://qdrant:6333` in Docker) |
| `QDRANT_COLLECTION` | Collection name |
| `INDEX_INCLUDE_DIRS` | Comma list of top-level dirs to index |
| `INDEX_EXCLUDE_DIRS` | Comma list of top-level dirs to skip |
| `CHUNK_MAX_TOKENS` | Sections under this stay as one chunk |
| `CHUNK_WINDOW_TOKENS` | Sliding-window size for long sections |
| `CHUNK_OVERLAP_TOKENS` | Window overlap |

## Running locally

```bash
# 1. Start Qdrant + CouchDB
docker compose -f docker/docker-compose.yml up -d qdrant couchdb

# 2. Install deps
pip install -e .

# 3. Bootstrap the collection and run a full reindex
ingestion init
ingestion reindex-all

# 4. Watch for changes
ingestion watch
```

## Running in Docker

```bash
docker compose -f docker/docker-compose.yml up -d
```

The `watcher` service mounts the vault read-only at `/data/vault` and
reindexes any `.md` change within `CHUNK_DEBOUNCE_SECONDS` (default 2s).

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

Tests cover the parser (frontmatter, heading split, code-block immunity,
Obsidian comments), the chunker (short / long sections, embed-text
prefixing, contiguous indices), and hashing (stability, sensitivity).

## Architecture notes

- **Idempotency**: chunk IDs are UUID5 over `(path, chunk_idx)`. Before
  upsert, the indexer fetches existing point hashes for the path and skips
  unchanged chunks. Stale slots beyond the new max index are deleted.
- **Hybrid search**: each point carries a `voyage` dense vector and a
  `bm25` sparse vector. The MCP server (Phase 2) issues hybrid queries
  combining both.
- **Contextualized embeddings**: all chunks of one note are embedded in a
  single Voyage call (`contextualized_embed`) so the model can attend
  across the whole note.

## Definition of Done

- [x] `docker compose up` starts qdrant + couchdb + watcher.
- [x] `ingestion init && ingestion reindex-all` works against
      `tests/fixtures/sample-vault/`.
- [x] Touching a markdown file triggers reindex within 5s (visible in
      structlog JSON output).
- [x] Tests pass: `pytest -q`.
- [x] README explains setup and operations.
