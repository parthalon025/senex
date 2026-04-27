"""Unit tests for senex.error_artifacts (per-file recovery writers)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from senex.error_artifacts import (
    write_error_artifact,
    write_raw_response,
    write_render_error_artifact,
    write_skipped_artifact,
)


def test_write_error_artifact_shape(tmp_path: Path) -> None:
    target = write_error_artifact(
        audit_dir=tmp_path,
        relpath="store/sqlite.py",
        kind="schema_mismatch",
        error_message="JSON did not validate after 1 retry",
        traceback="Traceback (most recent call last):\n  File \"x\"\n",
    )
    assert target == tmp_path / "store" / "sqlite.py.ERROR.md"
    text = target.read_text(encoding="utf-8")
    assert "schema_mismatch" in text
    assert "JSON did not validate after 1 retry" in text
    assert "Traceback" in text


def test_write_error_artifact_uses_atomic_write(tmp_path: Path) -> None:
    """Pre-existing ERROR.md unchanged when os.replace raises."""
    target_dir = tmp_path / "store"
    target_dir.mkdir()
    target = target_dir / "sqlite.py.ERROR.md"
    target.write_text("PREEXISTING", encoding="utf-8")

    with patch("senex.atomic_io.os.replace", side_effect=OSError("simulated")):
        with pytest.raises(OSError):
            write_error_artifact(
                audit_dir=tmp_path,
                relpath="store/sqlite.py",
                kind="x",
                error_message="x",
                traceback="x",
            )
    assert target.read_text(encoding="utf-8") == "PREEXISTING"


def test_write_error_artifact_applies_redactor(tmp_path: Path) -> None:
    target = write_error_artifact(
        audit_dir=tmp_path,
        relpath="x.py",
        kind="x",
        error_message="leaked: sk-FAKE-secret-1234567890abcdefghij",
        traceback="t",
    )
    text = target.read_text(encoding="utf-8")
    assert "sk-FAKE-secret-1234567890abcdefghij" not in text
    assert "[REDACTED:llm_api_key]" in text


def test_write_skipped_artifact_shape(tmp_path: Path) -> None:
    target = write_skipped_artifact(
        audit_dir=tmp_path,
        relpath="big/dump.txt",
        reason="too large: 1048576 bytes > 524288",
    )
    assert target == tmp_path / "big" / "dump.txt.SKIPPED.md"
    text = target.read_text(encoding="utf-8")
    assert "too large: 1048576 bytes > 524288" in text


def test_write_skipped_artifact_atomic(tmp_path: Path) -> None:
    target_dir = tmp_path / "big"
    target_dir.mkdir()
    target = target_dir / "dump.txt.SKIPPED.md"
    target.write_text("PREEXISTING", encoding="utf-8")

    with patch("senex.atomic_io.os.replace", side_effect=OSError("simulated")):
        with pytest.raises(OSError):
            write_skipped_artifact(
                audit_dir=tmp_path,
                relpath="big/dump.txt",
                reason="x",
            )
    assert target.read_text(encoding="utf-8") == "PREEXISTING"


def test_write_raw_response_shape(tmp_path: Path) -> None:
    raw_json = '{"foo": "bar", "n": 42}'
    target = write_raw_response(
        audit_dir=tmp_path,
        relpath="x.py",
        raw_json=raw_json,
    )
    assert target == tmp_path / "x.py.RAW.json"
    # The raw JSON is preserved verbatim (apart from any redaction).
    assert target.read_text(encoding="utf-8").strip() == raw_json


def test_write_raw_response_redacts_secrets(tmp_path: Path) -> None:
    raw_json = '{"leak": "sk-FAKE-secret-1234567890abcdefghij"}'
    target = write_raw_response(
        audit_dir=tmp_path,
        relpath="x.py",
        raw_json=raw_json,
    )
    text = target.read_text(encoding="utf-8")
    assert "sk-FAKE-secret-1234567890abcdefghij" not in text


def test_write_render_error_artifact_shape(tmp_path: Path) -> None:
    target = write_render_error_artifact(
        audit_dir=tmp_path,
        relpath="x.py",
        traceback="Traceback (most recent call last):\n",
        response_dump='{"schema_version": 1, "findings": []}',
    )
    assert target == tmp_path / "x.py.RENDER_ERROR.md"
    text = target.read_text(encoding="utf-8")
    assert "Traceback" in text
    assert "schema_version" in text


def test_write_render_error_artifact_does_not_kill_run(tmp_path: Path) -> None:
    """Wrapper test: writer returns normally (no re-raise)."""
    target = write_render_error_artifact(
        audit_dir=tmp_path,
        relpath="x.py",
        traceback="t",
        response_dump="{}",
    )
    assert target.exists()
