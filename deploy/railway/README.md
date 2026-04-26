# Railway Deployment Guide

End-to-end walkthrough for deploying the Second Brain on
[Railway](https://railway.app) with **LiveSync (CouchDB)** for multi-device
sync between Obsidian on Mac/iPhone/iPad and the server vault.

## Architecture on Railway

Three services in one project, sharing Railway's private network:

```
┌─────────────────────────────────────────────────────────────┐
│  Project: second-brain                                      │
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌─────────────────────┐    │
│  │  Qdrant  │    │ CouchDB  │◄───┤  second-brain (app) │    │
│  │  :6333   │    │  :5984   │    │  :8000 (public)     │    │
│  │ ┌──────┐ │    │ ┌──────┐ │    │  ┌────────────────┐ │    │
│  │ │ vol  │ │    │ │ vol  │ │    │  │ vol /data      │ │    │
│  │ └──────┘ │    │ └──────┘ │    │  │  ├─ vault/     │ │    │
│  └──────────┘    └──────────┘    │  │  ├─ state/     │ │    │
│                                  │  │  └─ secrets/   │ │    │
│                                  │  └────────────────┘ │    │
│                                  │  supervisord runs:  │    │
│                                  │  • livesync-bridge  │    │
│                                  │  • watcher (Phase 1)│    │
│                                  │  • mcp-server (2-5) │    │
│                                  └─────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                                            │
                                  https://<public>.up.railway.app
                                            │
                                            ▼
                             Claude.ai Custom Connector (SSE)
                             MeetGeek webhook (POST /meetgeek/webhook)
                             Obsidian devices (LiveSync to CouchDB)
```

The combined `second-brain` service runs three processes under
`supervisord`, all sharing one volume mounted at `/data`. The bridge
keeps `/data/vault` and CouchDB in sync; the watcher reindexes any
markdown change into Qdrant; the MCP server serves Claude.ai.

## Prerequisites

Before you start, gather:

| What | Where | Why |
|---|---|---|
| Voyage AI API key | https://dashboard.voyageai.com | Embeddings + reranking |
| MeetGeek webhook secret | (you choose) | Bearer for `POST /meetgeek/webhook` |
| Bearer token for Claude.ai | (you choose, 32+ chars) | Auth for the SSE endpoint |
| CouchDB admin password | (you choose) | Admin login for LiveSync |
| Fernet key for Google Tasks | generated locally | Encrypts the OAuth token |
| Google OAuth `client_secret.json` | https://console.cloud.google.com | Tasks API client |

Generate the random secrets:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"          # BEARER_TOKEN
python3 -c "import secrets; print(secrets.token_urlsafe(24))"          # MEETGEEK_WEBHOOK_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(20))"          # COUCHDB_PASSWORD
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # GTASKS_TOKEN_KEY
```

## Step 1 — Create the Railway project

1. Sign in at railway.app, **New Project → Empty Project**, name it
   `second-brain`.
2. In Settings → Environment, create a single environment `prod`.

## Step 2 — Deploy Qdrant

1. **+ New Service → Empty Service**, name it `qdrant`.
2. Settings → Source: leave empty.
3. Settings → Image: `qdrant/qdrant:latest`.
4. Settings → Networking: enable **Private Networking** (default on).
   Note the value of `RAILWAY_PRIVATE_DOMAIN` — you'll reference it as
   `${{Qdrant.RAILWAY_PRIVATE_DOMAIN}}` from other services.
5. Settings → Volumes: add a volume named `qdrant-storage`, mount path
   `/qdrant/storage`, size 10 GB (start small; you can grow it).
6. Deploy.

## Step 3 — Deploy CouchDB

1. **+ New Service → Empty Service**, name it `couchdb`.
2. Settings → Image: `couchdb:3`.
3. Settings → Variables (this service):

   ```
   COUCHDB_USER=admin
   COUCHDB_PASSWORD=<the password you generated>
   ```

4. Settings → Volumes: add `couchdb-data` mounted at `/opt/couchdb/data`,
   size 5 GB.
5. Settings → Networking: enable **Private Networking**.
6. Deploy.
7. Once it's up, exec into the container (Railway → couchdb → Shell) and
   create the LiveSync database:

   ```bash
   curl -u admin:$COUCHDB_PASSWORD -X PUT http://127.0.0.1:5984/obsidian
   curl -u admin:$COUCHDB_PASSWORD -X PUT http://127.0.0.1:5984/_users
   ```

   The bridge will also try to create `obsidian` on first run if missing.

## Step 4 — Deploy the combined `second-brain` service

1. **+ New Service → GitHub Repo**, point it at this repository.
2. Settings → Source → Root Directory: leave as repo root.
3. Settings → Build → Builder: **Dockerfile**.
4. Settings → Build → Dockerfile Path: `deploy/railway/Dockerfile`.
5. Settings → Volumes: add a volume named `app-data`, mount path
   `/data`, size 10 GB.
6. Settings → Networking: add a **public domain** (Railway will issue
   `*.up.railway.app` automatically with TLS).
7. Settings → Variables: paste the values from
   `deploy/railway/.env.example`. The cross-service references
   `${{Qdrant.RAILWAY_PRIVATE_DOMAIN}}` and
   `${{Couchdb.RAILWAY_PRIVATE_DOMAIN}}` resolve at deploy time —
   make sure your service names match (`Qdrant` and `Couchdb` if you
   used those, otherwise update the names).
8. Settings → Health Check Path: `/health`.
9. Deploy. Logs should show, in order:
   `livesync-bridge` started → `watcher` started → `mcp-server` started.

## Step 5 — One-time Google Tasks auth (local, then upload)

Google's `InstalledAppFlow` needs a browser, so run it on your laptop:

```bash
cd second-brain/phase-2-mcp-server
pip install -e .
export GOOGLE_CLIENT_SECRETS_PATH=$PWD/client_secret.json
export GTASKS_TOKEN_PATH=$PWD/gtasks_token.enc
export GTASKS_TOKEN_KEY=<the Fernet key you generated>
gtasks-auth auth
```

A browser opens, you accept the scope, the encrypted token lands in
`gtasks_token.enc`. Now base64 it and paste into Railway:

```bash
base64 -w0 gtasks_token.enc          # Linux
base64 -i  gtasks_token.enc | pbcopy # macOS
base64 -w0 client_secret.json        # repeat for the client secret
```

In Railway → second-brain → Variables, set:

```
GTASKS_TOKEN_B64=<base64 of gtasks_token.enc>
GOOGLE_CLIENT_SECRETS_B64=<base64 of client_secret.json>
GTASKS_TOKEN_KEY=<your Fernet key>
```

The `entrypoint.sh` decodes both back to files on container start.

## Step 6 — Connect Obsidian LiveSync

On every device (Mac, iPhone, iPad):

1. Install the **Self-hosted LiveSync** plugin from Community Plugins.
2. Settings → Self-hosted LiveSync → Setup wizard:
   - **Remote URI**: `https://<your-couchdb-public-url>` — but Couchdb on
     Railway is **private by default**. Two options:

     a) **Recommended**: keep CouchDB private. Add a public domain to
        the **`couchdb` service** in Railway too, with TLS. The plugin
        will hit `https://couchdb-<id>.up.railway.app`.

     b) Use the bridge as a reverse-proxy to CouchDB on the
        `second-brain` service (not implemented in this repo — would
        need an extra route).
   - **Username**: `admin`
   - **Password**: the COUCHDB_PASSWORD
   - **Database name**: `obsidian`
3. Plugin settings → **Sync settings**:
   - LiveSync ON (real-time bidirectional)
   - Periodic Sync: 30s as fallback
   - Use database suffix: leave empty
4. Plugin settings → **Behavior** (important — keeps the bridge simple):
   - Disable chunk-split: **ON** if available
   - End-to-end encryption: optional; if you turn it on, the bridge will
     not be able to read content (encrypted blobs only). **Leave it OFF**
     for now, unless you're prepared to do client-side decryption in the
     bridge.

After setup, the plugin pushes the existing vault to CouchDB. The bridge
in the `second-brain` service receives the changes feed and materializes
markdown files in `/data/vault`. The watcher picks them up and indexes
them in Qdrant.

## Step 7 — Register the connector in Claude.ai

1. Claude.ai → Settings → Connectors → **Add custom connector**.
2. URL: `https://<your-second-brain-public-url>/mcp`
3. Auth: **Bearer**, token = your `BEARER_TOKEN`.
4. Save. The 16+ tools should appear in the conversation tool picker.

## Step 8 — Configure MeetGeek

1. MeetGeek → Settings → Integrations → Webhooks → **Add Webhook**.
2. URL: `https://<your-second-brain-public-url>/meetgeek/webhook`
3. Auth header: `Authorization: Bearer <MEETGEEK_WEBHOOK_SECRET>`
4. Event: meeting completed.
5. Test delivery → check Railway logs for `meetgeek_processed`.

## Verifying

Once everything is up:

```bash
# Public health check
curl https://<your-app>/health
# {"ok": true}

# Auth must reject unknown bearers
curl -i https://<your-app>/mcp
# HTTP/2 401

# Bearer must succeed
curl -i -H "Authorization: Bearer $BEARER_TOKEN" https://<your-app>/mcp
# HTTP/2 200 (Streamable HTTP)
```

In Claude.ai, ask: *"Welche aktiven Projekte habe ich?"* — the
`list_active_projects` tool should be invoked. *"Lass uns über
ChapterNext reden"* — `get_living_doc` returns the file contents.

## Costs (rough)

Railway's **Hobby plan** ($5/month) covers all three services for
small-to-medium personal use. Estimate (depends on usage):

| Service | RAM | Roughly |
|---|---|---|
| Qdrant | ~512 MB | $3/mo |
| CouchDB | ~256 MB | $2/mo |
| second-brain | ~600 MB | $4/mo |
| **Total** | ~1.4 GB | **$9–12/mo** |

Plus Voyage AI usage (very cheap for a personal vault — first 200M
tokens/month free as of writing).

## Troubleshooting

**Watcher doesn't see files when CouchDB has them**

Bridge isn't running or failed to reconcile. Check
`livesync-bridge` logs in Railway. Most common: wrong `COUCHDB_USER` /
`COUCHDB_PASSWORD`, or the database `obsidian` doesn't exist yet.

**Files appear, but nothing in Qdrant**

`watcher` failed. Most common: bad `VOYAGE_API_KEY` or `QDRANT_URL`
(check the `${{Qdrant.RAILWAY_PRIVATE_DOMAIN}}` resolves to the actual
service name).

**Edits on iPhone don't make it to the vault**

LiveSync plugin isn't syncing. Check the plugin's status panel in
Obsidian. Common: chunk-encryption ON (we don't decrypt) or remote URI
points to a service without TLS.

**Echo loop (file rewrites itself rapidly)**

Bridge's echo suppression failed for some reason — usually because two
services have different views of the same file (e.g. both LiveSync and
git plugin trying to manage the same vault). Disable one of them.

**Re-deploy resets the vault**

The volume should persist across deploys. If it doesn't: confirm
**Volumes** is configured (not just `--volume` in a CMD), and that the
volume is attached to the right service.

## Backups

Railway snapshots volumes daily on paid plans. For an external safety net,
schedule a CouchDB replication to a free tier IBM Cloudant or another
CouchDB instance — config in the LiveSync plugin under "Replication
target". Or run `couchdb-dump` from a cron worker once a day.

## What this deployment does NOT do

- **End-to-end encryption**: the bridge sees plaintext. If you turn on
  E2EE in the LiveSync plugin, the bridge can't read or write — you'd
  need to extend `encoding.py` to handle the plugin's encryption (see
  the plugin source). Not currently in scope.
- **Multi-user**: one Claude account, one set of devices. The vault is
  shared, not segregated.
- **Conflict UI**: when you edit the same line on two devices and both
  push, CouchDB picks a winner. The bridge does not currently surface
  conflict markers.
