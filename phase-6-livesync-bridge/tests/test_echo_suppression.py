"""Echo suppression behavior."""

from __future__ import annotations

import time

from livesync_bridge.bridge import _EchoSuppressor


def test_remember_then_match_short_window() -> None:
    s = _EchoSuppressor(ttl=10.0)
    s.remember("a/b.md", "abc")
    assert s.matches("a/b.md", "abc") is True


def test_does_not_match_different_hash() -> None:
    s = _EchoSuppressor(ttl=10.0)
    s.remember("a/b.md", "abc")
    assert s.matches("a/b.md", "def") is False


def test_does_not_match_different_path() -> None:
    s = _EchoSuppressor(ttl=10.0)
    s.remember("a/b.md", "abc")
    assert s.matches("a/c.md", "abc") is False


def test_expires_after_ttl() -> None:
    s = _EchoSuppressor(ttl=0.0)
    s.remember("a/b.md", "abc")
    time.sleep(0.01)
    assert s.matches("a/b.md", "abc") is False


def test_gc_drops_expired_entries() -> None:
    s = _EchoSuppressor(ttl=0.0)
    s.remember("a/b.md", "x")
    time.sleep(0.01)
    # Trigger _gc by remembering another entry.
    s.remember("c/d.md", "y")
    assert "a/b.md" not in s.entries
