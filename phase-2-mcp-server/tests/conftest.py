"""Test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "vault"


@pytest.fixture
def fixture_vault(tmp_path: Path) -> Path:
    """Materialise a fresh copy of the fixture vault under tmp_path."""
    import shutil

    target = tmp_path / "vault"
    shutil.copytree(FIXTURES_ROOT, target)
    return target
