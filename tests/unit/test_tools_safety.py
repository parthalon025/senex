"""Tests for senex.tools.safety + senex.tools.exceptions.

Implements M5 Task 5.1 step tests (5.1.1 - 5.1.4): exception hierarchy,
``validate_repo_path`` (path traversal, UNC, drive-absolute, symlinks),
``validate_regex_pattern`` (length, ReDoS, uncompilable), and
``strip_ansi`` + ``redact_tool_result`` composition.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from senex.secret_redactor import SecretRedactor
from senex.tools.exceptions import (
    PathOutsideRepo,
    RegexTimeoutExceeded,
    RegexTooComplex,
    SymlinkRefused,
    ToolBudgetExhausted,
    ToolDispatchFailed,
    ToolingError,
    ToolInputInvalid,
    ToolUnavailable,
)
from senex.tools.safety import (
    redact_tool_result,
    strip_ansi,
    validate_regex_pattern,
    validate_repo_path,
)


# ----- Step 5.1.1: exception hierarchy ---------------------------------------


def test_exception_hierarchy_is_tooling_error() -> None:
    for cls in (
        ToolInputInvalid,
        PathOutsideRepo,
        SymlinkRefused,
        RegexTooComplex,
        RegexTimeoutExceeded,
        ToolDispatchFailed,
        ToolUnavailable,
        ToolBudgetExhausted,
    ):
        assert issubclass(cls, ToolingError), f"{cls.__name__} not ToolingError"
        assert issubclass(cls, Exception), f"{cls.__name__} not Exception"


def test_exceptions_carry_structured_message() -> None:
    table = {
        ToolInputInvalid: "schema_invalid",
        PathOutsideRepo: "path_rejected",
        SymlinkRefused: "path_rejected",
        RegexTooComplex: "regex_invalid",
        RegexTimeoutExceeded: "regex_timeout",
        ToolDispatchFailed: "dispatch_failed",
        ToolUnavailable: "unavailable",
        ToolBudgetExhausted: "budget_exhausted",
    }
    for cls, expected_kind in table.items():
        exc = cls("structured msg")
        assert str(exc) == "structured msg"
        assert exc.kind == expected_kind


# ----- Step 5.1.2: validate_repo_path ----------------------------------------


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return root.resolve()


def test_rejects_dotdot_traversal(repo_root: Path) -> None:
    with pytest.raises(PathOutsideRepo):
        validate_repo_path("../etc/passwd", repo_root)


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are Windows-specific")
def test_rejects_unc_path(repo_root: Path) -> None:
    with pytest.raises(PathOutsideRepo):
        validate_repo_path("\\\\server\\share\\file", repo_root)


def test_rejects_drive_absolute_outside_repo(repo_root: Path, tmp_path: Path) -> None:
    # A path that resolves outside repo_root must be rejected.
    outside = tmp_path / "elsewhere.txt"
    with pytest.raises(PathOutsideRepo):
        validate_repo_path(str(outside), repo_root)


def test_rejects_symlink_target(repo_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("secret\n", encoding="utf-8")
    link = repo_root / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform / privilege")
    with pytest.raises((SymlinkRefused, PathOutsideRepo)):
        validate_repo_path("link.txt", repo_root)


def test_rejects_symlinked_ancestor(repo_root: Path, tmp_path: Path) -> None:
    target_dir = tmp_path / "elsewhere_dir"
    target_dir.mkdir()
    (target_dir / "file.py").write_text("x = 1\n", encoding="utf-8")
    link_dir = repo_root / "linked_dir"
    try:
        os.symlink(target_dir, link_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform / privilege")
    with pytest.raises((SymlinkRefused, PathOutsideRepo)):
        validate_repo_path("linked_dir/file.py", repo_root)


def test_accepts_valid_relative_path(repo_root: Path) -> None:
    out = validate_repo_path("src/main.py", repo_root)
    assert out.is_file()
    assert out == (repo_root / "src" / "main.py").resolve()


def test_accepts_absolute_path_inside_repo(repo_root: Path) -> None:
    abs_path = repo_root / "src" / "main.py"
    out = validate_repo_path(str(abs_path), repo_root)
    assert out == abs_path.resolve()


# ----- Step 5.1.3: validate_regex_pattern ------------------------------------


def test_rejects_oversize_pattern() -> None:
    with pytest.raises(RegexTooComplex):
        validate_regex_pattern("a" * 257)


def test_rejects_catastrophic_backtracking() -> None:
    with pytest.raises((RegexTooComplex, RegexTimeoutExceeded)):
        validate_regex_pattern("(a+)+$")


def test_accepts_safe_pattern() -> None:
    # Safe pattern returns None (no raise).
    assert validate_regex_pattern("safe.*pattern") is None


def test_rejects_uncompilable_pattern() -> None:
    with pytest.raises(RegexTooComplex):
        validate_regex_pattern("[unclosed")


# ----- Step 5.1.4: strip_ansi + redact_tool_result ---------------------------


def test_strip_ansi_removes_csi() -> None:
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_strip_ansi_removes_osc() -> None:
    assert strip_ansi("\x1b]0;title\x07after") == "after"


def test_strip_ansi_preserves_whitespace() -> None:
    s = "\tline1\nline2\r\n"
    assert strip_ansi(s) == s


def test_redact_tool_result_chains_strip_then_redact() -> None:
    redactor = SecretRedactor()
    raw = "\x1b[31mAKIAIOSFODNN7EXAMPLE\x1b[0m"
    out = redact_tool_result(raw, redactor)
    assert "\x1b" not in out
    assert "[REDACTED:aws_access_key]" in out
