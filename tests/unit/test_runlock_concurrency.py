"""Concurrency, corruption recovery, and cross-platform PID liveness for RunLock.

Implements M4 Task 4.0 (production hardening of M1 stub) per the M4 plan.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from senex.runlock import RunLock, _is_pid_alive


def test_concurrent_acquire_release_no_lost_holder(tmp_path: Path) -> None:
    """Two threads x 100 iterations of acquire+release.

    The atomicity envelope (advisory lock spans read -> prune -> write -> rename)
    guarantees no holder is lost or duplicated. After all threads finish the
    holder count is 0 and the lockfile is removed.
    """
    fp = "concurrent_fp"
    iters = 100
    pid = os.getpid()
    errors: list[BaseException] = []

    def worker(run_id: str) -> None:
        try:
            for i in range(iters):
                RunLock.acquire(fp, f"{run_id}-{i}", pid, True, root=tmp_path)
                RunLock.release(fp, f"{run_id}-{i}", root=tmp_path)
        except BaseException as exc:  # pragma: no cover - re-raised
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
    holders = RunLock.list_holders(fp, root=tmp_path)
    assert holders == []
    # Lockfile must not be left behind when there are zero holders.
    assert not (tmp_path / f"{fp}.lock").exists()


def test_corrupt_json_recovers(tmp_path: Path) -> None:
    """Corrupt JSON in the lockfile is renamed (preserved) and the lockfile is recreated."""
    lock_path = tmp_path / "abc.lock"
    lock_path.write_text("{not valid json", encoding="utf-8")
    count = RunLock.acquire("abc", "run-1", os.getpid(), loaded_by_us=True, root=tmp_path)
    assert count == 1
    # The corrupt file was renamed with a timestamp suffix.
    corrupted = list(tmp_path.glob("abc.lock.corrupt-*"))
    assert len(corrupted) == 1
    # The lockfile was recreated with a single live holder.
    assert lock_path.exists()
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert len(data["holders"]) == 1


def test_pid_liveness_posix_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX branch: ProcessLookupError -> dead, PermissionError -> alive, no error -> alive."""
    monkeypatch.setattr("sys.platform", "linux")

    # Dead PID raises ProcessLookupError on os.kill(pid, 0).
    def raise_lookup(_pid: int, _sig: int) -> None:
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr("os.kill", raise_lookup)
    assert _is_pid_alive(99999) is False

    # Live PID returns None.
    monkeypatch.setattr("os.kill", lambda _pid, _sig: None)
    assert _is_pid_alive(os.getpid()) is True

    # PermissionError means alive but not ours; still counts.
    def raise_perm(_pid: int, _sig: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr("os.kill", raise_perm)
    assert _is_pid_alive(1) is True


def test_pid_liveness_windows_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows branch: OpenProcess returns 0 -> dead; nonzero handle -> alive + CloseHandle called."""
    monkeypatch.setattr("sys.platform", "win32")

    closed_handles: list[int] = []

    class FakeKernel32:
        @staticmethod
        def OpenProcess(_access: int, _inherit: bool, _pid: int) -> int:
            return 0  # dead

        @staticmethod
        def CloseHandle(handle: int) -> int:
            closed_handles.append(handle)
            return 1

    # Patch the cached kernel32 reference inside senex.runlock.
    monkeypatch.setattr("senex.runlock._WIN_KERNEL32", FakeKernel32, raising=False)
    assert _is_pid_alive(99999) is False
    assert closed_handles == []

    class LiveKernel32:
        @staticmethod
        def OpenProcess(_access: int, _inherit: bool, _pid: int) -> int:
            return 1234  # alive

        @staticmethod
        def CloseHandle(handle: int) -> int:
            closed_handles.append(handle)
            return 1

    monkeypatch.setattr("senex.runlock._WIN_KERNEL32", LiveKernel32, raising=False)
    assert _is_pid_alive(99999) is True
    assert closed_handles == [1234]


def test_list_holders_returns_holder_dicts(tmp_path: Path) -> None:
    """list_holders returns the current holders under the advisory lock."""
    RunLock.acquire("fp_list", "run-A", os.getpid(), True, root=tmp_path)
    RunLock.acquire("fp_list", "run-B", os.getpid(), False, root=tmp_path)
    holders = RunLock.list_holders("fp_list", root=tmp_path)
    run_ids = sorted(h["run_id"] for h in holders)
    assert run_ids == ["run-A", "run-B"]
    # Cleanup.
    RunLock.release("fp_list", "run-A", root=tmp_path)
    RunLock.release("fp_list", "run-B", root=tmp_path)


def test_list_holders_missing_lock_returns_empty(tmp_path: Path) -> None:
    """list_holders on a missing lock file returns []."""
    assert RunLock.list_holders("never-existed", root=tmp_path) == []


def test_clear_default_prunes_dead_only(tmp_path: Path) -> None:
    """clear(force=False) removes dead-PID holders only and returns the removed count."""
    stale_pid = 2**31 - 1
    lock = tmp_path / "fp_clear.lock"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "model_id": "m",
        "model_fingerprint": "fp_clear",
        "holders": [
            {"run_id": "dead", "pid": stale_pid, "started_at": "2026-01-01T00:00:00Z",
             "loaded_by_us": True},
            {"run_id": "live", "pid": os.getpid(), "started_at": "2026-01-01T00:00:00Z",
             "loaded_by_us": False},
        ],
    }
    lock.write_text(json.dumps(payload), encoding="utf-8")
    removed = RunLock.clear("fp_clear", root=tmp_path)
    assert removed == 1
    holders = RunLock.list_holders("fp_clear", root=tmp_path)
    assert [h["run_id"] for h in holders] == ["live"]


def test_clear_force_removes_all_holders(tmp_path: Path) -> None:
    """clear(force=True) removes every holder and returns the count."""
    RunLock.acquire("fp_force", "live", os.getpid(), True, root=tmp_path)
    with pytest.warns(UserWarning):
        removed = RunLock.clear("fp_force", force=True, root=tmp_path)
    assert removed == 1
    assert RunLock.list_holders("fp_force", root=tmp_path) == []


def test_clear_missing_lock_returns_zero(tmp_path: Path) -> None:
    assert RunLock.clear("never-existed", root=tmp_path) == 0
