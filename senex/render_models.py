"""senex.render_models - pydantic models used by the renderer + aggregator.

Implements spec sections 5.4 (audit_response shape consumed by renderer),
5.4.1 (crosscut_response shape feeding Theme), 7.1 / 7.2 / 7.3 / 7.4 (the
artifacts the operator reads), and SCHEMA-3 (stable id patterns).

Per conventions section 10: every model is `extra="forbid"`. Per convention
section 2: all public fields type-annotated; no Any in public surface.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCallSummary(_StrictModel):
    """One row in the per-file 'tools used' header bullet (section 7.1)."""

    name: str
    count: int = Field(ge=0)


class LocationRecord(_StrictModel):
    """Mirror of audit_response.findings[i].location (section 5.4)."""

    line_start: int | None = None
    line_end: int | None = None
    symbol: str | None = None


class FileMetadata(_StrictModel):
    """Per-file render-time stamps (section 7.1).

    Every field appears at least once in the rendered `<file>.md`. Not
    persisted to disk on its own; embedded in the rendered header by the
    Renderer.
    """

    relpath: str
    language: str
    model_id: str
    lens_name: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    thinking_seconds: float = Field(ge=0.0)
    output_seconds: float = Field(ge=0.0)
    tools_used: list[ToolCallSummary] = Field(default_factory=list)
    tools_max: int = Field(default=0, ge=0)
    compactions_used: int = Field(ge=0)
    compactions_max: int = Field(ge=0)
    graph_context_summary: str
    run_id_short: str  # first 8 chars of ULID
    date: str  # YYYY-MM-DD


class FindingRecord(_StrictModel):
    """Aggregated finding row (section 7.3 findings_index.findings[i])."""

    id: str = Field(pattern=r"^f-[a-f0-9]{12}$")
    file: str
    category: str
    priority: Literal["high", "medium", "low", "healthy"]
    title: str = Field(max_length=120)
    issue: str
    why: str
    fix: str
    confidence: Literal["high", "medium", "low"]
    location: LocationRecord
    report_path: str
    suppressed: bool
    prompt_hash: str
    config_hash: str
    model_fingerprint: str
    lens_version: str


class Theme(_StrictModel):
    """Cross-cutting theme (section 5.4.1). Embedded in findings.json + combined.md."""

    id: str = Field(pattern=r"^t-[a-f0-9]{12}$")
    title: str = Field(max_length=120)
    description: str
    affected_files: list[str]
    priority: Literal["high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]
    recommended_action: str


class SkippedRecord(_StrictModel):
    """One row in combined.md's 'Skipped Files' section (section 7.2)."""

    relpath: str
    reason: str


class ErroredRecord(_StrictModel):
    """One row in combined.md's 'Errored Files' section (section 7.2)."""

    relpath: str
    kind: str
    error_message: str


class RunMetadata(_StrictModel):
    """Run-level stamps for combined.md + findings.json `run` block (section 7.3)."""

    repo: str
    run_id: str
    run_id_short: str
    audit_dir: str
    model: str
    model_fingerprint: str
    lens: str
    lens_version: str
    started_at: str  # ISO-8601 with timezone
    duration_seconds: float = Field(ge=0.0)
    config_hash: str
    prompt_hash: str
    tool_pack_hash: str = ""
    gitnexus_index_hash: str = ""
    files_audited: int = Field(default=0, ge=0)
    files_skipped: int = Field(default=0, ge=0)
    files_errored: int = Field(default=0, ge=0)
    files_with_no_findings: list[str] = Field(default_factory=list)
    date: str  # YYYY-MM-DD


__all__ = [
    "ToolCallSummary",
    "LocationRecord",
    "FileMetadata",
    "FindingRecord",
    "Theme",
    "SkippedRecord",
    "ErroredRecord",
    "RunMetadata",
]
