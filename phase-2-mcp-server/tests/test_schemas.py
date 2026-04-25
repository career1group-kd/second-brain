"""Frontmatter schema tests."""

from __future__ import annotations

from datetime import date

import pytest

from mcp_server.schemas import validate_frontmatter


def test_validate_living_doc_minimal() -> None:
    out = validate_frontmatter(
        "living",
        {"project": "ChapterNext", "created": "2026-04-25", "updated": "2026-04-25"},
    )
    assert out["type"] == "living"
    assert out["status"] == "active"


def test_validate_meeting_required_date() -> None:
    out = validate_frontmatter(
        "meeting",
        {
            "date": "2026-04-25",
            "attendees": ["[[Anna]]"],
            "created": "2026-04-25",
            "updated": "2026-04-25",
        },
    )
    assert out["type"] == "meeting"


def test_validate_person_minimal() -> None:
    out = validate_frontmatter(
        "person",
        {
            "last_interaction": "2026-04-25",
            "created": "2026-04-25",
            "updated": "2026-04-25",
        },
    )
    assert out["type"] == "person"


def test_validate_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown type"):
        validate_frontmatter("nonsense", {})


def test_validate_living_doc_rejects_bad_status() -> None:
    with pytest.raises(Exception):
        validate_frontmatter(
            "living",
            {
                "project": "X",
                "status": "wat",
                "created": "2026-04-25",
                "updated": "2026-04-25",
            },
        )
