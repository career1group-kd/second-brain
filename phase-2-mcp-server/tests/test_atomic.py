"""Atomic write + conflict detection tests."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mcp_server.atomic import ConflictError, atomic_write, safe_overwrite


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "x.md"
    atomic_write(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "x.md"
    target.write_bytes(b"old")
    atomic_write(target, b"new")
    assert target.read_bytes() == b"new"


def test_safe_overwrite_detects_conflict(tmp_path: Path) -> None:
    target = tmp_path / "x.md"
    target.write_bytes(b"v1")
    captured = target.stat().st_mtime_ns
    target.write_bytes(b"v2")  # third party changes file
    with pytest.raises(ConflictError):
        safe_overwrite(target, b"v3", captured_mtime_ns=captured)


def test_safe_overwrite_writes_when_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "x.md"
    target.write_bytes(b"v1")
    captured = target.stat().st_mtime_ns
    safe_overwrite(target, b"v2", captured_mtime_ns=captured)
    assert target.read_bytes() == b"v2"


def test_concurrent_atomic_writes_no_corruption(tmp_path: Path) -> None:
    target = tmp_path / "x.md"
    target.write_bytes(b"start\n")

    barrier = threading.Barrier(20)

    def worker(i: int) -> None:
        barrier.wait()
        atomic_write(target, f"value-{i}\n".encode())

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    content = target.read_text()
    # Whatever lands, it must be one of the value-N writes (intact, no merge).
    assert content.startswith("value-")
    assert content.endswith("\n")
