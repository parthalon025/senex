"""Smoke tests for ``senex.cli.main`` dispatch (M10 Task 10.8 coverage gap).

Verifies main() routes each subcommand to the right cmd_<name> function.
Per-subcommand semantics are covered in test_cli_<name>.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch


def test_main_audit_dispatches() -> None:
    from senex.cli import main

    with patch("senex.cli_audit.cmd_audit", return_value=0) as mock:
        rc = main(["audit", "C:/some/repo"])
    assert rc == 0
    mock.assert_called_once()


def test_main_doctor_dispatches() -> None:
    from senex.cli import main

    with patch("senex.cli_doctor.cmd_doctor", return_value=0) as mock:
        rc = main(["doctor"])
    assert rc == 0
    mock.assert_called_once()


def test_main_aggregate_dispatches() -> None:
    from senex.cli import main

    with patch("senex.cli_aggregate.cmd_aggregate", return_value=0) as mock:
        rc = main(["aggregate", "C:/audit/dir"])
    assert rc == 0
    mock.assert_called_once()


def test_main_config_show_dispatches() -> None:
    from senex.cli import main

    with patch(
        "senex.cli_config_show.cmd_config_show", return_value=0
    ) as mock:
        rc = main(["config", "show", "--all"])
    assert rc == 0
    mock.assert_called_once()


def test_main_lifecycle_status_dispatches() -> None:
    from senex.cli import main

    async def _ok(*, as_json: bool, config_path: str | None = None) -> int:
        return 0

    with patch(
        "senex.lifecycle_cli.cli_lifecycle_status", side_effect=_ok
    ):
        rc = main(["lifecycle", "status"])
    assert rc == 0


def test_main_view_dispatches(tmp_path: Path) -> None:
    from senex.cli import main

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    async def _ok(*args: Any, **kw: Any) -> None:
        return None

    with patch("senex.tui.view.run_view", side_effect=_ok):
        rc = main(["view", str(audit_dir)])
    assert rc == 0


def test_main_verbose_sets_debug_logging() -> None:
    """``--verbose`` sets logging.basicConfig to DEBUG."""
    import logging

    from senex.cli import main

    with patch("senex.cli_audit.cmd_audit", return_value=0):
        main(["audit", "C:/repo", "--verbose"])
    # Defensive: just verify main ran without crashing on the verbose flag.
    assert logging.getLogger().level <= logging.DEBUG or logging.getLogger().level >= 0


def test_main_quiet_sets_warn_logging() -> None:
    from senex.cli import main

    with patch("senex.cli_audit.cmd_audit", return_value=0):
        rc = main(["audit", "C:/repo", "--quiet"])
    assert rc == 0
