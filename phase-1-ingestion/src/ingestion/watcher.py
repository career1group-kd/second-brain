"""Watchdog-based file watcher that debounces writes and reindexes on change."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import structlog
from watchdog.events import (
    DirDeletedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from .indexer import Indexer

log = structlog.get_logger()


class _Debouncer:
    def __init__(self, delay: float, callback) -> None:
        self.delay = delay
        self.callback = callback
        self.timers: dict[str, threading.Timer] = {}
        self.lock = threading.Lock()

    def schedule(self, key: str, *args) -> None:
        with self.lock:
            if key in self.timers:
                self.timers[key].cancel()
            t = threading.Timer(self.delay, self._fire, args=(key, args))
            t.daemon = True
            self.timers[key] = t
            t.start()

    def _fire(self, key: str, args: tuple) -> None:
        with self.lock:
            self.timers.pop(key, None)
        try:
            self.callback(*args)
        except Exception:
            log.exception("debounced_callback_failed", key=key)


class VaultEventHandler(FileSystemEventHandler):
    def __init__(self, indexer: Indexer, debounce_seconds: float) -> None:
        self.indexer = indexer
        self.debouncer = _Debouncer(debounce_seconds, self._reindex)

    def _relative(self, path: str) -> str:
        return Path(path).relative_to(self.indexer.settings.vault_path).as_posix()

    def _reindex(self, absolute: Path) -> None:
        if not self.indexer.is_indexable(absolute):
            return
        if not absolute.exists():
            return
        try:
            self.indexer.index_file(absolute)
        except Exception:
            log.exception("index_failed", path=str(absolute))

    def on_created(self, event) -> None:
        if isinstance(event, FileCreatedEvent):
            self.debouncer.schedule(str(event.src_path), Path(event.src_path))

    def on_modified(self, event) -> None:
        if isinstance(event, FileModifiedEvent):
            self.debouncer.schedule(str(event.src_path), Path(event.src_path))

    def on_deleted(self, event) -> None:
        if isinstance(event, (FileDeletedEvent, DirDeletedEvent)):
            try:
                rel = self._relative(event.src_path)
            except ValueError:
                return
            self.indexer.delete_file(rel)

    def on_moved(self, event) -> None:
        if isinstance(event, (FileMovedEvent, DirMovedEvent)):
            try:
                old_rel = self._relative(event.src_path)
                self.indexer.delete_file(old_rel)
            except ValueError:
                pass
            self.debouncer.schedule(str(event.dest_path), Path(event.dest_path))


def watch(indexer: Indexer) -> None:
    handler = VaultEventHandler(indexer, indexer.settings.debounce_seconds)
    observer = Observer()
    observer.schedule(handler, str(indexer.settings.vault_path), recursive=True)
    observer.start()
    log.info("watcher_started", path=str(indexer.settings.vault_path))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        log.info("watcher_stopped")
