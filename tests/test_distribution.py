"""
Distribution integrity tests.

Verifies that all files required for a clean-clone install are present,
correctly tracked by git, and internally consistent.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ── Required files ────────────────────────────────────────────────────────────

REQUIRED_TRACKED = [
    "readme.md",
    "MANIFEST.in",
    ".gitignore",
    "requirements.txt",
    "requirements-dev.txt",
    "run.py",
    "setup.sh",
    "setup.ps1",
    "install.sh",
    "install.ps1",
    "install.cmd",
    "pyproject.toml",
    "scripts/check_env.py",
    "scripts/db_runtime.py",
    "scripts/models_runtime.py",
    "scripts/pipeline_repl.py",
    "scripts/download_default_backbone.py",
    "api/__init__.py",
    "api/dashboard_api.py",
    "static/index.html",
    "static/app.js",
    "schema/001_init.sql",
    "models/.gitkeep",
    "db/.gitkeep",
    "temp_event_log/.gitkeep",
]


@pytest.mark.parametrize("rel_path", REQUIRED_TRACKED)
def test_required_file_exists(rel_path: str) -> None:
    assert (PROJECT_ROOT / rel_path).exists(), f"Missing distribution file: {rel_path}"


def _git_tracked() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, check=True,
    )
    return set(result.stdout.splitlines())


@pytest.mark.parametrize("rel_path", REQUIRED_TRACKED)
def test_required_file_is_git_tracked(rel_path: str) -> None:
    tracked = _git_tracked()
    assert rel_path in tracked, f"File exists but is not tracked by git: {rel_path}"


def _git_mode(filename: str) -> str:
    result = subprocess.run(
        ["git", "ls-files", "-s", filename],
        capture_output=True, text=True, cwd=PROJECT_ROOT, check=True,
    )
    return result.stdout.split()[0] if result.stdout.strip() else ""


def test_setup_sh_is_executable_in_git() -> None:
    mode = _git_mode("setup.sh")
    assert mode == "100755", f"setup.sh git mode should be 100755 (executable), got {mode!r}"


def test_install_sh_is_executable_in_git() -> None:
    mode = _git_mode("install.sh")
    assert mode == "100755", f"install.sh git mode should be 100755 (executable), got {mode!r}"


# ── Python syntax ─────────────────────────────────────────────────────────────

ALL_PY = [
    p.relative_to(PROJECT_ROOT).as_posix()
    for p in PROJECT_ROOT.rglob("*.py")
    if not any(part in p.parts for part in (".venv", "__pycache__", "Trileaf", "dist"))
]


@pytest.mark.parametrize("rel_path", ALL_PY)
def test_python_syntax(rel_path: str) -> None:
    src = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
    try:
        ast.parse(src, filename=rel_path)
    except SyntaxError as exc:
        pytest.fail(f"SyntaxError in {rel_path}: {exc}")


# ── Packaging layout ──────────────────────────────────────────────────────────

def test_one_liner_installers_exist() -> None:
    for rel_path in ("install.sh", "install.ps1", "install.cmd"):
        assert (PROJECT_ROOT / rel_path).exists(), f"Missing installer: {rel_path}"


def test_pyproject_build_backend() -> None:
    src = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'build-backend = "setuptools.build_meta"' in src


def test_pyproject_includes_static_assets() -> None:
    src = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "static/*.html" in src
    assert "static/*.js" in src


def test_pyproject_entry_point() -> None:
    src = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'llmpoison = "run:main"' in src


def test_schema_file_contains_required_tables() -> None:
    src = (PROJECT_ROOT / "schema" / "001_init.sql").read_text(encoding="utf-8")
    assert "mlc_events" in src
    assert "llm_outputs" in src
    assert "prompt_pool" in src
