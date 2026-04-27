"""Tests for ``scripts/setup.ps1`` and ``scripts/run_senex.bat`` (M10 Task 10.6).

The structure-only tests (existence + content checks) always run. The full
end-to-end "create venv + pip install" test is expensive (~60s) and is
gated behind ``SENEX_RUN_SETUP_PS1=1``; otherwise it skips. CI may opt in
manually for the v1 release validation.
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Structure-only tests (always run)
# ---------------------------------------------------------------------------


def test_run_senex_bat_exists() -> None:
    """``scripts/run_senex.bat`` is present and matches spec §10."""
    bat = _REPO_ROOT / "scripts" / "run_senex.bat"
    assert bat.exists(), f"missing: {bat}"
    body = bat.read_text(encoding="utf-8")
    # Spec §10 verbatim contents.
    assert "@echo off" in body
    assert "Nightly Audit" in body
    assert ".venv\\Scripts\\activate.bat" in body
    assert "python -m senex audit --nightly" in body
    # cd to repo root from the script's parent dir.
    assert "%~dp0" in body


def test_setup_ps1_exists() -> None:
    """``scripts/setup.ps1`` is present and references key steps."""
    ps1 = _REPO_ROOT / "scripts" / "setup.ps1"
    assert ps1.exists(), f"missing: {ps1}"
    body = ps1.read_text(encoding="utf-8")
    assert "$ErrorActionPreference" in body
    assert "python -m venv .venv" in body
    assert "-m pip install -e ." in body
    assert "-m senex --version" in body
    assert "senex.config.toml.example" in body


def test_setup_ps1_uses_strict_error_handling() -> None:
    ps1 = _REPO_ROOT / "scripts" / "setup.ps1"
    body = ps1.read_text(encoding="utf-8")
    assert "$ErrorActionPreference = \"Stop\"" in body, (
        "setup.ps1 must run with strict error handling so a failed pip "
        "install aborts the script."
    )


# ---------------------------------------------------------------------------
# End-to-end run (opt-in via env var or non-CI run)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    platform.system() != "Windows",
    reason="setup.ps1 is Windows-only",
)
@pytest.mark.skipif(
    os.environ.get("SENEX_RUN_SETUP_PS1") != "1",
    reason="set SENEX_RUN_SETUP_PS1=1 to opt in to the full venv+install run (~60s)",
)
def test_setup_ps1_creates_venv_and_imports_senex(tmp_path: Path) -> None:
    """Copy the repo into a temp dir, run setup.ps1, verify .venv + version."""
    import shutil

    # We can't deep-copy the full repo (slow); instead, run setup.ps1
    # against a project skeleton that pip-installs senex from the
    # current source tree.
    work = tmp_path / "work"
    work.mkdir()
    # Copy pyproject + senex package source.
    shutil.copytree(_REPO_ROOT / "senex", work / "senex")
    shutil.copy(_REPO_ROOT / "pyproject.toml", work / "pyproject.toml")
    shutil.copytree(_REPO_ROOT / "scripts", work / "scripts")

    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(work / "scripts" / "setup.ps1"),
        ],
        cwd=str(work),
        capture_output=True,
        timeout=300,
        text=True,
    )
    assert proc.returncode == 0, (
        f"setup.ps1 failed: exit={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert (work / ".venv").exists()
    assert "senex" in proc.stdout.lower()
