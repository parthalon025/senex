"""senex.atomic_io - tmp+fsync+rename atomic write helper.

Implements spec ARCH-13 (atomic per-file write order) and the disk-fatal
escalation policy (ENOSPC / EROFS / EDQUOT propagate as DiskFatalError;
all other write failures are recoverable per-file errors).

Used by Renderer.write_file_atomic, Aggregator.run, write_handoff, and the
error/skipped/raw artifact writers.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path

# Only these errnos kill the run. All other write failures are recoverable
# per-file errors (renderer crash, schema mismatch, etc.).
_DISK_FATAL_ERRNOS: frozenset[int] = frozenset(
    code
    for name in ("ENOSPC", "EROFS", "EDQUOT")
    for code in (getattr(errno, name, None),)
    if code is not None
)


class DiskFatalError(OSError):
    """Disk-fatal write failure (ENOSPC / EROFS / EDQUOT). Run-killing per ARCH-13."""


def write_text_atomic(target: Path, text: str, encoding: str = "utf-8") -> Path:
    """Atomically write `text` to `target` via tmp+fsync+rename.

    Order:
        1. Write the encoded bytes to `<target>.tmp`.
        2. Open the tmp read-only and call `os.fsync(fd)` so the bytes are
           on disk before the rename publishes them.
        3. `os.replace(tmp, target)` — atomic on POSIX and on NTFS for files
           on the same volume, which is the only supported case.

    Crash semantics:
        - Crash before step 3: tmp exists, target unchanged (or absent on
          first-write).
        - Crash mid-rename: the OS guarantees the rename is atomic.

    Raises:
        DiskFatalError: when the underlying OSError has errno in
            {ENOSPC, EROFS, EDQUOT}. Run-killing.
        OSError: any other write failure; recoverable per-file.

    Returns:
        The `target` path, unchanged.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    data = text.encode(encoding)
    try:
        # Write + fsync via os.open so we hold the fd at sync time.
        # O_BINARY on Windows preserves byte-for-byte content.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(tmp, flags, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)
    except OSError as exc:
        if exc.errno in _DISK_FATAL_ERRNOS:
            raise DiskFatalError(
                exc.errno, f"disk-fatal write failure: {exc.strerror}", str(target)
            ) from exc
        raise
    return target


__all__ = ["DiskFatalError", "write_text_atomic"]
