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
