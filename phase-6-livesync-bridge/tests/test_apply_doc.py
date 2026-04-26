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


async def test_apply_doc_skips_livesync_metadata(bridge: LiveSyncBridge) -> None:
    await bridge.apply_doc({"_id": "obsydian_livesync_version"}, deleted=False)
    await bridge.apply_doc({"_id": "_local/foo"}, deleted=False)
    files = list(bridge.settings.vault_path.rglob("*"))
    assert files == []


async def test_apply_doc_bareformat_uses_doc_path(bridge: LiveSyncBridge) -> None:
    """New livesync stores the lowercased path as ID and original-case
    path in the doc body. The bridge must use the body's `path`."""
    doc = {
        "_id": "willkommen.md",
        "path": "Willkommen.md",
        "type": "plain",
        "data": "# Willkommen\n",
    }
    await bridge.apply_doc(doc, deleted=False)
    target = bridge.settings.vault_path / "Willkommen.md"
    assert target.exists()
    assert target.read_text() == "# Willkommen\n"


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
    # push_path writes one leaf chunk doc, then the head doc.
    assert bridge.couch.put_doc.await_count == 2
    leaf, head = (call.args[0] for call in bridge.couch.put_doc.await_args_list)
    assert leaf["type"] == "leaf"
    assert leaf["_id"].startswith("h:")
    assert leaf["data"] == "# Anna\n"
    # Modern obsidian-livesync expects the lowercased path as the head ID,
    # `type: "plain"`, and the content referenced via `children`.
    assert head["_id"] == "70_people/anna.md"
    assert head["path"] == "70_People/Anna.md"
    assert head["type"] == "plain"
    assert head["children"] == [leaf["_id"]]
    assert "data" not in head


async def test_push_path_skips_existing_leaf(bridge: LiveSyncBridge) -> None:
    # Content-addressed leaves should not be re-written if they already
    # exist in CouchDB — only the head doc gets a new revision.
    target = bridge.settings.vault_path / "a.md"
    target.write_text("hello")

    async def fake_get(doc_id: str) -> dict | None:
        if doc_id.startswith("h:"):
            return {"_id": doc_id, "_rev": "1-leaf", "type": "leaf", "data": "hello"}
        return None

    bridge.couch.get_doc = AsyncMock(side_effect=fake_get)
    await bridge.push_path("a.md")
    assert bridge.couch.put_doc.await_count == 1
    head = bridge.couch.put_doc.await_args.args[0]
    assert head["type"] == "plain"


async def test_push_path_with_legacy_prefix(tmp_path: Path) -> None:
    settings = Settings(
        couchdb_url="http://couchdb:5984",
        couchdb_user="admin",
        couchdb_password="x",
        couchdb_file_prefix="f:",
        vault_path=tmp_path / "vault",
        state_path=tmp_path / "state.json",
    )
    settings.vault_path.mkdir()
    b = LiveSyncBridge(settings)
    b.couch.aclose = AsyncMock()
    b.couch.get_doc = AsyncMock(return_value=None)
    b.couch.put_doc = AsyncMock(return_value={"ok": True, "rev": "1-x"})
    target = settings.vault_path / "70_People" / "Anna.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Anna\n")
    await b.push_path("70_People/Anna.md")
    head = b.couch.put_doc.await_args_list[-1].args[0]
    assert head["_id"] == "f:70_People/Anna.md"


async def test_push_path_includes_existing_rev(bridge: LiveSyncBridge) -> None:
    bridge.couch.get_doc = AsyncMock(
        return_value={"_id": "a.md", "_rev": "3-abc", "type": "plain"}
    )
    target = bridge.settings.vault_path / "a.md"
    target.write_text("new")
    await bridge.push_path("a.md")
    # The rev belongs on the head doc (the file-id doc), which is the
    # last put_doc call.
    head = bridge.couch.put_doc.await_args_list[-1].args[0]
    assert head["_id"] == "a.md"
    assert head["_rev"] == "3-abc"


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
