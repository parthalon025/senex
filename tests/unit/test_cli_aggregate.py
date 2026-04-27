"""Tests for ``senex.cli_aggregate.cmd_aggregate`` (M10 Task 10.4).

Re-runs Phase 5 (``AggregatePhase``) against an existing audit dir.
Validates that required artifacts (``checkpoint.json`` and
``findings.partial.jsonl``) are present BEFORE invoking the phase.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


def _make_args(audit_dir: Path | str | None = None, **kwargs: Any) -> argparse.Namespace:
    defaults = dict(
        command="aggregate",
        audit_dir=str(audit_dir) if audit_dir is not None else None,
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


def _populate_audit_dir(
    audit_dir: Path,
    *,
    with_checkpoint: bool = True,
    with_partial: bool = True,
    partial_lines: list[str] | None = None,
) -> None:
    """Create the minimum scaffolding cmd_aggregate needs."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    if with_checkpoint:
        (audit_dir / "checkpoint.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": "01ABCDEFGHIJKLMNOPQRSTUVWX",
                    "current_phase": "aggregate",
                    "phase_status": {
                        "preflight": "complete",
                        "discovery": "complete",
                        "file_audit": "complete",
                        "crosscut": "complete",
                        "aggregate": "in_progress",
                    },
                    "phase_artifact_hashes": {},
                    "completed_files": [],
                    "config_hash": "deadbeef" * 8,
                    "prompt_hash": "cafef00d" * 8,
                    "model_fingerprint": "deadbeef" * 8,
                    "tool_pack_hash": "12345678" * 8,
                    "lens_version": "0.1.0",
                }
            ),
            encoding="utf-8",
        )
    if with_partial:
        if partial_lines is None:
            partial_lines = []
        (audit_dir / "findings.partial.jsonl").write_text(
            "\n".join(partial_lines) + ("\n" if partial_lines else ""),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Validation: required files
# ---------------------------------------------------------------------------


def test_cmd_aggregate_missing_checkpoint_returns_two(tmp_path: Path) -> None:
    """No ``checkpoint.json`` -> exit 2 with explicit error."""
    from senex.cli_aggregate import cmd_aggregate

    audit_dir = tmp_path / "audit"
    _populate_audit_dir(audit_dir, with_checkpoint=False, with_partial=True)
    args = _make_args(audit_dir=audit_dir)
    rc = cmd_aggregate(args)
    assert rc == 2


def test_cmd_aggregate_missing_partial_returns_two(tmp_path: Path) -> None:
    """No ``findings.partial.jsonl`` -> exit 2."""
    from senex.cli_aggregate import cmd_aggregate

    audit_dir = tmp_path / "audit"
    _populate_audit_dir(audit_dir, with_checkpoint=True, with_partial=False)
    args = _make_args(audit_dir=audit_dir)
    rc = cmd_aggregate(args)
    assert rc == 2


def test_cmd_aggregate_missing_audit_dir_returns_two(tmp_path: Path) -> None:
    """Audit dir does not exist -> exit 2."""
    from senex.cli_aggregate import cmd_aggregate

    args = _make_args(audit_dir=tmp_path / "nonexistent")
    rc = cmd_aggregate(args)
    assert rc == 2


# ---------------------------------------------------------------------------
# Happy path: phase invoked + exit 0
# ---------------------------------------------------------------------------


def test_cmd_aggregate_invokes_aggregate_phase(tmp_path: Path) -> None:
    """``cmd_aggregate`` builds an AggregatePhase and runs ``do_work``."""
    from senex.cli_aggregate import cmd_aggregate

    audit_dir = tmp_path / "audit"
    _populate_audit_dir(audit_dir)
    args = _make_args(audit_dir=audit_dir)

    with patch("senex.cli_aggregate.AggregatePhase") as mock_phase_cls:
        instance = mock_phase_cls.return_value

        async def _ok(state, lens, config, bus, command_bus):
            return {"finding_count": 0, "theme_count": 0}

        async def _read_state(audit_dir):
            return None

        async def _write_state(audit_dir, state):
            return None

        instance.do_work.side_effect = _ok
        instance.read_state.side_effect = _read_state
        instance.write_state.side_effect = _write_state
        rc = cmd_aggregate(args)

    assert rc == 0
    assert mock_phase_cls.called


def test_cmd_aggregate_phase_failure_returns_one(tmp_path: Path) -> None:
    """``AggregateFailed`` -> exit 1 (partial)."""
    from senex.cli_aggregate import cmd_aggregate
    from senex.phases.base import AggregateFailed

    audit_dir = tmp_path / "audit"
    _populate_audit_dir(audit_dir)
    args = _make_args(audit_dir=audit_dir)

    with patch("senex.cli_aggregate.AggregatePhase") as mock_phase_cls:
        instance = mock_phase_cls.return_value

        async def _read_state(audit_dir):
            return None

        async def _do_work(state, lens, config, bus, command_bus):
            raise AggregateFailed("boom", exit_code=1)

        instance.read_state.side_effect = _read_state
        instance.do_work.side_effect = _do_work
        rc = cmd_aggregate(args)

    assert rc == 1


# ---------------------------------------------------------------------------
# Corrupt partial line -> partial success (warning, skip)
# ---------------------------------------------------------------------------


def test_cmd_aggregate_corrupt_partial_line_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad line in findings.partial.jsonl produces a warning but does not abort.

    AggregatePhase delegates to senex.findings_aggregator.Aggregator which
    is responsible for tolerant parsing. Here we just verify cmd_aggregate
    surfaces the eventual return without aborting on the corrupt line.
    """
    from senex.cli_aggregate import cmd_aggregate

    audit_dir = tmp_path / "audit"
    _populate_audit_dir(audit_dir, partial_lines=["this is not JSON"])
    args = _make_args(audit_dir=audit_dir)

    with patch("senex.cli_aggregate.AggregatePhase") as mock_phase_cls:
        instance = mock_phase_cls.return_value

        async def _read_state(audit_dir):
            return None

        async def _do_work(state, lens, config, bus, command_bus):
            return {"finding_count": 0}

        async def _write_state(audit_dir, state):
            return None

        instance.read_state.side_effect = _read_state
        instance.do_work.side_effect = _do_work
        instance.write_state.side_effect = _write_state
        rc = cmd_aggregate(args)
    # Phase ran (mock returned ok) -> exit 0. The corrupt-line tolerance is
    # asserted by tests for findings_aggregator itself.
    assert rc == 0
