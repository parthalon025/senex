"""Tests for senex.schema/*.json — validity + audit_response location.oneOf + events union."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema  # type: ignore[import-untyped]
import pytest

SCHEMA_DIR = Path("senex/schema")
ALL_SCHEMAS = [
    "audit_response.schema.json",
    "crosscut_response.schema.json",
    "compaction_response.schema.json",
    "findings_index.schema.json",
    "events.schema.json",
    "checkpoint.schema.json",
]


@pytest.mark.parametrize("name", ALL_SCHEMAS)
def test_schema_is_structurally_valid_draft_2020_12(name: str) -> None:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_audit_response_rejects_finding_without_location() -> None:
    schema = json.loads((SCHEMA_DIR / "audit_response.schema.json").read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema)
    bad = {
        "schema_version": 1,
        "overall_assessment": "x" * 60,
        "findings": [{
            "category": "Correctness", "priority": "high", "title": "x",
            "issue": "i", "why": "w", "fix": "f", "confidence": "high"
            # no location.
        }],
        "recommendations": [],
    }
    assert not v.is_valid(bad)


def test_audit_response_rejects_finding_with_empty_location_oneOf() -> None:
    schema = json.loads((SCHEMA_DIR / "audit_response.schema.json").read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema)
    bad = {
        "schema_version": 1,
        "overall_assessment": "x" * 60,
        "findings": [{
            "category": "Correctness", "priority": "high", "title": "x",
            "issue": "i", "why": "w", "fix": "f", "confidence": "high",
            "location": {"line_end": 10}  # neither symbol nor line_start.
        }],
        "recommendations": [],
    }
    assert not v.is_valid(bad)


def test_audit_response_accepts_finding_with_symbol_only() -> None:
    schema = json.loads((SCHEMA_DIR / "audit_response.schema.json").read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema)
    ok = {
        "schema_version": 1,
        "overall_assessment": "x" * 60,
        "findings": [{
            "category": "Correctness", "priority": "high", "title": "x",
            "issue": "i", "why": "w", "fix": "f", "confidence": "high",
            "location": {"symbol": "validate_user"}
        }],
        "recommendations": [],
    }
    assert v.is_valid(ok)


def test_findings_index_rejects_unknown_priority() -> None:
    schema = json.loads(
        (SCHEMA_DIR / "findings_index.schema.json").read_text(encoding="utf-8")
    )
    v = jsonschema.Draft202012Validator(schema)
    bad_minimal = {
        "schema_version": 1,
        "run": {
            "repo": "r", "run_id": "x", "audit_dir": "/x", "model": "m",
            "model_fingerprint": "f", "lens": "correctness", "lens_version": "1.0.0",
            "started_at": "2026-04-26T00:00:00Z", "duration_seconds": 1,
            "config_hash": "c", "prompt_hash": "p",
        },
        "totals": {"high": 0, "medium": 0, "low": 0, "healthy": 0, "files": 0},
        "themes": [],
        "findings": [{
            "id": "f-aaaaaaaaaaaa", "file": "x", "category": "Correctness",
            "priority": "INVALID", "title": "x", "issue": "i", "why": "w",
            "fix": "f", "confidence": "high",
            "location": {"symbol": "x"}, "report_path": "x.md", "suppressed": False,
            "prompt_hash": "p", "config_hash": "c", "model_fingerprint": "m",
            "lens_version": "1.0.0",
        }],
    }
    assert not v.is_valid(bad_minimal)


def test_events_schema_validates_runstart_event() -> None:
    schema = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema)
    ev = {
        "v": 1, "type": "RunStart", "ts": "2026-04-26T00:00:00Z",
        "seq": 0, "run_id": "01JZ3K7B9C8DQRS4M2EXAMPLE",
        "repo": "r", "audit_dir": "/x", "model": "m", "lens": "correctness",
        "lens_version": "1.0.0",
        "config_hash": "c", "prompt_hash": "p", "model_fingerprint": "f",
        "started_at": "2026-04-26T00:00:00Z",
    }
    assert v.is_valid(ev), list(v.iter_errors(ev))


def test_events_schema_rejects_unknown_event_type() -> None:
    schema = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema)
    bad = {"v": 1, "type": "NotARealEvent", "ts": "2026-04-26T00:00:00Z",
           "seq": 0, "run_id": "x"}
    assert not v.is_valid(bad)


def test_events_schema_has_one_def_per_event_class() -> None:
    from senex.events import ALL_EVENT_TYPES
    schema = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
    defs = set(schema.get("$defs", {}).keys()) - {"BaseFields"}
    class_names = {c.__name__ for c in ALL_EVENT_TYPES}
    assert class_names.issubset(defs), f"missing $defs entries: {class_names - defs}"
