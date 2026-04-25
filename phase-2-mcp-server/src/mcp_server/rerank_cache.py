"""LRU + TTL cache for rerank results."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from threading import Lock


class RerankCache:
    def __init__(self, maxsize: int = 1000, ttl_seconds: int = 300) -> None:
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self._data: OrderedDict[str, tuple[float, list[tuple[int, float]]]] = OrderedDict()
        self._lock = Lock()

    @staticmethod
    def _key(query: str, doc_ids: list[str]) -> str:
        h = hashlib.sha256()
        h.update(query.encode("utf-8"))
        h.update(b"::")
        h.update("|".join(sorted(doc_ids)).encode("utf-8"))
        return h.hexdigest()

    def get(
        self,
        query: str,
        doc_ids: list[str],
    ) -> list[tuple[int, float]] | None:
        key = self._key(query, doc_ids)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.time() - ts > self.ttl:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(
        self,
        query: str,
        doc_ids: list[str],
        value: list[tuple[int, float]],
    ) -> None:
        key = self._key(query, doc_ids)
        with self._lock:
            self._data[key] = (time.time(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
