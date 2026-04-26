# Phase 6 — LiveSync Bridge

Bidirectional sync between an `obsidian-livesync` CouchDB and a vault on
disk. Required when you run the Second Brain on Railway (or any setup
where Obsidian devices sync via CouchDB but the watcher / MCP server
need files on a filesystem).

## What it does

- **CouchDB → FS**: subscribes to the `_changes` feed; for each updated
  document it writes the corresponding markdown file under
  `VAULT_PATH`. Reassembles chunked storage if the plugin uses it.
  Persists the last-applied sequence to a state file so restarts resume.
- **FS → CouchDB**: a watchdog observer detects local file changes (the
  MCP server's `append_to_living_doc` and friends) and pushes them as
  new revisions to CouchDB so devices see the update.
- **Echo suppression**: every outbound write is hashed and remembered
  for `ECHO_SUPPRESS_SECONDS`. If the opposite pump fires for the same
  path with the same hash inside that window, it's dropped — no
  oscillation.

## Layout

```
src/livesync_bridge/
├── config.py         # Pydantic Settings
├── encoding.py       # ID/path conversion + chunked-storage reassembly
├── couchdb.py        # async httpx client (GET, PUT, DELETE, /_changes)
├── bridge.py         # the two pumps + echo suppression
└── cli.py            # `livesync-bridge run` / `... reconcile`
tests/
├── test_encoding.py
├── test_echo_suppression.py
└── test_apply_doc.py
```

## Configuration

Copy `.env.example` to `.env` (or set Railway variables):

| Var | Default | Notes |
|---|---|---|
| `COUCHDB_URL` | `http://couchdb:5984` | Internal Railway URL |
| `COUCHDB_USER` | (empty) | Admin user |
| `COUCHDB_PASSWORD` | (empty) | Admin password |
| `COUCHDB_DB` | `obsidian` | LiveSync DB name |
| `VAULT_PATH` | `/data/vault` | Where files materialize |
| `STATE_PATH` | `/data/state/livesync.json` | Last applied sequence |
| `ECHO_SUPPRESS_SECONDS` | `10` | Echo window |
| `DEBOUNCE_SECONDS` | `1` | FS event coalescing |
| `FS_EXCLUDE_TOP_LEVEL` | `.obsidian,99_Meta/Templates` | Skip syncing |

## Run

```bash
pip install -e .
livesync-bridge reconcile   # one-shot pull from CouchDB
livesync-bridge run         # bidirectional daemon
```

In Docker (Railway), `supervisord` runs `livesync-bridge run` as one of
three programs in the combined `second-brain` container.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

Coverage:

- `test_encoding.py` — ID prefixes, chunk reassembly, base64 for
  binary, render_plain output shape.
- `test_echo_suppression.py` — match / mismatch / TTL / GC behaviour.
- `test_apply_doc.py` — write/delete, exclusions, FS↔Couch echo
  suppression in both directions (mocked CouchDB).

## Caveats

- **End-to-end encryption**: not supported. If the plugin has E2EE on,
  the bridge sees ciphertext and writes ciphertext to disk — useless to
  the watcher. Leave E2EE off, or extend `encoding.py` with a decryptor.
- **Chunk write-back format**: when the bridge pushes a file *to*
  CouchDB, it writes the same shape obsidian-livesync writes itself —
  a head doc (`type: "plain"`, `children: ["h:..."]`) plus a single
  content-addressed leaf doc (`type: "leaf"`, `data: <utf-8>`). The
  plugin may rechunk on the next device-side edit, which is fine.
  Inline-`data` heads or `type: "newnote"` cause `Failed to gather
  content` toasts in `ReplicateResultProcessor` — don't go there.
- **Conflict resolution**: if two devices edit the same bytes
  simultaneously, CouchDB stores both revisions and the bridge applies
  the winning revision per the plugin's conflict policy. We don't
  surface conflict markers in the file.
- **Initial reconciliation order**: the bridge pulls from CouchDB on
  startup before opening the FS watcher. That guarantees you don't
  push stale local content over fresh remote content on a fresh
  deployment.
