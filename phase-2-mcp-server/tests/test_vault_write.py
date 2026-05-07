"""Vault write tools: append_to_living_doc, update_section, create_*."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_server.config import Settings
from mcp_server.tools._common import ServerContext
from mcp_server.tools import vault_write


def _ctx(vault: Path) -> ServerContext:
    settings = Settings(vault_path=vault, voyage_api_key="x")
    return ServerContext(
        settings=settings,
        index=MagicMock(),
        voyage=MagicMock(),
        rerank_cache=MagicMock(),
    )


def test_append_to_living_doc(fixture_vault: Path) -> None:
    ctx = _ctx(fixture_vault)
    res = vault_write.append_to_living_doc(
        ctx, project="ChapterNext", section="Recent Insights", content="neuer Insight"
    )
    assert res.get("ok") is True
    body = (fixture_vault / "10_Projects" / "ChapterNext.md").read_text()
    assert "neuer Insight" in body
    assert "## Recent Insights" in body


def test_append_creates_missing_section(fixture_vault: Path) -> None:
    ctx = _ctx(fixture_vault)
    res = vault_write.append_to_living_doc(
        ctx, project="ChapterNext", section="Brand New", content="erste Zeile"
    )
    assert res.get("ok") is True
    body = (fixture_vault / "10_Projects" / "ChapterNext.md").read_text()
    assert "## Brand New" in body
    assert "erste Zeile" in body


def test_append_to_person_updates_last_interaction(fixture_vault: Path) -> None:
    ctx = _ctx(fixture_vault)
    res = vault_write.append_to_person(
        ctx, name="Anna Schmidt", section="History", content="[[meeting/x]]"
    )
    assert res.get("ok") is True
    body = (fixture_vault / "70_People" / "Anna Schmidt.md").read_text()
    assert "[[meeting/x]]" in body
    assert "last_interaction:" in body


def test_update_person_meta_merges_tags(fixture_vault: Path) -> None:
    ctx = _ctx(fixture_vault)
    res = vault_write.update_person_meta(
        ctx, name="Anna Schmidt", fields={"tags": ["VIP", "client"], "role": "VP Marketing"}
    )
    assert res.get("ok") is True
    body = (fixture_vault / "70_People" / "Anna Schmidt.md").read_text()
    # role overwritten
    assert "VP Marketing" in body
    # tags merged + deduped
    assert "VIP" in body
    assert body.count("- client") == 1


def test_create_person_creates_file(fixture_vault: Path) -> None:
    ctx = _ctx(fixture_vault)
    res = vault_write.create_person(
        ctx,
        name="Stefan Müller",
        frontmatter_data={
            "role": "Engineer",
            "company": "Acme",
            "email": "stefan@acme.io",
        },
    )
    assert res.get("ok") is True
    target = fixture_vault / "70_People" / "Stefan Müller.md"
    assert target.is_file()
    content = target.read_text()
    assert "type: person" in content
    assert "Engineer" in content


def test_create_note_rejects_existing(fixture_vault: Path) -> None:
    ctx = _ctx(fixture_vault)
    res = vault_write.create_note(
        ctx,
        path="10_Projects/ChapterNext.md",
        frontmatter_data={
            "type": "living",
            "project": "ChapterNext",
            "created": "2026-04-25",
            "updated": "2026-04-25",
        },
        content="x",
    )
    assert res.get("code") == "EXISTS"


def test_create_note_validates_frontmatter(fixture_vault: Path) -> None:
    ctx = _ctx(fixture_vault)
    res = vault_write.create_note(
        ctx,
        path="10_Projects/Bogus.md",
        frontmatter_data={"type": "living", "project": "B", "status": "wat"},
        content="x",
    )
    assert res.get("code") == "INVALID_FRONTMATTER"


# --- move_note / delete_note ----------------------------------------------
# These use a self-contained tmp_path layout (no fixture_vault dependency)
# so they run even when the fixture vault directory is absent.


def _seed_note(root: Path, rel: str, body: str = "x") -> Path:
    abs_path = root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(body)
    return abs_path


def test_move_note_renames_file(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md", "anna")
    res = vault_write.move_note(
        ctx, src="70_People/Anna Schmidt.md", dst="70_People/Anna S.md"
    )
    assert res.get("ok") is True
    assert not (tmp_path / "70_People" / "Anna Schmidt.md").exists()
    assert (tmp_path / "70_People" / "Anna S.md").read_text() == "anna"


def test_move_note_to_new_subdir(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md")
    res = vault_write.move_note(
        ctx,
        src="70_People/Anna Schmidt.md",
        dst="80_Archive/Anna Schmidt.md",
    )
    assert res.get("ok") is True
    assert (tmp_path / "80_Archive" / "Anna Schmidt.md").is_file()


def test_move_note_rejects_existing_dst(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md", "src")
    _seed_note(tmp_path, "10_Projects/ChapterNext.md", "existing")
    res = vault_write.move_note(
        ctx,
        src="70_People/Anna Schmidt.md",
        dst="10_Projects/ChapterNext.md",
    )
    assert res.get("code") == "EXISTS"
    # Both files untouched
    assert (tmp_path / "70_People" / "Anna Schmidt.md").read_text() == "src"
    assert (tmp_path / "10_Projects" / "ChapterNext.md").read_text() == "existing"


def test_move_note_force_overwrites(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md", "new")
    _seed_note(tmp_path, "10_Projects/ChapterNext.md", "old")
    res = vault_write.move_note(
        ctx,
        src="70_People/Anna Schmidt.md",
        dst="10_Projects/ChapterNext.md",
        force=True,
    )
    assert res.get("ok") is True
    assert (tmp_path / "10_Projects" / "ChapterNext.md").read_text() == "new"
    assert not (tmp_path / "70_People" / "Anna Schmidt.md").exists()


def test_move_note_missing_src(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = vault_write.move_note(
        ctx, src="70_People/Nobody.md", dst="70_People/Somebody.md"
    )
    assert res.get("code") == "NOT_FOUND"


def test_move_note_blocks_traversal(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md")
    res = vault_write.move_note(
        ctx, src="70_People/Anna Schmidt.md", dst="../escape.md"
    )
    assert res.get("code") == "INVALID_PATH"
    # Source untouched
    assert (tmp_path / "70_People" / "Anna Schmidt.md").is_file()


def test_move_note_same_path(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md")
    res = vault_write.move_note(
        ctx,
        src="70_People/Anna Schmidt.md",
        dst="70_People/Anna Schmidt.md",
    )
    assert res.get("code") == "INVALID_PATH"
    assert (tmp_path / "70_People" / "Anna Schmidt.md").is_file()


def test_delete_note_soft_moves_to_trash(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md", "body")
    res = vault_write.delete_note(ctx, path="70_People/Anna Schmidt.md")
    assert res.get("ok") is True
    assert res.get("hard") is False
    assert not (tmp_path / "70_People" / "Anna Schmidt.md").exists()
    trash_rel = res["trash_path"]
    assert trash_rel.startswith(".trash/")
    trash_abs = tmp_path / trash_rel
    assert trash_abs.is_file()
    assert trash_abs.read_text() == "body"
    # Flattened original path is preserved in the filename
    assert "70_People__Anna Schmidt.md" in trash_rel


def test_delete_note_hard_unlinks(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md")
    res = vault_write.delete_note(
        ctx, path="70_People/Anna Schmidt.md", hard=True
    )
    assert res.get("ok") is True
    assert res.get("hard") is True
    assert not (tmp_path / "70_People" / "Anna Schmidt.md").exists()
    # No trash artefact created
    assert not (tmp_path / ".trash").exists()


def test_delete_note_missing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = vault_write.delete_note(ctx, path="70_People/Nobody.md")
    assert res.get("code") == "NOT_FOUND"


def test_delete_note_blocks_traversal(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = vault_write.delete_note(ctx, path="../etc/passwd")
    assert res.get("code") == "INVALID_PATH"


def test_delete_note_already_trashed_blocked_without_hard(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _seed_note(tmp_path, "70_People/Anna Schmidt.md")
    first = vault_write.delete_note(ctx, path="70_People/Anna Schmidt.md")
    trash_rel = first["trash_path"]
    # Second soft-delete attempt on the trashed file should refuse
    second = vault_write.delete_note(ctx, path=trash_rel)
    assert second.get("code") == "ALREADY_TRASHED"
    assert (tmp_path / trash_rel).is_file()
    # hard=True does purge the trashed file
    purge = vault_write.delete_note(ctx, path=trash_rel, hard=True)
    assert purge.get("ok") is True
    assert not (tmp_path / trash_rel).exists()


def test_concurrent_appends_no_loss(fixture_vault: Path) -> None:
    """100 appends to the same Living Doc should produce 100 entries with no
    corruption (each entry must appear in the final file)."""
    ctx = _ctx(fixture_vault)

    barrier = threading.Barrier(20)
    errors: list[str] = []

    def worker(i: int) -> None:
        barrier.wait()
        for j in range(5):
            res = vault_write.append_to_living_doc(
                ctx,
                project="ChapterNext",
                section="Recent Insights",
                content=f"entry-{i}-{j}",
            )
            if not res.get("ok"):
                errors.append(str(res))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    body = (fixture_vault / "10_Projects" / "ChapterNext.md").read_text()
    found = sum(1 for i in range(20) for j in range(5) if f"entry-{i}-{j}" in body)
    assert found == 100
