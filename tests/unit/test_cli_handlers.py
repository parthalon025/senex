"""Smoke tests for ``senex.cli_handlers`` dispatcher (M10 Task 10.8 coverage gap).

These tests verify the dispatcher delegates correctly. Per-subcommand semantics
live in test_cli_audit / test_cli_doctor / test_cli_aggregate / test_cli_config_show.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import patch


def _make_args(**kwargs: Any) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_cmd_audit_dispatches_to_cli_audit() -> None:
    from senex.cli_handlers import cmd_audit

    with patch("senex.cli_audit.cmd_audit", return_value=0) as mock:
        rc = cmd_audit(_make_args(command="audit"))
    assert rc == 0
    mock.assert_called_once()


def test_cmd_doctor_dispatches_to_cli_doctor() -> None:
    from senex.cli_handlers import cmd_doctor

    with patch("senex.cli_doctor.cmd_doctor", return_value=0) as mock:
        rc = cmd_doctor(_make_args(command="doctor"))
    assert rc == 0
    mock.assert_called_once()


def test_cmd_aggregate_dispatches_to_cli_aggregate() -> None:
    from senex.cli_handlers import cmd_aggregate

    with patch("senex.cli_aggregate.cmd_aggregate", return_value=0) as mock:
        rc = cmd_aggregate(_make_args(command="aggregate"))
    assert rc == 0
    mock.assert_called_once()


def test_cmd_config_dispatches_to_cli_config_show() -> None:
    from senex.cli_handlers import cmd_config

    with patch(
        "senex.cli_config_show.cmd_config_show", return_value=0
    ) as mock:
        rc = cmd_config(_make_args(command="config"))
    assert rc == 0
    mock.assert_called_once()


def test_cmd_view_returns_two_when_audit_dir_missing() -> None:
    from senex.cli_handlers import cmd_view

    rc = cmd_view(_make_args(command="view", audit_dir=None, speed=1.0))
    assert rc == 2


def test_cmd_view_returns_two_when_audit_dir_does_not_exist(tmp_path: Path) -> None:
    from senex.cli_handlers import cmd_view

    rc = cmd_view(
        _make_args(
            command="view",
            audit_dir=str(tmp_path / "does-not-exist"),
            speed=1.0,
        )
    )
    assert rc == 2


def test_cmd_view_runs_run_view_when_audit_dir_exists(tmp_path: Path) -> None:
    from senex.cli_handlers import cmd_view

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    async def _ok(*args: Any, **kw: Any) -> None:
        return None

    with patch("senex.tui.view.run_view", side_effect=_ok):
        rc = cmd_view(
            _make_args(
                command="view",
                audit_dir=str(audit_dir),
                speed=1.0,
            )
        )
    assert rc == 0


def test_cmd_view_returns_two_on_replay_error(tmp_path: Path) -> None:
    from senex.cli_handlers import cmd_view
    from senex.tui.exceptions import ReplayError

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    async def _boom(*args: Any, **kw: Any) -> None:
        raise ReplayError("bad events.jsonl")

    with patch("senex.tui.view.run_view", side_effect=_boom):
        rc = cmd_view(
            _make_args(command="view", audit_dir=str(audit_dir), speed=1.0)
        )
    assert rc == 2


def test_cmd_lifecycle_status_dispatches() -> None:
    from senex.cli_handlers import cmd_lifecycle

    async def _ok(*, as_json: bool) -> int:
        return 0

    with patch(
        "senex.lifecycle_cli.cli_lifecycle_status", side_effect=_ok
    ):
        rc = cmd_lifecycle(
            _make_args(
                command="lifecycle",
                lifecycle_cmd="status",
                as_json=False,
            )
        )
    assert rc == 0


def test_cmd_lifecycle_clear_locks_dispatches() -> None:
    from senex.cli_handlers import cmd_lifecycle

    async def _ok(*, force: bool) -> int:
        return 0

    with patch(
        "senex.lifecycle_cli.cli_lifecycle_clear_locks", side_effect=_ok
    ):
        rc = cmd_lifecycle(
            _make_args(
                command="lifecycle",
                lifecycle_cmd="clear-locks",
                force=False,
            )
        )
    assert rc == 0


def test_cmd_lifecycle_unknown_subcommand_returns_two() -> None:
    from senex.cli_handlers import cmd_lifecycle

    rc = cmd_lifecycle(
        _make_args(command="lifecycle", lifecycle_cmd="bogus")
    )
    assert rc == 2
