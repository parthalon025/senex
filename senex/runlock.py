"""senex.runlock — Interprocess refcount file lock with stale-PID prune.

Implements spec §5.5.2.2 (runlock semantics) and §5.5.2.4 (failure modes).
Conventions §8: atomic write via tmp + fsync + rename.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import portalocker


class CorruptRunLock(RuntimeError):
    """Raised when the lockfile is structurally invalid in a way the caller must surface."""


_SAFE_FP = re.compile(r"[^A-Za-z0-9_.-]")


def _lock_path(fingerprint: str, root: Path) -> Path:
    # Sanitize fingerprint into a filesystem-safe name.
    safe = _SAFE_FP.sub("_", fingerprint)
    return root / f"{safe}.lock"


def _is_pid_alive(pid: int) -> bool:
    """Return True if ``pid`` is alive on the current OS."""
    if pid <= 0:
        return False
    if os.name == "nt":
        # Windows: use OpenProcess via ctypes.
        import ctypes  # local import: platform-conditional per conventions §1.

        process_query_limited_information = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but we don't own it.
    return True


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic on POSIX + Windows when same volume.


def _prune_stale(holders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [h for h in holders if _is_pid_alive(int(h["pid"]))]


class RunLock:
    """File-based refcount lock under ``~/.senex/locks/`` (§5.5.2.2)."""

    @classmethod
    def acquire(
        cls,
        fingerprint: str,
        run_id: str,
        pid: int,
        loaded_by_us: bool,
        root: Path | None = None,
    ) -> int:
        """Append the (run_id, pid) holder; return new holder count."""
        root = root or _default_root()
        root.mkdir(parents=True, exist_ok=True)
        lock = _lock_path(fingerprint, root)

        with portalocker.Lock(
            str(lock) + ".portalock", mode="w", timeout=10
        ):
            data = cls._read_or_recreate(lock, fingerprint)
            data["holders"] = _prune_stale(data.get("holders", []))
            data["holders"].append(
                {
                    "run_id": run_id,
                    "pid": pid,
                    "started_at": datetime.now(tz=timezone.utc).isoformat(),
                    "loaded_by_us": loaded_by_us,
                }
            )
            _atomic_write(lock, json.dumps(data, indent=2))
            return len(data["holders"])

    @classmethod
    def release(
        cls,
        fingerprint: str,
        run_id: str,
        root: Path | None = None,
    ) -> int:
        root = root or _default_root()
        lock = _lock_path(fingerprint, root)
        if not lock.exists():
            raise CorruptRunLock(f"release on nonexistent lock: {lock}")
        with portalocker.Lock(
            str(lock) + ".portalock", mode="w", timeout=10
        ):
            data = cls._read_or_recreate(lock, fingerprint)
            before = len(data["holders"])
            data["holders"] = [h for h in data["holders"] if h["run_id"] != run_id]
            if len(data["holders"]) == before:
                raise CorruptRunLock(
                    f"release({run_id}) but no matching holder in {lock}"
                )
            if not data["holders"]:
                lock.unlink(missing_ok=True)
                return 0
            _atomic_write(lock, json.dumps(data, indent=2))
            return len(data["holders"])

    @staticmethod
    def _read_or_recreate(lock: Path, fingerprint: str) -> dict[str, Any]:
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


def _default_root() -> Path:
    return Path.home() / ".senex" / "locks"
