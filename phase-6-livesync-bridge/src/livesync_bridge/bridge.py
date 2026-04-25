"""Bidirectional CouchDB <-> filesystem bridge.

Two pumps run concurrently:

* **CouchDB → FS**: subscribe to the `_changes` feed; for every changed
  document, write/delete the corresponding file under `vault_path`.
* **FS → CouchDB**: watchdog observer detects local file changes (writes
  initiated by the MCP server tools, mostly) and pushes them as new
  revisions to CouchDB.

Echo suppression: every outbound write to either side is recorded with a
content hash and a timestamp. If the opposite pump fires for the same path
with the same hash within `echo_suppress_seconds`, the event is dropped.
That keeps device → server → file → CouchDB → device loops from oscillating.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from . import encoding
from .config import Settings
from .couchdb import CouchDB

log = structlog.get_logger()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _EchoSuppressor:
    ttl: float
    entries: dict[str, tuple[str, float]] = field(default_factory=dict)

    def remember(self, path: str, content_hash: str) -> None:
        self.entries[path] = (content_hash, time.monotonic())
        self._gc()

    def matches(self, path: str, content_hash: str) -> bool:
        record = self.entries.get(path)
        if not record:
            return False
        h, ts = record
        if time.monotonic() - ts > self.ttl:
            self.entries.pop(path, None)
            return False
        return h == content_hash

    def _gc(self) -> None:
        now = time.monotonic()
        stale = [k for k, (_, ts) in self.entries.items() if now - ts > self.ttl]
        for k in stale:
            self.entries.pop(k, None)


class _FsHandler(FileSystemEventHandler):
    """Pushes coalesced (path, op) tuples onto an asyncio queue."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue,
        vault: Path,
        excluded_top_level: list[str],
    ) -> None:
        self.loop = loop
        self.queue = queue
        self.vault = vault
        self.excluded_top_level = excluded_top_level

    def _is_excluded(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.vault)
        except ValueError:
            return True
        if not rel.parts:
            return True
        first = rel.parts[0]
        for ex in self.excluded_top_level:
            if first == ex or rel.as_posix().startswith(ex + "/"):
                return True
        return False

    def _enqueue(self, op: str, src: str, dst: str | None = None) -> None:
        path = Path(src)
        if self._is_excluded(path):
            return
        if path.suffix.lower() != ".md":
            return
        try:
            rel = path.relative_to(self.vault).as_posix()
        except ValueError:
            return
        rel_dst = None
        if dst is not None:
            d = Path(dst)
            if not self._is_excluded(d) and d.suffix.lower() == ".md":
                try:
                    rel_dst = d.relative_to(self.vault).as_posix()
                except ValueError:
                    rel_dst = None
        self.loop.call_soon_threadsafe(
            self.queue.put_nowait, (op, rel, rel_dst)
        )

    def on_created(self, event):
        if isinstance(event, FileCreatedEvent):
            self._enqueue("upsert", event.src_path)

    def on_modified(self, event):
        if isinstance(event, FileModifiedEvent):
            self._enqueue("upsert", event.src_path)

    def on_deleted(self, event):
        if isinstance(event, FileDeletedEvent):
            self._enqueue("delete", event.src_path)

    def on_moved(self, event):
        if isinstance(event, FileMovedEvent):
            self._enqueue("delete", event.src_path)
            self._enqueue("upsert", event.dest_path)


class LiveSyncBridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.couch = CouchDB(
            url=settings.couchdb_url,
            db=settings.couchdb_db,
            user=settings.couchdb_user,
            password=settings.couchdb_password,
        )
        self.fs_to_db = _EchoSuppressor(ttl=settings.echo_suppress_seconds)
        self.db_to_fs = _EchoSuppressor(ttl=settings.echo_suppress_seconds)

    # --- state persistence --------------------------------------------------

    def _read_since(self) -> str:
        try:
            data = json.loads(self.settings.state_path.read_text())
            return str(data.get("since", "0"))
        except (FileNotFoundError, json.JSONDecodeError):
            return "0"

    def _write_since(self, since: str) -> None:
        self.settings.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.state_path.write_text(json.dumps({"since": since}))

    # --- helpers ------------------------------------------------------------

    def _is_path_excluded(self, path: str) -> bool:
        for ex in self.settings.excluded_top_level:
            if path == ex or path.startswith(ex + "/"):
                return True
        return False

    async def _resolve_chunks(
        self, head: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        children = head.get("children")
        if not isinstance(children, list) or not children:
            return {}
        docs = await self.couch.get_docs_bulk(children)
        return {c: d for c, d in zip(children, docs) if d is not None}

    async def _materialize(self, doc: dict[str, Any]) -> bytes:
        chunks = await self._resolve_chunks(doc)
        return encoding.reassemble(doc, chunk_resolver=lambda cid: chunks.get(cid))

    def _abs(self, rel: str) -> Path:
        return self.settings.vault_path / rel

    # --- CouchDB -> FS ------------------------------------------------------

    async def apply_doc(self, doc: dict[str, Any], deleted: bool) -> None:
        doc_id = doc.get("_id", "")
        if not encoding.is_file_doc(doc_id):
            return
        path = encoding.doc_id_to_path(doc_id)
        if path is None or self._is_path_excluded(path):
            return

        target = self._abs(path)
        if deleted:
            if target.exists():
                target.unlink()
                log.info("livesync_fs_delete", path=path)
            return

        content = await self._materialize(doc)
        h = _hash(content)
        if self.fs_to_db.matches(path, h):
            # We just pushed this exact content — skip echo.
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self.db_to_fs.remember(path, h)
        log.info("livesync_fs_write", path=path, bytes=len(content))

    async def couch_to_fs_loop(self) -> None:
        since = self._read_since()
        log.info("livesync_changes_start", since=since)
        try:
            async for change in self.couch.changes_continuous(since=since):
                seq = str(change.get("seq", ""))
                doc = change.get("doc")
                if doc is None:
                    self._write_since(seq)
                    continue
                try:
                    await self.apply_doc(doc, bool(change.get("deleted")))
                except Exception:
                    log.exception("livesync_apply_failed", id=doc.get("_id"))
                if seq:
                    self._write_since(seq)
        except Exception:
            log.exception("livesync_changes_loop_failed")
            raise

    # --- FS -> CouchDB ------------------------------------------------------

    async def push_path(self, rel: str) -> None:
        target = self._abs(rel)
        if not target.exists():
            return
        if not encoding.is_markdown_path(rel):
            return
        content = target.read_bytes()
        h = _hash(content)
        if self.db_to_fs.matches(rel, h):
            return  # echo

        doc_id = encoding.path_to_doc_id(rel)
        existing = await self.couch.get_doc(doc_id)
        body = encoding.render_plain(content)
        body["_id"] = doc_id
        if existing and "_rev" in existing:
            body["_rev"] = existing["_rev"]

        await self.couch.put_doc(body)
        self.fs_to_db.remember(rel, h)
        log.info("livesync_couch_write", path=rel, bytes=len(content))

    async def delete_path(self, rel: str) -> None:
        doc_id = encoding.path_to_doc_id(rel)
        existing = await self.couch.get_doc(doc_id)
        if not existing or "_rev" not in existing:
            return
        await self.couch.delete_doc(doc_id, existing["_rev"])
        log.info("livesync_couch_delete", path=rel)

    async def fs_to_couch_loop(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        handler = _FsHandler(
            loop=loop,
            queue=queue,
            vault=self.settings.vault_path,
            excluded_top_level=self.settings.excluded_top_level,
        )
        observer = Observer()
        self.settings.vault_path.mkdir(parents=True, exist_ok=True)
        observer.schedule(handler, str(self.settings.vault_path), recursive=True)
        observer.start()
        log.info("livesync_fs_watcher_started", path=str(self.settings.vault_path))

        debounce: dict[str, asyncio.TimerHandle] = {}

        async def process(op: str, rel: str) -> None:
            try:
                if op == "upsert":
                    await self.push_path(rel)
                elif op == "delete":
                    await self.delete_path(rel)
            except Exception:
                log.exception("livesync_push_failed", op=op, path=rel)

        try:
            while True:
                op, rel, rel_dst = await queue.get()

                def _schedule(op_: str, rel_: str) -> None:
                    handle = debounce.pop(rel_, None)
                    if handle is not None:
                        handle.cancel()
                    delay = self.settings.debounce_seconds
                    debounce[rel_] = loop.call_later(
                        delay,
                        lambda: asyncio.create_task(process(op_, rel_)),
                    )

                _schedule(op, rel)
                if rel_dst:
                    _schedule("upsert", rel_dst)
        finally:
            observer.stop()
            observer.join()

    # --- Reconciliation -----------------------------------------------------

    async def reconcile_initial(self) -> None:
        """Pull all docs once at startup so the FS reflects CouchDB.

        Outbound (FS -> Couch) reconciliation runs on demand: the watcher
        catches up new files as the watchdog scans them.
        """
        await self.couch.ensure_db()
        ids = await self.couch.all_doc_ids()
        file_ids = [i for i in ids if encoding.is_file_doc(i)]
        log.info("livesync_reconcile_start", count=len(file_ids))
        # Fetch in batches to keep memory steady.
        batch = self.settings.reconcile_batch
        for i in range(0, len(file_ids), batch):
            chunk = file_ids[i : i + batch]
            docs = await self.couch.get_docs_bulk(chunk)
            for doc in docs:
                if doc is None:
                    continue
                try:
                    await self.apply_doc(doc, deleted=False)
                except Exception:
                    log.exception("livesync_reconcile_failed", id=doc.get("_id"))
        log.info("livesync_reconcile_done", count=len(file_ids))

    # --- Entrypoint ---------------------------------------------------------

    async def run(self) -> None:
        try:
            await self.reconcile_initial()
            await asyncio.gather(
                self.couch_to_fs_loop(),
                self.fs_to_couch_loop(),
            )
        finally:
            await self.couch.aclose()
