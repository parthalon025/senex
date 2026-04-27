"""Unit tests for senex.atomic_io.write_text_atomic."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from senex.atomic_io import DiskFatalError, write_text_atomic


def test_write_text_atomic_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    result = write_text_atomic(target, "hello")
    assert result == target
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_text_atomic_lf_round_trip(tmp_path: Path) -> None:
    """write_text_atomic encodes UTF-8 byte-faithfully; LF normalization is
    the caller's responsibility (the renderer normalizes upstream)."""
    target = tmp_path / "out.md"
    write_text_atomic(target, "a\nb\n")
    raw = target.read_bytes()
    # No CR injected by us.
    assert b"\r" not in raw
    assert raw == b"a\nb\n"


def test_write_text_atomic_no_partial_on_rename_failure(tmp_path: Path) -> None:
    """Pre-existing target unchanged when os.replace raises."""
    target = tmp_path / "out.md"
    target.write_text("ORIGINAL", encoding="utf-8")

    with patch("senex.atomic_io.os.replace", side_effect=OSError("simulated")):
        with pytest.raises(OSError):
            write_text_atomic(target, "NEW")

    assert target.read_text(encoding="utf-8") == "ORIGINAL"


def test_write_text_atomic_no_target_on_first_write_rename_failure(tmp_path: Path) -> None:
    """If target did not pre-exist and os.replace raises, target still does not exist."""
    target = tmp_path / "newfile.md"
    assert not target.exists()

    with patch("senex.atomic_io.os.replace", side_effect=OSError("simulated")):
        with pytest.raises(OSError):
            write_text_atomic(target, "NEW")

    assert not target.exists()


def test_write_text_atomic_fsyncs_before_rename(tmp_path: Path) -> None:
    """os.fsync must be called BEFORE os.replace (call-order check)."""
    target = tmp_path / "out.md"
    call_log: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def log_fsync(fd: int) -> None:
        call_log.append("fsync")
        real_fsync(fd)

    def log_replace(src: str | bytes | os.PathLike[str], dst: str | bytes | os.PathLike[str]) -> None:
        call_log.append("replace")
        real_replace(src, dst)

    with patch("senex.atomic_io.os.fsync", side_effect=log_fsync), \
         patch("senex.atomic_io.os.replace", side_effect=log_replace):
        write_text_atomic(target, "x")

    assert call_log == ["fsync", "replace"]


def test_write_text_atomic_raises_disk_fatal_on_enospc(tmp_path: Path) -> None:
    """ENOSPC propagates as DiskFatalError (run-killing per ARCH-13)."""
    target = tmp_path / "out.md"
    err = OSError(errno.ENOSPC, "no space left")
    with patch("senex.atomic_io.os.write", side_effect=err):
        with pytest.raises(DiskFatalError):
            write_text_atomic(target, "x")


def test_write_text_atomic_raises_disk_fatal_on_erofs(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    err = OSError(errno.EROFS, "read-only filesystem")
    with patch("senex.atomic_io.os.write", side_effect=err):
        with pytest.raises(DiskFatalError):
            write_text_atomic(target, "x")


def test_write_text_atomic_other_oserror_not_disk_fatal(tmp_path: Path) -> None:
    """A generic OSError (e.g. EPERM) is recoverable, not run-killing."""
    target = tmp_path / "out.md"
    err = OSError(errno.EPERM, "permission denied")
    with patch("senex.atomic_io.os.write", side_effect=err):
        with pytest.raises(OSError) as ei:
            write_text_atomic(target, "x")
    assert not isinstance(ei.value, DiskFatalError)


def test_write_text_atomic_returns_target_path(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    result = write_text_atomic(target, "x")
    assert result == target


def test_renderer_crash_mid_write_leaves_target_intact(tmp_path: Path) -> None:
    """Crash-injection: pre-create the target, mock os.replace to raise,
    assert target still has sentinel content; tmp may exist for inspection."""
    audit_dir = tmp_path
    target = audit_dir / "store" / "sqlite.py.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    sentinel = "# original audit\n"
    target.write_text(sentinel, encoding="utf-8")

    with patch("senex.atomic_io.os.replace", side_effect=OSError("simulated")):
        with pytest.raises(OSError):
            write_text_atomic(target, "# NEW BUT NEVER PUBLISHED\n")

    assert target.read_text(encoding="utf-8") == sentinel
