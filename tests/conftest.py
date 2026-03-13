"""
Shared pytest fixtures for LLM Poison Detector tests.

Design constraints:
- No real model downloads — tests never touch models/ or HuggingFace Hub.
- DB tests use tmp_path so they never touch the real db/ directory.
- Env vars that point to real paths are overridden via monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return a path string for a fresh temp SQLite DB."""
    return str(tmp_path / "test_poison.db")


@pytest.fixture
def schema_path() -> str:
    """Return the real schema file path."""
    return str(PROJECT_ROOT / "schema" / "001_init.sql")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Remove model-path and DB env vars so tests start from clean defaults."""
    for var in (
        "MLC_MODEL_PATH",
        "MAIN_LLM_MODEL_PATH",
        "BASELINE_LLM_MODEL_PATH",
        "SQLITE_DB_PATH",
        "SCHEMA_PATH",
        "TEMP_EVENT_LOG_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
