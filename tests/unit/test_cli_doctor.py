"""Tests for ``senex.cli_doctor.cmd_doctor`` (M10 Task 10.3).

Reuses M8's ``senex.phases.preflight`` check functions; verifies aggregation,
JSON output schema, secret redaction, and exit-code propagation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


def _make_args(**kwargs: Any) -> argparse.Namespace:
    defaults = dict(
        command="doctor",
        repo_path=None,
        config=None,
        no_tui=False,
        verbose=False,
        quiet=False,
        as_json=False,
        no_load=False,
        no_unload=False,
        unload_after=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _write_minimal_config(tmp_path: Path, repo_dir: Path) -> Path:
    """Write a config TOML and return its path."""
    import tomli_w

    cfg = {
        "repos": [{"name": repo_dir.name, "path": str(repo_dir)}],
    }
    cfg_path = tmp_path / "senex.config.toml"
    cfg_path.write_text(tomli_w.dumps(cfg), encoding="utf-8")
    return cfg_path


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A tmp dir with a .git/ subdir so check_repo_path passes."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


# ---------------------------------------------------------------------------
# Single-repo path
# ---------------------------------------------------------------------------


def test_cmd_doctor_single_repo_all_pass_returns_zero(
    tmp_path: Path, fake_repo: Path
) -> None:
    """All checks pass -> exit 0."""
    from senex.cli_doctor import cmd_doctor

    cfg = _write_minimal_config(tmp_path, fake_repo)
    args = _make_args(repo_path=str(fake_repo), config=str(cfg), as_json=True)

    with patch("senex.cli_doctor._run_async_checks", return_value=[]):
        rc = cmd_doctor(args)
    assert rc == 0


def test_cmd_doctor_returns_two_on_config_fail(tmp_path: Path) -> None:
    """A FAIL config check returns its exit_code (2)."""
    from senex.cli_doctor import cmd_doctor

    bad_cfg = tmp_path / "missing.toml"  # does not exist
    args = _make_args(repo_path=str(tmp_path), config=str(bad_cfg), as_json=True)
    rc = cmd_doctor(args)
    assert rc == 2


def test_cmd_doctor_json_output_has_schema_fields(
    tmp_path: Path, fake_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--json`` output is valid JSON with version/repo/checks/exit_code."""
    from senex.cli_doctor import cmd_doctor

    cfg = _write_minimal_config(tmp_path, fake_repo)
    args = _make_args(repo_path=str(fake_repo), config=str(cfg), as_json=True)

    with patch("senex.cli_doctor._run_async_checks", return_value=[]):
        cmd_doctor(args)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["version"] == 1
    # Single-repo: top-level is the report dict (not a list).
    assert "repo" in parsed
    assert "checks" in parsed
    assert "exit_code" in parsed
    # checks is a list of {name, status, message, details?}.
    for check in parsed["checks"]:
        assert "name" in check
        assert "status" in check
        assert check["status"] in {"pass", "warn", "fail"}


def test_cmd_doctor_human_output_when_no_json(
    tmp_path: Path, fake_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No ``--json``: human-readable table on stdout."""
    from senex.cli_doctor import cmd_doctor

    cfg = _write_minimal_config(tmp_path, fake_repo)
    args = _make_args(repo_path=str(fake_repo), config=str(cfg), as_json=False)

    with patch("senex.cli_doctor._run_async_checks", return_value=[]):
        cmd_doctor(args)
    captured = capsys.readouterr()
    # Human output should include something readable, not raw JSON.
    out = captured.out + captured.err
    assert "config_parses" in out or "preflight" in out.lower() or "check" in out.lower()


# ---------------------------------------------------------------------------
# Multi-repo path (no <repo-path> -> iterate config.repos)
# ---------------------------------------------------------------------------


def test_cmd_doctor_no_repo_path_iterates_config_repos(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``senex doctor`` (no path) runs against every [[repos]]."""
    import tomli_w
    from senex.cli_doctor import cmd_doctor

    r1 = tmp_path / "r1"
    r1.mkdir()
    (r1 / ".git").mkdir()
    r2 = tmp_path / "r2"
    r2.mkdir()
    (r2 / ".git").mkdir()
    cfg_path = tmp_path / "senex.config.toml"
    cfg_path.write_text(
        tomli_w.dumps(
            {
                "repos": [
                    {"name": "r1", "path": str(r1)},
                    {"name": "r2", "path": str(r2)},
                ]
            }
        ),
        encoding="utf-8",
    )
    args = _make_args(repo_path=None, config=str(cfg_path), as_json=True)

    with patch("senex.cli_doctor._run_async_checks", return_value=[]):
        rc = cmd_doctor(args)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    # Multi-repo JSON: top-level list of report objects.
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    repos_in = {r["repo"] for r in parsed}
    assert str(r1) in repos_in
    assert str(r2) in repos_in
    # Both repos pass -> exit 0.
    assert rc == 0


def test_cmd_doctor_no_repo_path_no_repos_returns_two(
    tmp_path: Path,
) -> None:
    """``senex doctor`` with empty config.repos returns 2."""
    import tomli_w
    from senex.cli_doctor import cmd_doctor

    cfg_path = tmp_path / "senex.config.toml"
    cfg_path.write_text(tomli_w.dumps({}), encoding="utf-8")
    args = _make_args(repo_path=None, config=str(cfg_path), as_json=True)
    rc = cmd_doctor(args)
    assert rc == 2


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


def test_cmd_doctor_json_redacts_secrets(
    tmp_path: Path, fake_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Secret-shaped strings in details are redacted before JSON serialization."""
    from senex.cli_doctor import cmd_doctor
    from senex.phases.preflight import CheckResult, CheckStatus

    cfg = _write_minimal_config(tmp_path, fake_repo)
    args = _make_args(repo_path=str(fake_repo), config=str(cfg), as_json=True)

    # Inject a synthetic check whose message contains a secret-shaped string.
    fake_async = [(
        "lms_reachable",
        CheckResult(
            status=CheckStatus.PASS,
            message="probe ok using API_KEY=sk-real-1234567890abcdef",
        ),
    )]

    with patch("senex.cli_doctor._run_async_checks", return_value=fake_async):
        cmd_doctor(args)
    captured = capsys.readouterr()
    out = captured.out
    # The literal secret value should not appear; "REDACTED" should.
    assert "sk-real-" not in out
    assert "REDACTED" in out
