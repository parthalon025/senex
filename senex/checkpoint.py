"""senex.checkpoint — atomic state machine for resume.

Implements spec §8.3 (resume) and §8.5 (reproducibility hash bucket).
Every checkpoint carries the full hash field set required for resume-compat.
Conventions §8: atomic write via tmp + fsync + rename.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import jsonschema  # type: ignore[import-untyped]

PhaseName = Literal["preflight", "discovery", "file_audit", "crosscut", "aggregate"]
PhaseStatus = Literal["pending", "in_progress", "complete", "errored"]
ALL_PHASES: tuple[PhaseName, ...] = (
    "preflight", "discovery", "file_audit", "crosscut", "aggregate",
)

_SCHEMA_PATH = Path(__file__).parent / "schema" / "checkpoint.schema.json"


class CheckpointCorrupt(RuntimeError):
    """Raised when checkpoint.json is structurally invalid (unparseable)."""


class CheckpointSchemaError(ValueError):
    """Raised when checkpoint.json fails JSON Schema validation."""


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, indent=2, sort_keys=True))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@dataclass(frozen=True)
class Checkpoint:
    data: dict[str, Any]

    @classmethod
    def create(
        cls,
        audit_dir: Path,
        run_id: str,
        *,
        config_hash: str,
        prompt_hash: str,
        model_fingerprint: str,
        tool_pack_hash: str,
        lens_version: str,
    ) -> Checkpoint:
        # Per spec §5.5.1 + §8.5: compaction prompt hash is folded into
        # ``prompt_hash`` upstream (preflight prompt-snapshot stage).
        # No separate compaction_prompt_hash field is persisted here.
        data: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "current_phase": "preflight",
            "phase_status": {p: "pending" for p in ALL_PHASES},
            "phase_artifact_hashes": {},
            "completed_files": [],
            "config_hash": config_hash,
            "prompt_hash": prompt_hash,
            "model_fingerprint": model_fingerprint,
            "tool_pack_hash": tool_pack_hash,
            "lens_version": lens_version,
        }
        _validator().validate(data)
        audit_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(audit_dir / "checkpoint.json", data)
        return cls(data=data)

    @classmethod
    def load(cls, audit_dir: Path) -> Checkpoint:
        path = audit_dir / "checkpoint.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CheckpointCorrupt(f"checkpoint.json is not valid JSON: {exc}") from exc
        try:
            _validator().validate(data)
        except jsonschema.ValidationError as exc:
            raise CheckpointSchemaError(str(exc)) from exc
        return cls(data=data)

    @classmethod
    def mark_done(cls, audit_dir: Path, file: str) -> None:
        path = audit_dir / "checkpoint.json"
        cp = cls.load(audit_dir)
        new_data = dict(cp.data)
        new_data["completed_files"] = list(cp.data["completed_files"]) + [
            {"path": file, "completed_at": datetime.now(tz=timezone.utc).isoformat()}
        ]
        _atomic_write_json(path, new_data)

    @classmethod
    def set_phase(cls, audit_dir: Path, name: PhaseName, status: PhaseStatus) -> None:
        path = audit_dir / "checkpoint.json"
        cp = cls.load(audit_dir)
        new_data = dict(cp.data)
        phase_status = dict(cp.data["phase_status"])
        phase_status[name] = status
        new_data["phase_status"] = phase_status
        new_data["current_phase"] = name
        _atomic_write_json(path, new_data)

    def is_compatible(self, other_hashes: dict[str, str]) -> bool:
        """Return True iff every hash in ``other_hashes`` matches this checkpoint.

        Note: a compaction-prompt change is detected through ``prompt_hash``
        (the compaction prompt is one of the preflight-snapshotted assets
        that compose ``prompt_hash``; see spec §5.5.1 + §8.5).
        """
        for k in (
            "config_hash", "prompt_hash", "model_fingerprint",
            "tool_pack_hash", "lens_version",
        ):
            if k in other_hashes and other_hashes[k] != self.data[k]:
                return False
        return True
