"""Lock-and-atomic-write helpers for Living Doc + Person edits.

Uses a two-layer lock:

1. An in-process `threading.Lock` keyed by absolute path. Required because
   we use `os.replace` which makes per-fd fcntl locks meaningless across
   threads (each thread opens a distinct inode after a replace).
2. A sentinel `<file>.lock` fcntl lock that is never replaced. Required for
   safety across multiple processes.
"""

from __future__ import annotations

import contextlib
import errno
import os
import threading
import time
from pathlib import Path
from typing import Iterator


class ConflictError(RuntimeError):
    """Raised when a file's mtime moved between read and write."""


_lock_registry: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _process_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _registry_lock:
        lock = _lock_registry.get(key)
        if lock is None:
            lock = threading.Lock()
            _lock_registry[key] = lock
        return lock


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 5.0) -> Iterator[int]:
    """Acquire an in-process + cross-process exclusive lock around `path`.

    The cross-process lock is held on a sibling `<name>.lock` file that is
    never replaced, so all writers contend on the same inode.
    """
    proc_lock = _process_lock(path)
    proc_lock.acquire()

    sentinel = path.with_suffix(path.suffix + ".lock")
    sentinel.parent.mkdir(parents=True, exist_ok=True)

    try:
        import fcntl
    except ImportError:
        try:
            yield -1
        finally:
            proc_lock.release()
        return

    fd = os.open(str(sentinel), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.time() > deadline:
                    raise TimeoutError(f"lock timeout: {path}") from e
                time.sleep(0.01)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
        proc_lock.release()


def atomic_write(path: Path, content: bytes) -> None:
    """Atomically replace `path` with `content`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}"
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


def safe_overwrite(
    path: Path,
    content: bytes,
    *,
    captured_mtime_ns: int,
) -> None:
    """Atomically replace `path`, raising ConflictError if mtime advanced."""
    if path.exists():
        actual = path.stat().st_mtime_ns
        if actual != captured_mtime_ns:
            raise ConflictError(
                f"file modified since read: {path} "
                f"(captured={captured_mtime_ns}, actual={actual})"
            )
    atomic_write(path, content)
