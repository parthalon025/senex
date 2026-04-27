"""Tests for senex.lifecycle_cli - status / clear-locks subcommands.

Implements M4 Task 4.4 per the M4 plan.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from senex.lifecycle_cli import (
    cli_lifecycle_clear_locks,
    cli_lifecycle_status,
    register_lifecycle_subparser,
)
from senex.lmstudio_lifecycle import ModelInfo, _compute_fingerprint
from senex.runlock import RunLock, _lock_path


def _make_info(model_id: str = "m", quant: str = "Q5", digest: str = "abc",
               backend: str = "sdk") -> ModelInfo:
    return ModelInfo(
        model_id=model_id,
        quant=quant,
        checkpoint_digest=digest,
        fingerprint=_compute_fingerprint(model_id, quant, digest),
        backend=backend,
    )


@pytest.fixture
def runlock_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect RunLock root + lifecycle_cli runlock_dir into tmp_path."""
    monkeypatch.setattr("senex.runlock._default_root", lambda: tmp_path)
    monkeypatch.setattr(
        "senex.lifecycle_cli._default_runlock_root", lambda: tmp_path
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Task 4.4 - cli_lifecycle_status
# ---------------------------------------------------------------------------


async def test_lifecycle_status_json_schema(
    monkeypatch: pytest.MonkeyPatch,
    runlock_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON output must byte-match the pinned schema (spec section 10).

    {
      "version": 1,
      "loaded_models": [{model_id, quant, fingerprint, backend}],
      "runlock_holders": [{fingerprint, run_id, pid, started_at,
                           loaded_by_us, pid_alive}]
    }
    """
    info = _make_info()
    fp = info.fingerprint
    fake_backend = MagicMock()
    fake_backend.list_loaded = AsyncMock(return_value=[info])
    monkeypatch.setattr(
        "senex.lifecycle_cli.LifecycleBackendFactory.select",
        AsyncMock(return_value=fake_backend),
    )

    # Pre-populate one runlock with one live holder (current PID).
    RunLock.acquire(fp, "01HQAAAA", os.getpid(), True, root=runlock_root)

    exit_code = await cli_lifecycle_status(as_json=True)
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == 1
    assert out["loaded_models"] == [
        {"model_id": "m", "quant": "Q5", "fingerprint": fp, "backend": "sdk"},
    ]
    assert len(out["runlock_holders"]) == 1
    h = out["runlock_holders"][0]
    assert set(h.keys()) == {
        "fingerprint", "run_id", "pid", "started_at",
        "loaded_by_us", "pid_alive",
    }
    assert h["fingerprint"] == fp
    assert h["run_id"] == "01HQAAAA"
    assert h["pid"] == os.getpid()
    assert h["loaded_by_us"] is True
    assert h["pid_alive"] is True


async def test_lifecycle_status_human_table(
    monkeypatch: pytest.MonkeyPatch,
    runlock_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human-readable table output contains the expected column headers."""
    info = _make_info()
    fake_backend = MagicMock()
    fake_backend.list_loaded = AsyncMock(return_value=[info])
    monkeypatch.setattr(
        "senex.lifecycle_cli.LifecycleBackendFactory.select",
        AsyncMock(return_value=fake_backend),
    )
    exit_code = await cli_lifecycle_status(as_json=False)
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "MODEL_ID" in out
    assert "QUANT" in out
    assert "FINGERPRINT" in out
    assert info.model_id in out


async def test_lifecycle_status_handles_no_backend(
    monkeypatch: pytest.MonkeyPatch,
    runlock_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Backend unavailable -> JSON still emits version=1 with empty loaded_models."""
    from senex.lmstudio_lifecycle import LifecycleBackendUnavailable

    monkeypatch.setattr(
        "senex.lifecycle_cli.LifecycleBackendFactory.select",
        AsyncMock(side_effect=LifecycleBackendUnavailable("none")),
    )
    exit_code = await cli_lifecycle_status(as_json=True)
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == 1
    assert out["loaded_models"] == []


# ---------------------------------------------------------------------------
# Task 4.4 - cli_lifecycle_clear_locks
# ---------------------------------------------------------------------------


async def test_clear_locks_default_prunes_dead_only(
    monkeypatch: pytest.MonkeyPatch,
    runlock_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """force=False prunes only dead-PID holders; live PIDs preserved."""
    info = _make_info()
    fp = info.fingerprint
    lock = _lock_path(fp, runlock_root)
    runlock_root.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({
            "schema_version": 1,
            "model_id": "m",
            "model_fingerprint": fp,
            "holders": [
                {"run_id": "dead", "pid": 999998,
                 "started_at": "2026-04-26T00:00:00Z", "loaded_by_us": True},
                {"run_id": "live", "pid": os.getpid(),
                 "started_at": "2026-04-26T00:00:00Z", "loaded_by_us": False},
            ],
        }),
        encoding="utf-8",
    )
    exit_code = await cli_lifecycle_clear_locks(force=False)
    assert exit_code == 0
    holders = RunLock.list_holders(fp)
    assert len(holders) == 1
    assert holders[0]["run_id"] == "live"
    err = capsys.readouterr().err
    assert "pruned 1" in err or "pruned" in err


async def test_clear_locks_force_removes_live_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    runlock_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """force=True removes all holders; UserWarning raised for live PIDs."""
    info = _make_info()
    fp = info.fingerprint
    lock = _lock_path(fp, runlock_root)
    runlock_root.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({
            "schema_version": 1,
            "model_id": "m",
            "model_fingerprint": fp,
            "holders": [
                {"run_id": "dead", "pid": 999998,
                 "started_at": "2026-04-26T00:00:00Z", "loaded_by_us": True},
                {"run_id": "live", "pid": os.getpid(),
                 "started_at": "2026-04-26T00:00:00Z", "loaded_by_us": False},
            ],
        }),
        encoding="utf-8",
    )
    with pytest.warns(UserWarning):
        exit_code = await cli_lifecycle_clear_locks(force=True)
    assert exit_code == 0
    # Lock file should now be deleted.
    assert RunLock.list_holders(fp, root=runlock_root) == []


async def test_clear_locks_refuses_live_without_force(
    monkeypatch: pytest.MonkeyPatch,
    runlock_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """force=False with live-only holders -> refuse, exit non-zero, stderr message."""
    info = _make_info()
    fp = info.fingerprint
    RunLock.acquire(fp, "live-run", os.getpid(), True, root=runlock_root)
    exit_code = await cli_lifecycle_clear_locks(force=False)
    assert exit_code != 0
    err = capsys.readouterr().err
    assert "refusing to remove live holders" in err
    assert "use --force" in err
    # Holder unchanged.
    holders = RunLock.list_holders(fp)
    assert len(holders) == 1


# ---------------------------------------------------------------------------
# Task 4.4 - register_lifecycle_subparser
# ---------------------------------------------------------------------------


def test_register_lifecycle_subparser() -> None:
    """The subparser registration declares 'status' and 'clear-locks' commands."""
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register_lifecycle_subparser(sub)

    # status (with --json)
    args = parser.parse_args(["lifecycle", "status", "--json"])
    assert args.cmd == "lifecycle"
    assert args.lifecycle_cmd == "status"
    assert args.as_json is True

    # clear-locks --force
    args = parser.parse_args(["lifecycle", "clear-locks", "--force"])
    assert args.lifecycle_cmd == "clear-locks"
    assert args.force is True
