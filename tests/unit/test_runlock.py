"""Tests for senex.runlock — interprocess refcount file lock with stale-PID prune."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from senex.runlock import (
    CorruptRunLock,
    RunLock,
    _is_pid_alive,
)


def test_runlock_acquire_creates_lockfile_with_one_holder(tmp_path: Path) -> None:
    n = RunLock.acquire(
        fingerprint="sha256:abc",
        run_id="run1",
        pid=os.getpid(),
        loaded_by_us=True,
        root=tmp_path,
    )
    assert n == 1
    lock = tmp_path / "sha256_abc.lock"
    assert lock.exists()
    data = json.loads(lock.read_text(encoding="utf-8"))
    assert len(data["holders"]) == 1
    assert data["holders"][0]["run_id"] == "run1"


def test_runlock_acquire_twice_returns_count_two(tmp_path: Path) -> None:
    RunLock.acquire("fp1", "run1", os.getpid(), True, root=tmp_path)
    n = RunLock.acquire("fp1", "run2", os.getpid(), False, root=tmp_path)
    assert n == 2


def test_runlock_release_removes_holder(tmp_path: Path) -> None:
    RunLock.acquire("fp1", "run1", os.getpid(), True, root=tmp_path)
    RunLock.acquire("fp1", "run2", os.getpid(), False, root=tmp_path)
    remaining = RunLock.release("fp1", "run1", root=tmp_path)
    assert remaining == 1


def test_runlock_release_last_holder_deletes_file(tmp_path: Path) -> None:
    RunLock.acquire("fp1", "run1", os.getpid(), True, root=tmp_path)
    remaining = RunLock.release("fp1", "run1", root=tmp_path)
    assert remaining == 0
    assert not (tmp_path / "fp1.lock").exists()


def test_runlock_acquire_prunes_stale_pid_entry(tmp_path: Path) -> None:
    # Pre-seed a stale entry with a PID that does not exist (use INT_MAX as a sentinel).
    stale_pid = 2**31 - 1
    lock = tmp_path / "fp1.lock"
    lock.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_id": "x",
                "model_fingerprint": "fp1",
                "holders": [
                    {"run_id": "dead", "pid": stale_pid,
                     "started_at": "2026-01-01T00:00:00Z", "loaded_by_us": True}
                ],
            }
        ),
        encoding="utf-8",
    )
    assert not _is_pid_alive(stale_pid)
    n = RunLock.acquire("fp1", "live", os.getpid(), False, root=tmp_path)
    assert n == 1  # stale dropped before append.


def test_runlock_corrupt_json_renamed_and_recreated(tmp_path: Path) -> None:
    lock = tmp_path / "fp1.lock"
    lock.write_text("{ this is not json", encoding="utf-8")
    # Acquire MUST salvage by renaming corrupt and starting over.
    n = RunLock.acquire("fp1", "live", os.getpid(), False, root=tmp_path)
    assert n == 1
    corrupt = list(tmp_path.glob("fp1.lock.corrupt-*"))
    assert len(corrupt) == 1


def test_runlock_release_unknown_run_id_raises_corruptrunlock(tmp_path: Path) -> None:
    RunLock.acquire("fp1", "run1", os.getpid(), True, root=tmp_path)
    with pytest.raises(CorruptRunLock):
        RunLock.release("fp1", "run-never-acquired", root=tmp_path)


def test_runlock_module_has_nonempty_docstring() -> None:
    from senex import runlock as rl
    assert rl.__doc__ and rl.__doc__.strip() != ""
