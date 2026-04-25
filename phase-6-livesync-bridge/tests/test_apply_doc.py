"""End-to-end-ish tests for apply_doc + push_path using a mocked CouchDB."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from livesync_bridge.bridge import LiveSyncBridge
from livesync_bridge.config import Settings


@pytest.fixture
def bridge(tmp_path: Path) -> LiveSyncBridge:
    settings = Settings(
        couchdb_url="http://couchdb:5984",
        couchdb_user="admin",
        couchdb_password="x",
        vault_path=tmp_path / "vault",
        state_path=tmp_path / "state.json",
    )
    settings.vault_path.mkdir()
    b = LiveSyncBridge(settings)
    b.couch.aclose = AsyncMock()
    b.couch.get_doc = AsyncMock(return_value=None)
    b.couch.put_doc = AsyncMock(return_value={"ok": True, "rev": "1-x"})
    b.couch.delete_doc = AsyncMock()
    b.couch.get_docs_bulk = AsyncMock(return_value=[])
    return b


async def test_apply_doc_writes_file(bridge: LiveSyncBridge) -> None:
    doc = {
        "_id": "f:10_Projects/X.md",
        "type": "plain",
        "data": "# X\n\nbody\n",
    }
    await bridge.apply_doc(doc, deleted=False)
    target = bridge.settings.vault_path / "10_Projects" / "X.md"
    assert target.exists()
    assert target.read_text() == "# X\n\nbody\n"


async def test_apply_doc_deletes_file(bridge: LiveSyncBridge) -> None:
    target = bridge.settings.vault_path / "10_Projects" / "Y.md"
    target.parent.mkdir(parents=True)
    target.write_text("doomed")
    await bridge.apply_doc({"_id": "f:10_Projects/Y.md"}, deleted=True)
    assert not target.exists()


async def test_apply_doc_skips_chunk_only_docs(bridge: LiveSyncBridge) -> None:
    await bridge.apply_doc({"_id": "h:abc"}, deleted=False)
    files = list(bridge.settings.vault_path.rglob("*"))
    assert files == []


async def test_apply_doc_excluded_paths_skipped(bridge: LiveSyncBridge) -> None:
    await bridge.apply_doc(
        {"_id": "f:.obsidian/workspace.json", "type": "plain", "data": "{}"},
        deleted=False,
    )
    assert not (bridge.settings.vault_path / ".obsidian" / "workspace.json").exists()


async def test_push_path_creates_doc(bridge: LiveSyncBridge) -> None:
    target = bridge.settings.vault_path / "70_People" / "Anna.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Anna\n")
    await bridge.push_path("70_People/Anna.md")
    bridge.couch.put_doc.assert_awaited_once()
    body = bridge.couch.put_doc.await_args.args[0]
    assert body["_id"] == "f:70_People/Anna.md"
    assert body["data"] == "# Anna\n"


async def test_push_path_includes_existing_rev(bridge: LiveSyncBridge) -> None:
    bridge.couch.get_doc = AsyncMock(
        return_value={"_id": "f:a.md", "_rev": "3-abc", "data": "old"}
    )
    target = bridge.settings.vault_path / "a.md"
    target.write_text("new")
    await bridge.push_path("a.md")
    body = bridge.couch.put_doc.await_args.args[0]
    assert body["_rev"] == "3-abc"


async def test_apply_then_push_does_not_echo(bridge: LiveSyncBridge) -> None:
    """A doc applied from CouchDB should not be pushed back when its file
    materialises and the watcher fires."""
    doc = {"_id": "f:loop.md", "type": "plain", "data": "loop body"}
    await bridge.apply_doc(doc, deleted=False)
    # Now simulate the watcher seeing the freshly written file.
    await bridge.push_path("loop.md")
    bridge.couch.put_doc.assert_not_awaited()


async def test_push_then_apply_does_not_echo(bridge: LiveSyncBridge) -> None:
    """A file pushed to CouchDB should not be re-written when CouchDB
    echoes the same content back via the changes feed."""
    target = bridge.settings.vault_path / "echo.md"
    target.write_text("hello")
    await bridge.push_path("echo.md")
    target.write_text("intermediate")
    await bridge.apply_doc(
        {"_id": "f:echo.md", "type": "plain", "data": "hello"},
        deleted=False,
    )
    # File should remain at "intermediate" — apply was suppressed.
    assert target.read_text() == "intermediate"
