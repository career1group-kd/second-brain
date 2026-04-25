"""Tests for init_vault.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import init_vault  # noqa: E402


def test_run_creates_full_structure(tmp_path: Path) -> None:
    stats = init_vault.run(tmp_path, ["ChapterNext", "C1G Sidebars"], force=False)

    expected_dirs = [
        "00_Inbox",
        "10_Projects",
        "20_Areas",
        "30_Resources",
        "40_Archive",
        "50_Daily",
        "50_Daily/meetings",
        "60_MOCs",
        "70_People",
        "99_Meta",
        "99_Meta/Templates",
    ]
    for d in expected_dirs:
        assert (tmp_path / d).is_dir(), f"missing dir: {d}"

    expected_files = [
        "99_Meta/Templates/living-doc.md",
        "99_Meta/Templates/meeting.md",
        "99_Meta/Templates/person.md",
        "99_Meta/Templates/daily.md",
        "99_Meta/Templates/resource.md",
        "99_Meta/Vault-Convention.md",
        "99_Meta/DSGVO-People-Convention.md",
        ".gitignore",
        "README.md",
        "10_Projects/ChapterNext.md",
        "10_Projects/C1G Sidebars.md",
    ]
    for f in expected_files:
        assert (tmp_path / f).is_file(), f"missing file: {f}"

    assert stats["written"] >= len(expected_files)
    assert stats["skipped"] == 0


def test_run_is_idempotent(tmp_path: Path) -> None:
    init_vault.run(tmp_path, ["ChapterNext"], force=False)
    second = init_vault.run(tmp_path, ["ChapterNext"], force=False)
    assert second["written"] == 0
    assert second["skipped"] > 0


def test_force_overwrites(tmp_path: Path) -> None:
    init_vault.run(tmp_path, ["ChapterNext"], force=False)
    target = tmp_path / "10_Projects" / "ChapterNext.md"
    target.write_text("# manually edited\n", encoding="utf-8")
    init_vault.run(tmp_path, ["ChapterNext"], force=True)
    assert "type: living" in target.read_text(encoding="utf-8")


def test_living_doc_has_required_sections(tmp_path: Path) -> None:
    init_vault.run(tmp_path, ["ChapterNext"], force=False)
    body = (tmp_path / "10_Projects" / "ChapterNext.md").read_text(encoding="utf-8")
    for section in [
        "## Status & Kontext",
        "## Architektur & Entscheidungen",
        "## Offene Fragen",
        "## Recent Insights",
        "## TODOs",
        "## Conversation Log",
    ]:
        assert section in body


def test_empty_projects_list(tmp_path: Path) -> None:
    stats = init_vault.run(tmp_path, [], force=False)
    assert stats["written"] > 0
    assert not list((tmp_path / "10_Projects").iterdir())


def test_main_cli(tmp_path: Path, capsys) -> None:
    rc = init_vault.main(
        ["--output", str(tmp_path), "--projects", "ChapterNext"],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Vault initialized" in out
