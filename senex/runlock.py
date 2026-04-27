"""senex.runlock - Interprocess refcount file lock with stale-PID prune.

Implements spec sections 5.5.2.2 (runlock semantics) and 5.5.2.4 (failure modes).
Conventions section 8: atomic write via tmp + fsync + rename.

Hardened in M4 Task 4.0:
- Atomicity envelope: portalocker advisory lock spans read -> prune -> write -> rename.
- Cross-platform PID liveness (POSIX os.kill + Windows OpenProcess via ctypes).
- Corrupt lockfile recovery: rename to ``<lock>.corrupt-<unix_ts>`` then start fresh.
- ``list_holders()`` and ``clear(force=...)`` for the ``senex lifecycle`` CLI (M4 Task 4.4).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import portalocker


class RunLockError(Exception):
    """Base exception for runlock failures."""


class RunLockCorrupt(RunLockError):
    """Raised when the lockfile is structurally invalid in a way the caller must surface.

    Note: corrupt JSON is auto-recovered (renamed + recreated); this exception is
    reserved for semantic violations like ``release()`` of a holder that does not
    exist in the file.
    """


# Backwards-compatible alias from the M1 stub.
CorruptRunLock = RunLockCorrupt


_SAFE_FP = re.compile(r"[^A-Za-z0-9_.-]")


def _lock_path(fingerprint: str, root: Path) -> Path:
    """Sanitize fingerprint into a filesystem-safe name (strips ``:`` etc.)."""
    safe = _SAFE_FP.sub("_", fingerprint)
    return root / f"{safe}.lock"


def _guard_path(lock: Path) -> Path:
    """Return the sibling advisory-lock guard file for ``lock``.

    The advisory lock is taken on a ``.guard`` file rather than on ``lock`` itself
    so that we can safely ``os.replace(tmp, lock)`` while still holding the lock.
    """
    return lock.with_suffix(lock.suffix + ".guard")


# Cached kernel32 handle on Windows. Module-level so tests can monkeypatch.
if sys.platform == "win32":  # pragma: no cover - imported for type only on POSIX
    import ctypes

    _WIN_KERNEL32: Any = ctypes.WinDLL("kernel32")
else:
    _WIN_KERNEL32 = None  # type: ignore[assignment]

# Windows access mask: PROCESS_QUERY_LIMITED_INFORMATION.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _is_pid_alive(pid: int) -> bool:
    """Return True if ``pid`` is alive on the current OS.

    POSIX: ``os.kill(pid, 0)`` raises ``ProcessLookupError`` for dead PIDs and
    ``PermissionError`` for live-but-not-ours processes. Both "permission" and
    "no error" mean alive.

    Windows: ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, ...)`` returns 0
    for dead PIDs; a nonzero handle means alive (and must be closed via
    ``CloseHandle``).
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        # Re-resolve the cached handle each call so monkeypatched test doubles take effect.
        from senex import runlock as _self

        handle = _self._WIN_KERNEL32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        _self._WIN_KERNEL32.CloseHandle(handle)
        return True

    # POSIX
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but we don't own it.
    return True


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Serialize ``data`` to JSON and atomically replace ``path``.

    Uses tmp file + fsync + ``os.replace`` (atomic on POSIX and on Windows when
    both files share a volume).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2)
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_or_recover(lock: Path, fingerprint: str) -> dict[str, Any]:
    """Read the lockfile, or rename + recreate if it is corrupt.

    Caller MUST hold the advisory lock around this call.
    """
    if not lock.exists():
        return {
            "schema_version": 1,
            "model_id": "",
            "model_fingerprint": fingerprint,
            "holders": [],
        }
    try:
        return json.loads(lock.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        ts = int(time.time())
        corrupt = lock.with_suffix(lock.suffix + f".corrupt-{ts}")
        os.replace(lock, corrupt)
        return {
            "schema_version": 1,
            "model_id": "",
            "model_fingerprint": fingerprint,
            "holders": [],
        }


def _prune_dead(holders: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Filter holders to only live PIDs. Returns (live_holders, dead_count)."""
    live: list[dict[str, Any]] = []
    dead = 0
    for h in holders:
        if _is_pid_alive(int(h["pid"])):
            live.append(h)
        else:
            dead += 1
    return live, dead


def _default_root() -> Path:
    return Path.home() / ".senex" / "locks"


class RunLock:
    """File-based refcount lock under ``~/.senex/locks/`` (spec 5.5.2.2).

    Atomicity envelope (acquire and release):

        with portalocker.Lock(<lock>.guard):
            data = read_or_recover(lock)
            data['holders'] = prune_dead(data['holders'])
            data['holders'].append/remove(...)
            atomic_write_json(<lock>.tmp -> os.replace -> <lock>)
    """

    @classmethod
    def acquire(
        cls,
        fingerprint: str,
        run_id: str,
        pid: int,
        loaded_by_us: bool,
        *,
        root: Path | None = None,
    ) -> int:
        """Append the (run_id, pid) holder; return new holder count.

        Atomic over the entire read -> prune -> append -> write window thanks
        to the portalocker advisory lock on a sibling ``.guard`` file.
        """
        root = root or _default_root()
        root.mkdir(parents=True, exist_ok=True)
        lock = _lock_path(fingerprint, root)

        with portalocker.Lock(str(_guard_path(lock)), mode="w", timeout=10):
            data = _read_or_recover(lock, fingerprint)
            holders, _dead = _prune_dead(data.get("holders", []))
            holders.append(
                {
                    "run_id": run_id,
                    "pid": pid,
                    "started_at": datetime.now(tz=timezone.utc).isoformat(),
                    "loaded_by_us": loaded_by_us,
                }
            )
            data["holders"] = holders
            data["model_fingerprint"] = fingerprint
            _atomic_write_json(lock, data)
            return len(holders)

    @classmethod
    def release(
        cls,
        fingerprint: str,
        run_id: str,
        *,
        root: Path | None = None,
    ) -> int:
        """Remove the holder for ``run_id``; return remaining holder count.

        Raises RunLockCorrupt if the lockfile is missing or the run_id has no
        matching holder.
        """
        root = root or _default_root()
        lock = _lock_path(fingerprint, root)

        with portalocker.Lock(str(_guard_path(lock)), mode="w", timeout=10):
            if not lock.exists():
                raise RunLockCorrupt(f"release on nonexistent lock: {lock}")
            data = _read_or_recover(lock, fingerprint)
            before = len(data["holders"])
            data["holders"] = [h for h in data["holders"] if h["run_id"] != run_id]
            if len(data["holders"]) == before:
                raise RunLockCorrupt(
                    f"release({run_id}) but no matching holder in {lock}"
                )
            if not data["holders"]:
                lock.unlink(missing_ok=True)
                return 0
            _atomic_write_json(lock, data)
            return len(data["holders"])

    @classmethod
    def list_holders(
        cls,
        fingerprint: str,
        *,
        root: Path | None = None,
    ) -> list[dict[str, Any]]:
        """Read-only snapshot of current holders. Holds the advisory lock for the read."""
        root = root or _default_root()
        lock = _lock_path(fingerprint, root)
        if not lock.exists():
            return []
        # Make sure the parent dir exists so portalocker can create the guard file.
        root.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(_guard_path(lock)), mode="w", timeout=10):
            if not lock.exists():
                return []
            data = _read_or_recover(lock, fingerprint)
            holders: list[dict[str, Any]] = list(data.get("holders", []))
            return holders

    @classmethod
    def clear(
        cls,
        fingerprint: str,
        *,
        force: bool = False,
        root: Path | None = None,
    ) -> int:
        """Prune holders.

        force=False: remove dead-PID holders only; live holders remain.
        force=True: remove all holders, emitting a UserWarning for each live PID removed.

        Returns the number of holders removed.
        """
        root = root or _default_root()
        lock = _lock_path(fingerprint, root)
        if not lock.exists():
            return 0
        root.mkdir(parents=True, exist_ok=True)

        with portalocker.Lock(str(_guard_path(lock)), mode="w", timeout=10):
            if not lock.exists():
                return 0
            data = _read_or_recover(lock, fingerprint)
            holders = list(data.get("holders", []))
            kept: list[dict[str, Any]] = []
            removed = 0
            for h in holders:
                alive = _is_pid_alive(int(h["pid"]))
                if alive and not force:
                    kept.append(h)
                    continue
                if alive and force:
                    warnings.warn(
                        f"clear(force=True) removing live holder run_id={h.get('run_id')!r} "
                        f"pid={h.get('pid')}",
                        UserWarning,
                        stacklevel=2,
                    )
                removed += 1
            data["holders"] = kept
            if not kept:
                lock.unlink(missing_ok=True)
            else:
                _atomic_write_json(lock, data)
            return removed
