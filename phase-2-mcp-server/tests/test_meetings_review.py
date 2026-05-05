"""Tests for meeting-review tools."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from mcp_server.tools.meetings import (
    _replace_speaker_in_body,
    _utterance_samples,
    list_meetings_needing_review,
    replace_speaker_in_transcript,
)


_NOTE = """---
title: Hardware SOS
type: meeting
date: 2026-05-05
attendees: ["[[70_People/Anja Amelow]]"]
unrecognized_attendees: []
fireflies_id: FF1
---

# Hardware SOS

## Summary

Anja and an unknown speaker discussed laptop reset.

## Action Items

## Transcript

<details>
<summary>Click to expand</summary>

**Speaker 1** [00:00:01]: Hi all, let me kick this off with a long opening statement about the matter at hand.

**Speaker 1** [00:00:10]: short.

**Speaker 2** [00:00:15]: Right, I think we should reset the device first.

**Anja Amelow** [00:00:20]: Agreed.

</details>
"""


def _ctx(tmp_path: Path):
    (tmp_path / "50_Daily" / "meetings").mkdir(parents=True)
    (tmp_path / "50_Daily" / "meetings" / "2026-05-05-hardware-sos.md").write_text(
        _NOTE, encoding="utf-8"
    )
    (tmp_path / "70_People").mkdir()
    (tmp_path / "70_People" / "Christoph Reinhardt.md").write_text(
        "---\ntitle: Christoph Reinhardt\ntype: person\n---\n\n## History\n\n",
        encoding="utf-8",
    )

    settings = MagicMock()
    settings.vault_path = tmp_path
    ctx = MagicMock()
    ctx.settings = settings
    return ctx


def test_utterance_samples_returns_longest_first():
    samples = _utterance_samples(_NOTE, "Speaker 1", k=2)
    assert len(samples) == 2
    assert "long opening statement" in samples[0]
    assert samples[1] == "short."


def test_replace_speaker_in_body_rewrites_only_that_speaker():
    new_body, n = _replace_speaker_in_body(_NOTE, "Speaker 1", "Christoph Reinhardt")
    assert n == 2
    assert "**Christoph Reinhardt** [00:00:01]" in new_body
    assert "**Speaker 2**" in new_body  # untouched
    assert "**Anja Amelow**" in new_body  # untouched


def test_list_meetings_needing_review_today_only(tmp_path: Path):
    ctx = _ctx(tmp_path)
    result = list_meetings_needing_review(
        ctx, date_from=date(2026, 5, 5), date_to=date(2026, 5, 5)
    )
    assert result["count"] == 1
    entry = result["results"][0]
    assert entry["title"] == "Hardware SOS"
    assert set(entry["anonymous_speakers"]) == {"Speaker 1", "Speaker 2"}
    assert "Speaker 1" in entry["samples"]
    assert any("long opening" in s for s in entry["samples"]["Speaker 1"])


def test_list_meetings_needing_review_skips_clean_notes(tmp_path: Path):
    ctx = _ctx(tmp_path)
    clean = """---
title: Clean meeting
type: meeting
date: 2026-05-05
attendees: ["[[70_People/Anja Amelow]]"]
unrecognized_attendees: []
fireflies_id: FF2
---

# Clean

## Transcript

**Anja Amelow**: All good.
"""
    (tmp_path / "50_Daily" / "meetings" / "2026-05-05-clean.md").write_text(
        clean, encoding="utf-8"
    )
    result = list_meetings_needing_review(
        ctx, date_from=date(2026, 5, 5), date_to=date(2026, 5, 5)
    )
    titles = {e["title"] for e in result["results"]}
    assert "Hardware SOS" in titles
    assert "Clean meeting" not in titles


def test_replace_speaker_links_existing_person(tmp_path: Path, monkeypatch):
    ctx = _ctx(tmp_path)

    # Stub append_to_person + update_person_meta so we don't pull the
    # whole vault_write machinery (which expects a full ctx with index).
    import mcp_server.tools.meetings as mod

    calls: dict = {}
    monkeypatch.setattr(
        mod, "append_to_person",
        lambda ctx, **kw: calls.setdefault("append", []).append(kw) or {"ok": True},
    )
    monkeypatch.setattr(
        mod, "update_person_meta",
        lambda ctx, **kw: calls.setdefault("meta", []).append(kw) or {"ok": True},
    )

    result = replace_speaker_in_transcript(
        ctx,
        path="50_Daily/meetings/2026-05-05-hardware-sos.md",
        old_speaker="Speaker 1",
        new_name="Christoph Reinhardt",
    )
    assert result["ok"] is True
    assert result["replacements"] == 2
    assert result["person_linked"] == "Christoph Reinhardt"
    assert calls["append"][0]["name"] == "Christoph Reinhardt"
    assert calls["meta"][0]["name"] == "Christoph Reinhardt"

    # Frontmatter updated.
    new_raw = (tmp_path / "50_Daily" / "meetings" / "2026-05-05-hardware-sos.md").read_text()
    assert "[[70_People/Christoph Reinhardt]]" in new_raw
    assert "**Christoph Reinhardt**" in new_raw


def test_replace_speaker_no_match_returns_error(tmp_path: Path):
    ctx = _ctx(tmp_path)
    result = replace_speaker_in_transcript(
        ctx,
        path="50_Daily/meetings/2026-05-05-hardware-sos.md",
        old_speaker="Speaker 99",
        new_name="Someone",
    )
    assert result["ok"] is False
    assert result["code"] == "NO_MATCH"
