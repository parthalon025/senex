"""Tests for ``senex.cli_config_show.cmd_config_show`` (M10 Task 10.5).

Verifies TOML round-trip, --json output, --all dump, and secret redaction.
"""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest


def _make_args(**kwargs: Any) -> argparse.Namespace:
    defaults = dict(
        command="config",
        config_subcmd="show",
        repo_path=None,
        all=False,
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


def _write_config(
    tmp_path: Path,
    repos: list[dict[str, Any]] | None = None,
    api_key: str | None = None,
) -> Path:
    """Write a senex.config.toml and return its path."""
    import tomli_w

    body: dict[str, Any] = {}
    if repos is not None:
        body["repos"] = repos
    if api_key is not None:
        body["lmstudio"] = {"api_key": api_key}
    cfg_path = tmp_path / "senex.config.toml"
    cfg_path.write_text(tomli_w.dumps(body), encoding="utf-8")
    return cfg_path


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    return repo


# ---------------------------------------------------------------------------
# TOML round-trip
# ---------------------------------------------------------------------------


def test_cmd_config_show_toml_roundtrips(tmp_path: Path, fake_repo: Path) -> None:
    """Re-loading the TOML output produces an equivalent SenexConfig."""
    from senex.cli_config_show import cmd_config_show
    from senex.config import load_config

    cfg = _write_config(
        tmp_path,
        repos=[{"name": "myrepo", "path": str(fake_repo)}],
    )
    args = _make_args(repo_path=str(fake_repo), config=str(cfg), as_json=False)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_config_show(args)
    assert rc == 0
    output = buf.getvalue()

    # Round-trip: write the output to a file and load_config it.
    rt_path = tmp_path / "rt.toml"
    rt_path.write_text(output, encoding="utf-8")
    rt_loaded = load_config(rt_path)
    # The lens name (a stable field) should match the default.
    assert rt_loaded.lens.name == "correctness"


# ---------------------------------------------------------------------------
# --json output
# ---------------------------------------------------------------------------


def test_cmd_config_show_json_outputs_valid_json(
    tmp_path: Path, fake_repo: Path
) -> None:
    """``--json`` outputs JSON with the SenexConfig fields."""
    from senex.cli_config_show import cmd_config_show

    cfg = _write_config(
        tmp_path,
        repos=[{"name": "myrepo", "path": str(fake_repo)}],
    )
    args = _make_args(repo_path=str(fake_repo), config=str(cfg), as_json=True)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_config_show(args)
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert "lens" in parsed
    assert "lmstudio" in parsed
    assert "walker" in parsed


# ---------------------------------------------------------------------------
# --all dump
# ---------------------------------------------------------------------------


def test_cmd_config_show_all_dumps_each_repo(tmp_path: Path) -> None:
    """``--all`` lists every [[repos]] entry."""
    from senex.cli_config_show import cmd_config_show

    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    r1.mkdir()
    r2.mkdir()
    cfg = _write_config(
        tmp_path,
        repos=[
            {"name": "r1", "path": str(r1)},
            {"name": "r2", "path": str(r2)},
        ],
    )
    args = _make_args(all=True, config=str(cfg), as_json=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_config_show(args)
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    repos_in = {item["repo"] for item in parsed}
    assert str(r1) in repos_in
    assert str(r2) in repos_in


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


def test_cmd_config_show_redacts_secrets(
    tmp_path: Path, fake_repo: Path
) -> None:
    """``api_key`` field value is redacted in output."""
    from senex.cli_config_show import cmd_config_show

    cfg = _write_config(
        tmp_path,
        repos=[{"name": "myrepo", "path": str(fake_repo)}],
        api_key="sk-real-shouldbe-redacted-1234567890",
    )
    args = _make_args(repo_path=str(fake_repo), config=str(cfg), as_json=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_config_show(args)
    out = buf.getvalue()
    # Real value never appears.
    assert "sk-real-shouldbe-redacted" not in out
    # Some redaction marker is present.
    assert "REDACTED" in out


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_cmd_config_show_missing_config_returns_two(tmp_path: Path) -> None:
    """Missing config file -> exit 2."""
    from senex.cli_config_show import cmd_config_show

    args = _make_args(
        repo_path="/nonexistent",
        config=str(tmp_path / "missing.toml"),
        as_json=False,
    )
    rc = cmd_config_show(args)
    assert rc == 2


def test_cmd_config_show_no_args_returns_two(tmp_path: Path) -> None:
    """Neither --all nor <repo-path> -> exit 2."""
    from senex.cli_config_show import cmd_config_show

    cfg = _write_config(tmp_path, repos=[])
    args = _make_args(repo_path=None, all=False, config=str(cfg))
    rc = cmd_config_show(args)
    assert rc == 2
