"""senex.findings_aggregator - phase 5 dedupe + sort + atomic findings.json write.

Implements spec section 7.3 (`findings.json` shape) and SCHEMA-4 (NDJSON tolerance:
the LAST line may be partial after a crash; non-last lines must parse).

`findings.json` is **derived**: every run regenerates it from
`findings.partial.jsonl` + the cross-cut output. `senex aggregate` calls this
module standalone for resume / repair.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import ValidationError

from senex.atomic_io import write_text_atomic
from senex.render_models import (
    FindingRecord,
    LocationRecord,
    RunMetadata,
    Theme,
)

log = logging.getLogger(__name__)

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2, "healthy": 3}

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "findings_index.schema.json"


class FindingsValidationFailed(ValueError):
    """A single aggregated finding failed `findings_index.schema.json` validation."""


class AggregatorInputMissing(FileNotFoundError):
    """Catastrophic: the audit directory itself is missing.

    The non-catastrophic case (no `findings.partial.jsonl`) is normal when an
    audit dies before any file completes; the aggregator returns an empty
    `findings.json` rather than raising.
    """


class Aggregator:
    """Phase 5 findings aggregator (dedupe by id, stable sort, atomic write)."""

    def __init__(self) -> None:
        self._schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(self._schema)

    async def run(
        self,
        audit_dir: Path,
        themes: list[Theme] | None,
        run_metadata: RunMetadata,
    ) -> Path:
        """Read partial log, dedupe, sort, validate, atomic-write `findings.json`.

        Args:
            audit_dir: directory containing `findings.partial.jsonl`.
            themes: cross-cutting themes (may be None on crosscut failure).
            run_metadata: run-level stamps for the `run` block.

        Returns:
            Path to the written `findings.json`.

        Raises:
            AggregatorInputMissing: `audit_dir` does not exist.
            DiskFatalError: ENOSPC / EROFS / EDQUOT during the atomic write.
        """
        if not audit_dir.exists():
            raise AggregatorInputMissing(f"audit dir does not exist: {audit_dir}")

        partial = audit_dir / "findings.partial.jsonl"
        deduped: dict[str, FindingRecord] = {}
        if partial.exists():
            self._load_partial(partial, deduped)

        sorted_findings = sorted(
            deduped.values(),
            key=lambda f: (
                PRIORITY_RANK.get(f.priority, 99),
                f.file,
                f.location.line_start or 0,
                f.title,
            ),
        )

        # ``totals.files`` reports the count of files actually audited in
        # this run, NOT the count of distinct files that produced findings
        # (which would be 0 on a clean run, hiding the audit's scope from
        # the operator — see M11 bug 3). Falls back to the distinct-files
        # heuristic only when the caller hasn't populated
        # ``run_metadata.files_audited`` (older callers / tests).
        files_total = (
            run_metadata.files_audited
            if run_metadata.files_audited > 0
            else len({f.file for f in sorted_findings})
        )
        totals = {
            "high": sum(1 for f in sorted_findings if f.priority == "high"),
            "medium": sum(1 for f in sorted_findings if f.priority == "medium"),
            "low": sum(1 for f in sorted_findings if f.priority == "low"),
            "healthy": sum(1 for f in sorted_findings if f.priority == "healthy"),
            "files": files_total,
        }

        index = {
            "schema_version": 1,
            "run": self._serialize_run(run_metadata),
            "totals": totals,
            "themes": [t.model_dump() for t in (themes or [])],
            "findings": [self._dump_finding(f) for f in sorted_findings],
        }

        # Validate the full index. If a single finding fails per-finding shape
        # (e.g. due to a deserialization quirk), drop it and re-run; a full-
        # index failure is a coding bug.
        self._validator.validate(index)

        target = audit_dir / "findings.json"
        body = json.dumps(index, indent=2, sort_keys=False) + "\n"
        return write_text_atomic(target, body)

    # --- helpers ------------------------------------------------------------

    def _load_partial(
        self, partial: Path, deduped: dict[str, FindingRecord]
    ) -> None:
        text = partial.read_text(encoding="utf-8")
        # splitlines() drops the trailing "\n" cleanly. A crash mid-write
        # leaves the file ending mid-line; that bad line shows up here as
        # the last element and either parses or raises JSONDecodeError.
        raw_lines = text.splitlines()
        # Identify lines that carry content (skip blanks).
        non_empty = [(i, line) for i, line in enumerate(raw_lines) if line.strip()]
        if not non_empty:
            return
        last_idx = non_empty[-1][0]
        for i, line in non_empty:
            try:
                record_dict = json.loads(line)
            except json.JSONDecodeError:
                if i == last_idx:
                    log.warning(
                        "findings_aggregator: skipping corrupt last line of "
                        "%s (crash mid-write tolerated)",
                        partial,
                    )
                    continue
                raise FindingsValidationFailed(
                    f"corrupt non-last line {i} in {partial}"
                )
            try:
                record = FindingRecord(
                    **{
                        **record_dict,
                        "location": LocationRecord(**record_dict.get("location", {})),
                    }
                )
            except ValidationError as exc:
                # Skip the offending finding; one bad finding shouldn't kill
                # the aggregate (per plan task 7.3.2).
                log.error(
                    "findings_aggregator: dropping invalid finding "
                    "(line %d in %s): %s",
                    i,
                    partial,
                    exc,
                )
                continue
            deduped[record.id] = record  # last-wins on duplicate id.

    @staticmethod
    def _dump_finding(f: FindingRecord) -> dict[str, object]:
        """Serialize, dropping `None`-valued optional location fields.

        The schema declares `line_start`/`line_end`/`symbol` as integer/string
        (no null variant); pydantic's default dump emits `None` for unset
        Optionals. Strip them so the schema validates cleanly.
        """
        d = f.model_dump()
        loc = d.get("location", {})
        if isinstance(loc, dict):
            d["location"] = {k: v for k, v in loc.items() if v is not None}
        return d

    @staticmethod
    def _serialize_run(meta: RunMetadata) -> dict[str, object]:
        # Project RunMetadata onto the findings_index.schema.json `run` block.
        return {
            "repo": meta.repo,
            "run_id": meta.run_id,
            "audit_dir": meta.audit_dir,
            "model": meta.model,
            "model_fingerprint": meta.model_fingerprint,
            "lens": meta.lens,
            "lens_version": meta.lens_version,
            "started_at": meta.started_at,
            "duration_seconds": meta.duration_seconds,
            "config_hash": meta.config_hash,
            "prompt_hash": meta.prompt_hash,
        }


__all__ = ["Aggregator", "AggregatorInputMissing", "FindingsValidationFailed"]
