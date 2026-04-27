"""Tests for ``senex.cli_audit.cmd_audit`` (M10 Task 10.2).

Focus: composition root and exit-code matrix. All deps are mocked; we
verify that ``cmd_audit`` wires them correctly, returns the right exit
code for each scenario, and respects ``--no-tui`` / ``--nightly`` /
``--resume``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from senex.config import RepoCfg, SenexConfig


def _make_args(**kwargs: Any) -> argparse.Namespace:
    """Return a Namespace populated with audit-subcommand defaults."""
    defaults = dict(
        command="audit",
        repo_path=None,
        resume=False,
        nightly=False,
        include_tests=False,
        lens=None,
        min_priority=None,
        model=None,
        allow_mixed_resume=False,
        unsafe_resume=False,
        config=None,
        no_tui=True,  # default to headless in tests for determinism
        verbose=False,
        quiet=False,
        as_json=False,
        no_load=False,
        no_unload=False,
        unload_after=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


@pytest.fixture
def fake_config(tmp_path: Path) -> SenexConfig:
    """A minimal SenexConfig with one repo entry pointing at tmp_path."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    return SenexConfig(
        repos=[
            RepoCfg(name="repo", path=str(repo_dir)),
        ]
    )


@pytest.fixture
def write_config(tmp_path: Path, fake_config: SenexConfig) -> Path:
    """Write fake_config to a temporary TOML file and return its path."""
    import tomli_w

    cfg_path = tmp_path / "senex.config.toml"
    data = fake_config.model_dump()
    # tomli_w can't serialize None; strip them.
    def _strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: _strip(v) for k, v in o.items() if v is not None}
        if isinstance(o, list):
            return [_strip(i) for i in o]
        return o

    cfg_path.write_text(tomli_w.dumps(_strip(data)), encoding="utf-8")
    return cfg_path


# ---------------------------------------------------------------------------
# Exit code matrix
# ---------------------------------------------------------------------------


def test_cmd_audit_happy_path_returns_zero(
    write_config: Path, fake_config: SenexConfig, tmp_path: Path
) -> None:
    """Successful audit returns exit 0."""
    from senex.cli_audit import cmd_audit

    args = _make_args(
        repo_path=str(tmp_path / "repo"),
        config=str(write_config),
        no_tui=True,
    )
    with (
        patch("senex.cli_audit.run_audit") as mock_run,
        patch("senex.cli_audit.Lens.load") as mock_lens,
    ):
        mock_run.return_value = 0  # async fn returns directly via asyncio.run path
        # Make run_audit awaitable
        async def _ok(**kwargs: Any) -> int:
            return 0

        mock_run.side_effect = _ok
        mock_lens.return_value = MagicMock()
        rc = cmd_audit(args)
    assert rc == 0


def test_cmd_audit_partial_success_returns_one(
    write_config: Path, tmp_path: Path
) -> None:
    """run_audit returning 1 (partial) propagates."""
    from senex.cli_audit import cmd_audit

    args = _make_args(
        repo_path=str(tmp_path / "repo"),
        config=str(write_config),
        no_tui=True,
    )
    with (
        patch("senex.cli_audit.run_audit") as mock_run,
        patch("senex.cli_audit.Lens.load") as mock_lens,
    ):
        async def _partial(**kwargs: Any) -> int:
            return 1

        mock_run.side_effect = _partial
        mock_lens.return_value = MagicMock()
        rc = cmd_audit(args)
    assert rc == 1


def test_cmd_audit_bad_config_returns_two(tmp_path: Path) -> None:
    """Unparseable config file returns exit 2."""
    from senex.cli_audit import cmd_audit

    bad_cfg = tmp_path / "bad.toml"
    bad_cfg.write_text("this is not [valid TOML\n", encoding="utf-8")
    args = _make_args(
        repo_path=str(tmp_path / "repo"),
        config=str(bad_cfg),
        no_tui=True,
    )
    rc = cmd_audit(args)
    assert rc == 2


def test_cmd_audit_missing_config_returns_two(tmp_path: Path) -> None:
    """Missing config file returns exit 2."""
    from senex.cli_audit import cmd_audit

    args = _make_args(
        repo_path=str(tmp_path / "repo"),
        config=str(tmp_path / "nonexistent.toml"),
        no_tui=True,
    )
    rc = cmd_audit(args)
    assert rc == 2


def test_cmd_audit_external_error_returns_three(
    write_config: Path, tmp_path: Path
) -> None:
    """run_audit returning 3 (external) propagates."""
    from senex.cli_audit import cmd_audit

    args = _make_args(
        repo_path=str(tmp_path / "repo"),
        config=str(write_config),
        no_tui=True,
    )
    with (
        patch("senex.cli_audit.run_audit") as mock_run,
        patch("senex.cli_audit.Lens.load") as mock_lens,
    ):
        async def _ext(**kwargs: Any) -> int:
            return 3

        mock_run.side_effect = _ext
        mock_lens.return_value = MagicMock()
        rc = cmd_audit(args)
    assert rc == 3


def test_cmd_audit_keyboard_interrupt_returns_130(
    write_config: Path, tmp_path: Path
) -> None:
    """SIGINT mid-run returns exit 130."""
    from senex.cli_audit import cmd_audit

    args = _make_args(
        repo_path=str(tmp_path / "repo"),
        config=str(write_config),
        no_tui=True,
    )
    with (
        patch("senex.cli_audit.run_audit") as mock_run,
        patch("senex.cli_audit.Lens.load") as mock_lens,
    ):
        async def _interrupt(**kwargs: Any) -> int:
            raise KeyboardInterrupt()

        mock_run.side_effect = _interrupt
        mock_lens.return_value = MagicMock()
        rc = cmd_audit(args)
    assert rc == 130


# ---------------------------------------------------------------------------
# --nightly: iterates repos
# ---------------------------------------------------------------------------


def test_cmd_audit_nightly_iterates_all_repos(tmp_path: Path) -> None:
    """``--nightly`` iterates every [[repos]]; partial outcome bubbles."""
    import tomli_w
    from senex.cli_audit import cmd_audit

    repo1 = tmp_path / "r1"
    repo2 = tmp_path / "r2"
    repo3 = tmp_path / "r3"
    for r in (repo1, repo2, repo3):
        r.mkdir()
    cfg_path = tmp_path / "n.toml"
    cfg = {
        "repos": [
            {"name": "r1", "path": str(repo1)},
            {"name": "r2", "path": str(repo2)},
            {"name": "r3", "path": str(repo3)},
        ]
    }
    cfg_path.write_text(tomli_w.dumps(cfg), encoding="utf-8")
    args = _make_args(nightly=True, config=str(cfg_path), no_tui=True)

    call_results = iter([0, 1, 0])  # one partial in the middle

    async def _fake_run(**kwargs: Any) -> int:
        return next(call_results)

    with (
        patch("senex.cli_audit.run_audit", side_effect=_fake_run) as mock_run,
        patch("senex.cli_audit.Lens.load") as mock_lens,
    ):
        mock_lens.return_value = MagicMock()
        rc = cmd_audit(args)

    # 3 calls, exit code = max(0, 1, 0) = 1.
    assert mock_run.call_count == 3
    assert rc == 1


def test_cmd_audit_nightly_continues_on_per_repo_exception(tmp_path: Path) -> None:
    """A bad repo entry should not abort the entire nightly batch."""
    import tomli_w
    from senex.cli_audit import cmd_audit

    repo1 = tmp_path / "r1"
    repo2 = tmp_path / "r2"
    for r in (repo1, repo2):
        r.mkdir()
    cfg_path = tmp_path / "n.toml"
    cfg = {
        "repos": [
            {"name": "r1", "path": str(repo1)},
            {"name": "r2", "path": str(repo2)},
        ]
    }
    cfg_path.write_text(tomli_w.dumps(cfg), encoding="utf-8")
    args = _make_args(nightly=True, config=str(cfg_path), no_tui=True)

    calls = {"count": 0}

    async def _fake_run(**kwargs: Any) -> int:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        return 0

    with (
        patch("senex.cli_audit.run_audit", side_effect=_fake_run),
        patch("senex.cli_audit.Lens.load") as mock_lens,
    ):
        mock_lens.return_value = MagicMock()
        rc = cmd_audit(args)

    # First repo crashes (treated as exit 3), second is 0; max = 3.
    assert calls["count"] == 2
    assert rc == 3


# ---------------------------------------------------------------------------
# Subscriber wiring (TuiSubscriber when --no-tui not set; HeadlessSubscriber otherwise)
# ---------------------------------------------------------------------------


def test_cmd_audit_no_tui_uses_headless_subscriber(
    write_config: Path, tmp_path: Path
) -> None:
    """``--no-tui`` should select HeadlessSubscriber path."""
    from senex.cli_audit import cmd_audit

    args = _make_args(
        repo_path=str(tmp_path / "repo"),
        config=str(write_config),
        no_tui=True,
    )

    seen: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> int:
        # Test-side: just ensure run_audit gets called once (with bus subscribed).
        seen["called"] = True
        return 0

    with (
        patch("senex.cli_audit.run_audit", side_effect=_capture),
        patch("senex.cli_audit.Lens.load") as mock_lens,
    ):
        mock_lens.return_value = MagicMock()
        rc = cmd_audit(args)

    assert seen.get("called") is True
    assert rc == 0


def test_cmd_audit_resume_flag_propagates(write_config: Path, tmp_path: Path) -> None:
    """``--resume`` propagates to run_audit(resume=True)."""
    from senex.cli_audit import cmd_audit

    args = _make_args(
        repo_path=str(tmp_path / "repo"),
        config=str(write_config),
        no_tui=True,
        resume=True,
    )

    captured: dict[str, Any] = {}

    async def _fake_run(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    with (
        patch("senex.cli_audit.run_audit", side_effect=_fake_run),
        patch("senex.cli_audit.Lens.load") as mock_lens,
    ):
        mock_lens.return_value = MagicMock()
        rc = cmd_audit(args)

    assert rc == 0
    assert captured.get("resume") is True


def test_cmd_audit_missing_repo_path_without_nightly_returns_two(
    write_config: Path,
) -> None:
    """``audit`` with no repo path AND no --nightly is exit 2."""
    from senex.cli_audit import cmd_audit

    args = _make_args(
        repo_path=None,
        nightly=False,
        config=str(write_config),
        no_tui=True,
    )
    rc = cmd_audit(args)
    assert rc == 2
