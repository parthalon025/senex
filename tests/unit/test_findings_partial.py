"""Unit tests for senex.findings_partial — NDJSON streaming writer + stable id."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from senex.findings_partial import (
    FindingsPartialWriter,
    compute_finding_id,
)
from senex.render_models import FindingRecord, LocationRecord

FINDINGS_INDEX_SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent.parent / "senex" / "schema" / "findings_index.schema.json").read_text(encoding="utf-8")
)


def _finding_record(**overrides: object) -> FindingRecord:
    base: dict[str, object] = {
        "id": "f-abcdef012345",
        "file": "store/sqlite.py",
        "category": "Robustness",
        "priority": "high",
        "title": "Connection leak on rollback",
        "issue": "rollback does not close cursor",
        "why": "leaks fds",
        "fix": "close in finally",
        "confidence": "high",
        "location": LocationRecord(line_start=142, line_end=156, symbol="rollback_transaction"),
        "report_path": "store/sqlite.py.md",
        "suppressed": False,
        "prompt_hash": "sha256:p",
        "config_hash": "sha256:c",
        "model_fingerprint": "sha256:m",
        "lens_version": "1.0.0",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return FindingRecord(**base)  # type: ignore[arg-type]


def test_append_writes_one_valid_json_line(tmp_path: Path) -> None:
    f = _finding_record()
    with FindingsPartialWriter(tmp_path) as w:
        w.append(f)
    text = (tmp_path / "findings.partial.jsonl").read_text(encoding="utf-8")
    assert text.count("\n") == 1
    parsed = json.loads(text.strip())
    assert parsed["id"] == "f-abcdef012345"
    assert parsed["file"] == "store/sqlite.py"


def test_each_line_validates_against_findings_index_schema(tmp_path: Path) -> None:
    findings = [
        _finding_record(),
        _finding_record(id="f-100000000000", priority="medium",
                        title="Race in batch", issue="race", why="bad", fix="fix it"),
    ]
    with FindingsPartialWriter(tmp_path) as w:
        for f in findings:
            w.append(f)
    text = (tmp_path / "findings.partial.jsonl").read_text(encoding="utf-8")

    finding_subschema = FINDINGS_INDEX_SCHEMA["properties"]["findings"]["items"]
    validator = Draft202012Validator(finding_subschema)

    for line in text.splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        validator.validate(parsed)


def test_finding_id_format(tmp_path: Path) -> None:
    fid = compute_finding_id(
        file="a.py", symbol="foo", line_start=10,
        title="x", prompt_hash="sha256:p",
    )
    assert re.match(r"^f-[a-f0-9]{12}$", fid), fid


def test_partial_write_crash_safety(tmp_path: Path) -> None:
    """5th fsync raises; first 4 lines are intact NDJSON."""
    findings = [_finding_record(id=f"f-{i:012x}", title=f"t{i}") for i in range(5)]

    real_fsync = os.fsync
    call_count = {"n": 0}

    def maybe_raise(fd: int) -> None:
        call_count["n"] += 1
        if call_count["n"] >= 5:
            raise OSError("simulated fsync failure")
        real_fsync(fd)

    with patch("senex.findings_partial.os.fsync", side_effect=maybe_raise):
        with pytest.raises(OSError):
            with FindingsPartialWriter(tmp_path) as w:
                for f in findings:
                    w.append(f)

    text = (tmp_path / "findings.partial.jsonl").read_text(encoding="utf-8")
    lines = text.splitlines()
    valid_count = 0
    for line in lines[:-1]:  # all except the last must parse
        json.loads(line)
        valid_count += 1
    # The last line may parse fine (we crashed AFTER write) OR may be partial.
    try:
        json.loads(lines[-1])
        valid_count += 1
    except json.JSONDecodeError:
        pass  # partial last line is acceptable
    assert valid_count >= 4


def test_context_manager_closes_idempotently(tmp_path: Path) -> None:
    f = _finding_record()
    with FindingsPartialWriter(tmp_path) as w:
        w.append(f)
        w.close()  # explicit
    # __exit__ also calls close; no error.


def test_compute_finding_id_stable() -> None:
    a = compute_finding_id(file="x.py", symbol="foo", line_start=1, title="t", prompt_hash="ph")
    b = compute_finding_id(file="x.py", symbol="foo", line_start=1, title="t", prompt_hash="ph")
    assert a == b


def test_compute_finding_id_sensitive_to_prompt_hash() -> None:
    a = compute_finding_id(file="x.py", symbol="foo", line_start=1, title="t", prompt_hash="ph1")
    b = compute_finding_id(file="x.py", symbol="foo", line_start=1, title="t", prompt_hash="ph2")
    assert a != b


def test_compute_finding_id_sensitive_to_title() -> None:
    a = compute_finding_id(file="x.py", symbol="foo", line_start=1, title="A", prompt_hash="ph")
    b = compute_finding_id(file="x.py", symbol="foo", line_start=1, title="B", prompt_hash="ph")
    assert a != b


def test_compute_finding_id_handles_none_branch() -> None:
    """`symbol` and `line_start` may be None — the `or ''` branch is covered."""
    a = compute_finding_id(file="x.py", symbol=None, line_start=None, title="t", prompt_hash="ph")
    b = compute_finding_id(file="x.py", symbol=None, line_start=None, title="t", prompt_hash="ph")
    assert a == b
    assert re.match(r"^f-[a-f0-9]{12}$", a)
