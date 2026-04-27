"""Tests for senex.checkpoint — atomic state machine + resume-hash compatibility."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from senex.checkpoint import (
    Checkpoint,
    CheckpointCorrupt,
    CheckpointSchemaError,
)

HASHES = {
    "config_hash":            "sha256:cfg",
    "prompt_hash":            "sha256:prm",
    "model_fingerprint":      "sha256:mdl",
    "tool_pack_hash":         "sha256:tlp",
    "lens_version":           "1.0.0",
}
# Per spec §5.5.1 + §8.5: the compaction prompt hash is folded into prompt_hash;
# there is no separate compaction_prompt_hash field on the checkpoint.


def test_checkpoint_create_writes_all_required_fields(tmp_path: Path) -> None:
    Checkpoint.create(
        audit_dir=tmp_path,
        run_id="01JZ3K7B9C8DQRS4M2EXAMPLEZ",
        **HASHES,
    )
    f = tmp_path / "checkpoint.json"
    assert f.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["run_id"] == "01JZ3K7B9C8DQRS4M2EXAMPLEZ"
    assert data["current_phase"] == "preflight"
    assert data["phase_status"]["preflight"] == "pending"
    assert data["completed_files"] == []
    for k, v in HASHES.items():
        assert data[k] == v


def test_checkpoint_mark_done_appends_completed_file(tmp_path: Path) -> None:
    Checkpoint.create(
        audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLEZ", **HASHES
    )
    Checkpoint.mark_done(tmp_path, "src/foo.py")
    data = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    paths = [c["path"] for c in data["completed_files"]]
    assert paths == ["src/foo.py"]


def test_checkpoint_set_phase_updates_status(tmp_path: Path) -> None:
    Checkpoint.create(
        audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLEZ", **HASHES
    )
    Checkpoint.set_phase(tmp_path, "crosscut", "in_progress")
    data = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert data["current_phase"] == "crosscut"
    assert data["phase_status"]["crosscut"] == "in_progress"


def test_checkpoint_atomic_write_unaffected_by_midwrite_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    Checkpoint.create(
        audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLEZ", **HASHES
    )
    original = (tmp_path / "checkpoint.json").read_text(encoding="utf-8")

    from senex import checkpoint as cp_mod

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(cp_mod.os, "replace", boom)
    with pytest.raises(OSError):
        Checkpoint.mark_done(tmp_path, "src/foo.py")
    # Original canonical file is intact.
    assert (tmp_path / "checkpoint.json").read_text(encoding="utf-8") == original


def test_checkpoint_load_corrupt_raises_checkpointcorrupt(tmp_path: Path) -> None:
    (tmp_path / "checkpoint.json").write_text("{ broken json", encoding="utf-8")
    with pytest.raises(CheckpointCorrupt):
        Checkpoint.load(tmp_path)


def test_checkpoint_is_compatible_returns_true_on_full_match(tmp_path: Path) -> None:
    Checkpoint.create(
        audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLEZ", **HASHES
    )
    cp = Checkpoint.load(tmp_path)
    assert cp.is_compatible(HASHES) is True


def test_checkpoint_is_compatible_returns_false_on_any_mismatch(tmp_path: Path) -> None:
    Checkpoint.create(
        audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLEZ", **HASHES
    )
    cp = Checkpoint.load(tmp_path)
    mismatched = {**HASHES, "prompt_hash": "sha256:DIFFERENT"}
    assert cp.is_compatible(mismatched) is False


def test_checkpoint_validates_against_schema(tmp_path: Path) -> None:
    # Simulate a hand-edited file missing a required hash field.
    bad = {
        "schema_version": 1, "run_id": "01JZ3K7B9C8DQRS4M2EXAMPLEZ",
        "current_phase": "preflight", "phase_status": {}, "phase_artifact_hashes": {},
        "completed_files": [],
        # missing prompt_hash, etc.
    }
    (tmp_path / "checkpoint.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(CheckpointSchemaError):
        Checkpoint.load(tmp_path)


def test_checkpoint_module_has_nonempty_docstring() -> None:
    from senex import checkpoint as cp_mod
    assert cp_mod.__doc__ and cp_mod.__doc__.strip() != ""
