"""Rerank cache tests."""

from __future__ import annotations

import time

from mcp_server.rerank_cache import RerankCache


def test_set_and_get_returns_cached() -> None:
    c = RerankCache()
    c.set("q", ["a", "b"], [(0, 0.9), (1, 0.5)])
    assert c.get("q", ["a", "b"]) == [(0, 0.9), (1, 0.5)]


def test_order_independent_lookup() -> None:
    c = RerankCache()
    c.set("q", ["a", "b"], [(0, 0.9)])
    assert c.get("q", ["b", "a"]) == [(0, 0.9)]


def test_miss_returns_none() -> None:
    c = RerankCache()
    assert c.get("q", ["a"]) is None


def test_eviction_at_maxsize() -> None:
    c = RerankCache(maxsize=2)
    c.set("q1", ["a"], [(0, 0.1)])
    c.set("q2", ["a"], [(0, 0.2)])
    c.set("q3", ["a"], [(0, 0.3)])
    assert c.get("q1", ["a"]) is None  # evicted
    assert c.get("q2", ["a"]) == [(0, 0.2)]
    assert c.get("q3", ["a"]) == [(0, 0.3)]


def test_ttl_expiry() -> None:
    c = RerankCache(ttl_seconds=0)
    c.set("q", ["a"], [(0, 0.9)])
    time.sleep(0.01)
    assert c.get("q", ["a"]) is None
