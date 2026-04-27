# Milestone 7: Renderer + Aggregator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M7 turns structured `AuditResponse` JSON into the human-facing artifacts: per-file Markdown reports, the streaming `findings.partial.jsonl` log, the deduped+sorted `findings.json`, the cross-cutting themes pass, the `combined.md` summary, the `claude-handoff.md`, and the failure-artifact writers (`<file>.ERROR.md`, `<file>.SKIPPED.md`, `<file>.RAW.json`). Every file the operator (or downstream Claude Code) actually reads comes from this milestone.

**Architectural intent:** Layer 4 is "rendering and aggregation" — it depends on Layers 0-3 (config, schemas, client, tools) but knows nothing about phases or the auditor coroutine. The renderer is byte-deterministic for golden tests; aggregation is content-deterministic (stable IDs, stable sort). Per-file writes are atomic so a crash mid-write never leaves a partial `<file>.md` on disk. Renderer crashes are explicitly *non-fatal* per spec §ARCH-13 — they produce `<file>.RENDER_ERROR.md` and the run continues. Only disk-fatal errors (ENOSPC, EROFS) are run-killing.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration, M5 Tool Framework, M6 Compaction
- **Required modules from prior work:**
  - `senex/schema/audit_response.schema.json` — renderer input shape (M1 §5.4)
  - `senex/schema/crosscut_response.schema.json` — crosscut output shape (M1 §5.4.1)
  - `senex/schema/findings_index.schema.json` — `findings.json` shape (M1 §7.3)
  - `senex/lmstudio_client.py` — used by `CrossCutter` for the crosscut LLM call (M3)
  - `senex/prompts/cross_cutting.md` + `prompts/claude_handoff.md` (M2 Task 2.5)
  - `senex/secret_redactor.py` — applied to every rendered artifact per §5.10 (M1)
  - `senex/events.py` — `CrosscutStart`, `CrosscutComplete` (M1)
  - `senex/config.py` — `CrosscutCfg.top_n_high`, `top_n_medium`, `top_n_low` (M1)
- **Required tools/state:**
  - Hand-crafted `tests/golden/sample_file.input.json` and matching `tests/golden/sample_file.expected.md` for byte-match regression test (committed in repo)
  - `tests/recorded/` LMS replay infrastructure from M3 (used for crosscut tests)
  - Pydantic v2 (already pinned by M1)

## Deliverable

This milestone creates the following files:

- `senex/renderer.py` — `Renderer` class: `render_file()`, `render_combined()`, `write_file_atomic()`
- `senex/findings_partial.py` — `FindingsPartialWriter` (NDJSON streaming append, line-buffered, fsync-per-line)
- `senex/findings_aggregator.py` — `Aggregator.run(audit_dir, themes) -> Path` (dedupe + sort + atomic write)
- `senex/cross_cutting.py` — `CrossCutter.run(client, lens, findings) -> list[Theme] | None` (LMS-backed; tolerant)
- `senex/handoff.py` — `write_handoff(audit_dir, run_metadata, top_findings) -> Path` (references-only per §POL-2)
- `senex/atomic_io.py` — `write_text_atomic()` helper (tmp + fsync + rename) used across all renderers
- `senex/error_artifacts.py` — `write_error_artifact()`, `write_skipped_artifact()`, `write_raw_response()`, `write_render_error_artifact()` for §8.2 per-file recovery
- `senex/render_models.py` — pydantic models: `FileMetadata`, `RunMetadata`, `FindingRecord`, `Theme`, `ToolCallSummary`
- `tests/golden/test_renderer.py`, `tests/golden/sample_file.input.json`, `tests/golden/sample_file.expected.md`
- `tests/unit/test_findings_partial.py`, `test_findings_aggregator.py`, `test_cross_cutting.py`, `test_handoff.py`, `test_atomic_io.py`, `test_error_artifacts.py`, `test_render_combined.py`

## Downstream consumers

- **M8** `FileAuditPhase` calls `Renderer.render_file()` per file, then `Renderer.write_file_atomic()`, then `FindingsPartialWriter.append()` per finding (per §ARCH-11 ordering: tmp+fsync → partial append → checkpoint → rename → emit `FileComplete`).
- **M8** `AggregatePhase` calls `Aggregator.run()` then `write_handoff()` then `Renderer.render_combined()`.
- **M8** `CrosscutPhase` calls `CrossCutter.run()`; passes themes to `AggregatePhase`. On `None` return, `AggregatePhase` proceeds with `themes=None` and `combined.md` notes the gap.
- **M8** `FileAuditPhase` calls the failure-artifact writers on per-file errors (schema mismatch, skip, render crash).
- **M10** Live gate 13a verifies `combined.md`, `findings.json`, `claude-handoff.md`, and at least one `<file>.md` exist after a successful audit.

## Spec sections referenced

- §7.1 Per-File Report — header format, A/B/C/Healthy categorization, recommendations with code blocks, best practices table
- §7.2 Combined Report (`combined.md`) — run header, priority rollup, themes, top findings table, files-with-no-findings, skipped, errored, run metadata
- §7.3 `findings.json` — schema, run + totals + themes + findings array
- §7.4 `claude-handoff.md` — handoff structure (top-3 findings summary, references, no full content embed)
- §5.4 `audit_response` schema — renderer input contract
- §5.4.1 `crosscut_response` schema — crosscut output contract
- §8.2 Per-file Recovery — `<file>.ERROR.md`, `<file>.SKIPPED.md`, `<file>.RAW.json`, `<file>.RENDER_ERROR.md` artifact shapes
- §SCHEMA-3 stable IDs — `f-` finding id derivation (`sha256(file + symbol + line_start + title + prompt_hash)[:12]`); `t-` theme id derivation
- §SCHEMA-4 streaming findings persistence — NDJSON append-only, partial-line tolerance
- §POL — pipe-escape rules in markdown tables (`best_practices_table`, top findings table)
- §POL-2 — handoff does not embed full findings content (references only)
- §ARCH-11 — atomic per-file write order (tmp → partial append → checkpoint → rename)
- §ARCH-12 — crosscut input cap (50 high + 100 medium + 100 low) + hierarchical chunking shape
- §ARCH-13 — renderer crash → `<file>.RENDER_ERROR.md`, run continues; only disk-fatal kills the run
- §SEC-10 — validate JSON FIRST, render SECOND; never write `<file>.md` without successful validation
- §5.10 — `SecretRedactor` applied to every rendered string before disk

## Key contracts

- **`class Renderer:`**
  - `__init__(self, output_root: Path, redactor: SecretRedactor) -> None`
  - `render_file(self, response: AuditResponse, file_metadata: FileMetadata) -> str` — pure; returns markdown string. Applies redactor as the *last* step.
  - `render_combined(self, run_metadata: RunMetadata, findings: list[FindingRecord], themes: list[Theme] | None, skipped: list[SkippedRecord], errored: list[ErroredRecord]) -> str` — pure; returns combined markdown.
  - `write_file_atomic(self, audit_dir: Path, relpath: str, markdown: str) -> Path` — wraps `write_text_atomic`; returns final `<file>.md` path.

- **`class FileMetadata(BaseModel):`** (pydantic v2, `extra="forbid"`)
  - `relpath: str`, `language: str`, `model_id: str`, `lens_name: str`
  - `prompt_tokens: int`, `completion_tokens: int`
  - `thinking_seconds: float`, `output_seconds: float`
  - `tools_used: list[ToolCallSummary]`, `compactions_used: int`, `compactions_max: int`
  - `graph_context_summary: str` (one-line, e.g., `cluster=retrieval/factory | callers d=1: 14 | processes: ...`)
  - `run_id_short: str` (first 8 chars of ULID), `date: str` (`YYYY-MM-DD`)

- **`class ToolCallSummary(BaseModel):`** `name: str`, `count: int`

- **`class FindingRecord(BaseModel):`** (pydantic v2, `extra="forbid"`)
  - `id: str` (pattern `^f-[a-f0-9]{12}$`), `file: str`, `category: str`
  - `priority: Literal["high", "medium", "low", "healthy"]`
  - `title: str`, `issue: str`, `why: str`, `fix: str`
  - `confidence: Literal["high", "medium", "low"]`
  - `location: LocationRecord` (one of `symbol` or `line_start` required per §5.4)
  - `report_path: str`, `suppressed: bool`
  - `prompt_hash: str`, `config_hash: str`, `model_fingerprint: str`, `lens_version: str`

- **`class Theme(BaseModel):`** id `^t-[a-f0-9]{12}$` per §5.4.1.

- **`class RunMetadata(BaseModel):`** matches `findings.json` `run` block per §7.3 + `files_audited`, `files_skipped`, `files_errored`, `duration_seconds`.

- **`class FindingsPartialWriter:`** context-manager
  - `__init__(self, audit_dir: Path) -> None` — opens `<audit_dir>/findings.partial.jsonl` in append-binary buffered mode.
  - `append(self, finding: FindingRecord) -> None` — single `write()` of `model_dump_json() + "\n"`; explicit `flush()` then `os.fsync(fd)`.
  - `close(self) -> None` — flushes + closes; idempotent.
  - `__enter__/__exit__` — context manager wraps `close()`.

- **`class Aggregator:`**
  - `async def run(self, audit_dir: Path, themes: list[Theme] | None, run_metadata: RunMetadata) -> Path` — reads `findings.partial.jsonl`, dedupes by `id` (last-wins), sorts, validates against `findings_index.schema.json`, writes `findings.json` via `write_text_atomic`.

- **`class CrossCutter:`**
  - `async def run(self, client: LMStudioClient, lens: Lens, findings: list[FindingRecord], cfg: CrosscutCfg) -> list[Theme] | None` — caps input per §ARCH-12, calls LMS, returns themes or `None` on failure.

- **Module-level helpers:**
  - `def write_text_atomic(target: Path, text: str, encoding: str = "utf-8") -> Path` — tmp + fsync + rename (`os.replace`).
  - `def write_handoff(audit_dir: Path, run_metadata: RunMetadata, top_findings: list[FindingRecord]) -> Path`
  - `def write_error_artifact(audit_dir: Path, relpath: str, kind: str, error_message: str, traceback: str) -> Path`
  - `def write_skipped_artifact(audit_dir: Path, relpath: str, reason: str) -> Path`
  - `def write_raw_response(audit_dir: Path, relpath: str, raw_json: str) -> Path`
  - `def write_render_error_artifact(audit_dir: Path, relpath: str, traceback: str, response_dump: str) -> Path` (per §ARCH-13)
  - `def compute_finding_id(file: str, symbol: str | None, line_start: int | None, title: str, prompt_hash: str) -> str`
  - `def compute_theme_id(title: str, run_id: str) -> str`

## Named exceptions

All exception classes live at module top-level (per conventions §4):

- `RenderError` (in `renderer.py`) — base for renderer-internal failures. Caught at the auditor boundary; produces `<file>.RENDER_ERROR.md`. **NOT run-killing** unless re-raised by `DiskFatalError`.
- `FindingsValidationFailed` (in `findings_aggregator.py`) — raised when an aggregated finding fails `findings_index.schema.json` validation. Logged + emits `FileError`; aggregator skips the offending finding and continues.
- `AggregatorInputMissing` (in `findings_aggregator.py`) — raised when `findings.partial.jsonl` does not exist at aggregation time (run aborted before any file completed). Run-level decision; the aggregator returns an empty `findings.json` with `totals = {0,0,0,0,0}` rather than crashing.
- `DiskFatalError` (in `atomic_io.py`) — wraps `OSError` with `errno in {ENOSPC, EROFS, EDQUOT}`. Run-killing per §ARCH-13. Re-raised from `write_text_atomic`.
- `HandoffPolicyViolation` (in `handoff.py`) — raised by handoff writer's self-check if any finding's `issue`/`why`/`fix` text is detected in the rendered handoff string. Defense-in-depth against §POL-2 regressions.

## Watch-outs

- **Byte-match test is brittle by design.** A single newline drift fails it. That's the point — the renderer's output is referenced from `claude-handoff.md` and embedded in CI artifacts; drift here means downstream consumers diff cleanly. When you intentionally change the format, regenerate the golden file in the same commit (`feat` or `refactor` type, body explains why).
- **`finding_id` MUST be stable.** `"f-" + sha256(file + (symbol or "") + (line_start or "") + title + prompt_hash)[:12]`. Two re-audits with identical inputs produce identical ids → last-wins dedupe works. Including `prompt_hash` means a prompt change creates new ids (intentional — different lens/prompt = different finding identity per §SCHEMA-3).
- **Pipe-escape in markdown tables.** Any cell value containing `|` is escaped as `\|` before insertion into `best_practices_table` and the top-findings table. Test with a deliberate `|` in a fixture (§POL).
- **Newline normalization.** Replace `\r\n` and `\r` with `\n` in all model-supplied text before insertion into markdown. Otherwise the byte-match test thrashes on Windows-origin fixtures.
- **CrossCutter is tolerant.** On any LMS failure (HTTP, timeout, schema mismatch after retry), return `None` and let the run continue; `combined.md` notes "[cross-cutting themes unavailable: <reason>]". Do NOT raise (per §8.3 "Cross-cutting pass failure: don't fail run").
- **Atomic write is the *only* write pattern.** Every `<file>.md`, `findings.json`, `combined.md`, `claude-handoff.md`, `<file>.ERROR.md`, `<file>.SKIPPED.md`, `<file>.RAW.json`, `<file>.RENDER_ERROR.md` writes through `write_text_atomic()`. A crash mid-write must leave the previous version intact (or no file at all for first-write).
- **Crosscut input cap (§ARCH-12):** 50 high + 100 medium + 100 low (defaults; tunable in `[crosscut]`). Exceeding this is a silent over-cost. Truncate, don't error. v1 ships the degenerate single-level case; the `_chunk_for_crosscut(findings)` helper signature accepts a `cluster_map: dict[str, list[FindingRecord]] | None` arg shaped for future hierarchical extension.
- **Handoff is reference-only (§POL-2).** `claude-handoff.md` summarizes top-3 findings as `[PRIORITY] file:line — title` ONLY and points at the per-file reports. It does NOT embed `issue`/`why`/`fix` content. Otherwise it duplicates `combined.md` AND leaks secrets to a downstream Claude session even if redaction is later relaxed. The writer self-checks; on detection, raise `HandoffPolicyViolation`.
- **Validate JSON FIRST, render SECOND (§SEC-10).** The renderer trusts its input is already validated against `audit_response.schema.json`. The auditor (M8) is responsible for validation; the renderer's failure mode for malformed input is an explicit `RenderError` with the failing field path, NOT silent corruption.
- **Per-finding stamps preserve reproducibility buckets (§7.3).** `prompt_hash`, `config_hash`, `model_fingerprint`, `lens_version` are stamped on every `FindingRecord` at write time, not at aggregation time. Heterogeneous resume across config changes can then be rendered in per-bucket sections by the aggregator (§8.5; v1 emits a warning + flat list, but the metadata is preserved for forward compatibility).
- **Renderer crashes are non-fatal (§ARCH-13).** A bug in the renderer that raises on a validated response writes `<file>.RENDER_ERROR.md` containing the traceback + the structured response that triggered the crash, emits `FileError`, and the run continues. Only `DiskFatalError` (ENOSPC/EROFS/EDQUOT) propagates to the run level.
- **Secret redaction at the boundary (§5.10).** Apply `SecretRedactor.redact()` to *every* string flowing into a rendered artifact: `<file>.md`, `combined.md`, `claude-handoff.md`, `<file>.ERROR.md`, `<file>.RAW.json` (yes — the raw response can contain secrets the model parroted from a tool result). Apply once, at the rendered-string boundary, before `write_text_atomic`.
- **Encoding is UTF-8, line endings are LF (§8 conventions).** `write_text(..., encoding="utf-8", newline="\n")`. Never depend on platform default.
- **Sort stability matters for golden tests.** Within a priority bucket, sort by `(file, location.line_start or 0, title)` — three-key total ordering, never relying on input order.

## Patterns to follow

- **Golden-file regression for the renderer:** hand-craft input + expected output once; subsequent edits regenerate the expected file in the same commit. Strongest guarantee against silent format drift.
- **NDJSON for streaming:** one JSON object per line, each line schema-valid in isolation. Recovery from process death = "drop the last partial line, append new lines" (§SCHEMA-4). Aggregator reads with `for line in f:` and skips `json.JSONDecodeError` on the *last* line only (logs the skip).
- **Atomic helper used everywhere:** implement `write_text_atomic()` once in `senex/atomic_io.py` and have every renderer call it. No bespoke tmp-rename code in feature modules.
- **Pydantic-first data flow:** every artifact passes through a pydantic model (`FileMetadata`, `FindingRecord`, `RunMetadata`, `Theme`) before serialization. The model is the contract; markdown formatting is a pure function of the model.
- **Pure renderers, side-effecting writers:** `render_file()` and `render_combined()` are pure (input → string). All disk I/O is in the writer functions. This makes the byte-match test a one-liner and keeps the file system out of the unit-test path.

## Tasks

### Task 7.1: Renderer (structured → markdown) + golden-file test

**Files:**
- Create: `senex/renderer.py`
- Create: `senex/render_models.py`
- Create: `tests/golden/test_renderer.py`
- Create: `tests/golden/sample_file.input.json`
- Create: `tests/golden/sample_file.expected.md`

- [ ] **Step 7.1.1: Hand-craft `tests/golden/sample_file.input.json`** as a fully-populated `AuditResponse` instance:
  - `schema_version: 1`
  - `overall_assessment` ≥ 50 chars (§5.4 minLength)
  - 3 findings: 1 high, 1 medium, 1 low — each with distinct `category`, `title`, `issue`, `why`, `fix`, `confidence`, and `location` (one with `symbol` only, one with `line_start`+`line_end`, one with all three)
  - 1 healthy finding (different category, no priority letter prefix)
  - 1 finding with a `|` character in its title (pipe-escape edge case)
  - 2 recommendations: first with `code_snippet` + explicit `language="python"`, second without `code_snippet` (rationale only)
  - 3-row `best_practices_table` — at least one row contains `|` in a cell (pipe-escape edge case)

- [ ] **Step 7.1.2: Hand-craft `tests/golden/sample_file.expected.md`** matching the §7.1 spec format. Pin the COMPLETE expected content (every byte). Include:
  - Header: `# Audit: <relpath>` + `**Date:** ... **Run ID:** ... **Model:** ... **Lens:** ...` + tokens line + tools line + GitNexus context line
  - `<overall_assessment>` paragraph
  - `---`
  - `## Detailed Audit Findings`
  - `### A. <category-1> (High Priority)` with the high finding (Issue/Why/Fix/Confidence/Location)
  - `### B. <category-2> (Medium Priority)` with the medium finding
  - `### C. <category-3> (Low Priority)` with the low finding (note: the pipe-containing title is here; assert escaped as `\|`)
  - `### Healthy` with the healthy finding (no letter prefix)
  - `## Recommendations` with `#### Recommendation 1:` (with `python` code fence) and `#### Recommendation 2:` (no code fence)
  - `### Summary of Best Practices Applied` table with 3 rows; the row containing `|` shows it as `\|`
  - Every `FileMetadata` field (relpath, language, model_id, lens_name, prompt_tokens, completion_tokens, thinking_seconds, output_seconds, tools_used, compactions_used, graph_context_summary, run_id_short, date) appears at least once in the rendered output (round-trip completeness — assert separately).

- [ ] **Step 7.1.3: Failing tests** in `tests/golden/test_renderer.py`:
  - `test_render_byte_match_against_golden` — `Renderer(...).render_file(input, meta) == Path("sample_file.expected.md").read_text(encoding="utf-8", newline="\n")` byte-exact.
  - `test_render_includes_every_metadata_field` — round-trip completeness: each field of `FileMetadata` appears as a substring in the rendered output.
  - `test_render_escapes_pipes_in_table_cells` — assert `\|` substring in the rendered table.
  - `test_render_escapes_pipes_in_finding_title` — assert `\|` in the L42 finding title section.
  - `test_render_recommendation_language_falls_back_to_file_language` — when `recommendations[i].language` is None, fence uses `FileMetadata.language`.

- [ ] **Step 7.1.4: Implement `senex/render_models.py`** — pydantic v2 models (`FileMetadata`, `RunMetadata`, `FindingRecord`, `LocationRecord`, `Theme`, `ToolCallSummary`, `SkippedRecord`, `ErroredRecord`) with `extra="forbid"`.

- [ ] **Step 7.1.5: Implement `Renderer.render_file(response, file_metadata)`** in `senex/renderer.py`:
  - Header with all `FileMetadata` fields
  - Findings sorted: priority high → medium → low → healthy (omit empty); within priority, by `(file, location.line_start or 0, title)`. Categories assigned letter prefix A/B/C in priority order; healthy finding gets `### Healthy` with no letter prefix.
  - Each finding: `- **<title>**` + Issue/Why/Fix/Confidence/Location bullets
  - Pipe escaping: `cell.replace("|", "\\|")` applied to every cell in `best_practices_table` AND every finding title (since finding titles can leak into the combined report's top-findings table). Newline normalization: `s.replace("\r\n", "\n").replace("\r", "\n")` applied to every model-supplied string.
  - Recommendations numbered 1-N. `code_snippet` language tag from `recommendations[i].language` if present, else from `FileMetadata.language`.
  - `best_practices_table` rows escape pipes in every cell.
  - Apply `SecretRedactor.redact()` to the final string.

- [ ] **Step 7.1.6: Implement `Renderer.write_file_atomic(audit_dir, relpath, markdown)`** — delegates to `write_text_atomic` (after Task 7.7 lands; before then, stub raising `NotImplementedError`). Returns final `<file>.md` path.

- [ ] **Step 7.1.7: Run** the 5 tests → all green.

- [ ] **Step 7.1.8: Commit** `feat(M7): renderer with golden-file regression test`.

### Task 7.2: Findings partial writer (NDJSON streaming)

**Files:**
- Create: `senex/findings_partial.py`
- Create: `tests/unit/test_findings_partial.py`

- [ ] **Step 7.2.1: Failing tests**:
  - `test_append_writes_one_valid_json_line` — single `append()` produces exactly one line; `json.loads(line)` round-trips.
  - `test_each_line_validates_against_findings_index_schema` — every appended line satisfies the per-finding subset of `findings_index.schema.json`.
  - `test_finding_id_format` — every emitted `id` matches `^f-[a-f0-9]{12}$`.
  - `test_partial_write_crash_safety` — write 5 findings via `FindingsPartialWriter`; mock `os.fsync` to raise on the 5th; reopen the file and assert: 4 valid lines + an optionally truncated 5th (file ends mid-line). Reader skips the bad last line via `json.JSONDecodeError` handling.
  - `test_context_manager_closes_idempotently` — `with FindingsPartialWriter(d) as w: w.append(...)` then explicit `w.close()` → no error.

- [ ] **Step 7.2.2: Implement `FindingsPartialWriter`** — opens `findings.partial.jsonl` in append-binary mode (`"ab"`, no buffering across calls). Each `append()` performs: serialize → `write(line + b"\n")` → `flush()` → `os.fsync(fileno)`. `close()` flushes + closes; idempotent.

- [ ] **Step 7.2.3: Implement `compute_finding_id`** in same module: `"f-" + hashlib.sha256(f"{file}|{symbol or ''}|{line_start or ''}|{title}|{prompt_hash}".encode("utf-8")).hexdigest()[:12]`.

- [ ] **Step 7.2.4: Stability + sensitivity tests** for `compute_finding_id`:
  - Same inputs → same id (stability).
  - Different `prompt_hash` → different id (sensitivity to prompt change).
  - Different `title` → different id (sensitivity to title change).
  - Missing `symbol` (None) and missing `line_start` (None) — id still deterministic; ensures the `or ''` / `or ''` branch is covered.

- [ ] **Step 7.2.5: Run** tests → green.

- [ ] **Step 7.2.6: Commit** `feat(M7): streaming findings.partial.jsonl writer with stable ids`.

### Task 7.3: Findings aggregator (Phase 5)

**Files:**
- Create: `senex/findings_aggregator.py`
- Create: `tests/unit/test_findings_aggregator.py`

- [ ] **Step 7.3.1: Failing tests**:
  - `test_aggregate_dedup_by_id_last_wins` — write 3 findings with identical id (re-audit scenario); aggregator output contains exactly 1 finding with the LAST-written content.
  - `test_aggregate_sort_priority_then_file_then_line` — given findings with mixed priority/file/line, output is sorted high → medium → low → healthy, then alphabetical by file, then ascending by `location.line_start`.
  - `test_aggregate_validates_against_findings_index_schema` — output `findings.json` validates against `findings_index.schema.json`.
  - `test_aggregate_idempotent` — running `Aggregator.run()` twice on the same input produces byte-identical output.
  - `test_aggregate_skips_corrupt_last_line` — write 4 valid lines + 1 truncated/non-JSON line at the end; aggregator emits 4 findings + logs the skip; does NOT raise.
  - `test_aggregate_missing_partial_returns_empty_findings_json` — no `findings.partial.jsonl` on disk → aggregator writes `findings.json` with empty `findings: []`, `totals: {high:0,medium:0,low:0,healthy:0,files:0}`; does NOT raise `AggregatorInputMissing` (that's reserved for the catastrophic case where the audit dir itself is missing).
  - `test_aggregate_atomic_write` — mock `os.replace` to raise; assert no partial `findings.json` exists; tmp file may exist.

- [ ] **Step 7.3.2: Implement `Aggregator.run(audit_dir, themes, run_metadata) -> Path`**:
  - Read `findings.partial.jsonl` line-by-line; on `json.JSONDecodeError` for the LAST line only, log + skip; for non-last lines, raise `FindingsValidationFailed` (corruption mid-stream is a real bug).
  - Validate each line against `FindingRecord`; on validation failure, emit `FileError` event and skip the finding (do NOT raise — one bad finding shouldn't kill the aggregate).
  - Dedupe by `id` using a dict (insertion order, last-wins).
  - Sort: `(priority_rank, file, location.line_start or 0)` where `priority_rank = {"high":0,"medium":1,"low":2,"healthy":3}`.
  - Compute `totals` from sorted list.
  - Build `FindingsIndex` model: `{schema_version: 1, run: run_metadata, totals, themes: themes or [], findings}`.
  - Validate against `findings_index.schema.json`.
  - Write via `write_text_atomic(audit_dir / "findings.json", model.model_dump_json(indent=2))`.
  - Return final path.

- [ ] **Step 7.3.3: Run** tests → green.

- [ ] **Step 7.3.4: Commit** `feat(M7): findings.json aggregator with dedupe + stable sort`.

### Task 7.4: Cross-cutting pass

**Files:**
- Create: `senex/cross_cutting.py`
- Create: `tests/unit/test_cross_cutting.py`

- [ ] **Step 7.4.1: Failing tests**:
  - `test_crosscut_compresses_findings_to_tuples` — given 10 findings, the LMS payload contains tuples of `(file, category, priority, title)` only (no `issue`/`why`/`fix`).
  - `test_crosscut_caps_input_per_priority` — given 60 high + 200 medium + 200 low findings, the LMS payload contains exactly 50 + 100 + 100 = 250 tuples (per §ARCH-12 defaults from `CrosscutCfg`).
  - `test_crosscut_returns_themes_with_stable_ids` — themes have `id` matching `^t-[a-f0-9]{12}$` per §5.4.1; same `(title, run_id)` pair → same id.
  - `test_crosscut_returns_none_on_lms_http_failure` — mocked client raises HTTP error; `CrossCutter.run()` returns `None`; emits `CrosscutComplete` event with `themes=None` (or `CrosscutFailed` event — pick one and document).
  - `test_crosscut_returns_none_on_schema_mismatch` — mocked client returns invalid JSON; after one retry, `CrossCutter.run()` returns `None`.
  - `test_crosscut_passes_lens_tools_disabled` — payload sent to `client.chat()` has `tools=None` (the crosscut call uses NO tools per the same logic as compaction).
  - `test_crosscut_hierarchical_signature_extension_point` — `_chunk_for_crosscut(findings, cluster_map=None)` returns the full list when `cluster_map=None` (degenerate single-level); when `cluster_map` is provided, returns clustered groups (skeleton — full hierarchy in v2).

- [ ] **Step 7.4.2: Implement `CrossCutter.run(client, lens, findings, cfg)`**:
  - Cap input via `_top_n_per_priority(findings, cfg.top_n_high, cfg.top_n_medium, cfg.top_n_low)`.
  - Compress to tuples `(file, category, priority, title)`.
  - Build user prompt from `prompts/cross_cutting.md` template + tuples (renderer handles JSON-encoding).
  - Call `client.chat(messages, schema=crosscut_response_schema, tools=None)` with one retry on validation failure.
  - On any failure (HTTP, timeout, schema mismatch after retry): emit event, log, return `None`.
  - On success: parse response; compute theme ids via `compute_theme_id(title, run_id)`; return `list[Theme]`.

- [ ] **Step 7.4.3: Implement `compute_theme_id`** in same module: `"t-" + hashlib.sha256(f"{title}|{run_id}".encode("utf-8")).hexdigest()[:12]`.

- [ ] **Step 7.4.4: Run** tests → green.

- [ ] **Step 7.4.5: Commit** `feat(M7): cross-cutting themes pass with input cap`.

### Task 7.5: Combined report renderer

**Files:**
- Edit: `senex/renderer.py`
- Create: `tests/unit/test_render_combined.py`

- [ ] **Step 7.5.1: Failing tests**:
  - `test_render_combined_includes_every_run_metadata_field` — every `RunMetadata` field appears in output.
  - `test_render_combined_priority_rollup_correct` — given totals `{high:8, medium:34, low:56, healthy:17}`, rollup section contains exactly those numbers.
  - `test_render_combined_themes_section_when_themes_present` — themes section lists each theme with title + description.
  - `test_render_combined_themes_unavailable_gap_when_themes_none` — when `themes=None`, section contains the literal `[cross-cutting themes unavailable]`.
  - `test_render_combined_top_findings_table_sorted` — top-findings table rows sorted by priority, then file, then line.
  - `test_render_combined_top_findings_hyperlink_to_per_file_report` — each row contains `[report](<relpath>.md)` markdown link.
  - `test_render_combined_files_with_no_findings_section` — files in `run_metadata.files_with_no_findings` list appear under "Files With No Findings".
  - `test_render_combined_skipped_section_lists_reason` — each `SkippedRecord` appears as `<path>: <reason>`.
  - `test_render_combined_errored_section_lists_kind` — each `ErroredRecord` appears with kind annotation.
  - `test_render_combined_pipe_escapes_finding_titles_in_table` — finding with `|` in title shows `\|` in the top-findings table.

- [ ] **Step 7.5.2: Implement `Renderer.render_combined(run_metadata, findings, themes, skipped, errored)`** matching §7.2 structure:
  - `# senex Audit: <repo> <date>`
  - Run header line: files audited / skipped / errored, duration, model, prompt_hash, config_hash
  - `## Priority Rollup` — HIGH/MEDIUM/LOW/HEALTHY counts
  - `## Cross-Cutting Themes` — numbered list OR `[cross-cutting themes unavailable]` gap note
  - `## Top Findings (sorted by priority)` — markdown table (with pipe escapes)
  - `## Files With No Findings`
  - `## Skipped Files`
  - `## Errored Files`
  - `## Run Metadata` — model fingerprint, GitNexus index hash, totals
  - Apply `SecretRedactor.redact()` to final string.

- [ ] **Step 7.5.3: Run** tests → green.

- [ ] **Step 7.5.4: Commit** `feat(M7): combined report renderer`.

### Task 7.6: Handoff renderer (references-only per §POL-2)

**Files:**
- Create: `senex/handoff.py`
- Create: `tests/unit/test_handoff.py`

- [ ] **Step 7.6.1: Failing tests**:
  - `test_handoff_lists_audit_dir_paths` — output contains `Audit dir:`, `Findings index:`, `Per-file reports:` lines pointing at the actual audit dir.
  - `test_handoff_top_3_findings_summary_format` — top-3 list is exactly `<N>. [<PRIORITY>] <file>:<line> — <title>` (per §7.4 example).
  - `test_handoff_does_not_embed_finding_issue_text` — assert `top_findings[0].issue` substring is NOT present in handoff output.
  - `test_handoff_does_not_embed_finding_why_text` — same for `why`.
  - `test_handoff_does_not_embed_finding_fix_text` — same for `fix`.
  - `test_handoff_self_check_raises_on_policy_violation` — inject a finding whose `title` happens to equal its `issue` text; the writer's self-check still raises `HandoffPolicyViolation` if the *body text* is detected (use a unique sentinel string in `issue`).
  - `test_handoff_atomic_write` — mock rename to raise; original `claude-handoff.md` (if present) unchanged.
  - `test_handoff_top_3_uses_priority_then_line_order` — given mixed priority/line findings, top-3 are correctly ordered.

- [ ] **Step 7.6.2: Implement `write_handoff(audit_dir, run_metadata, top_findings)`** per spec §7.4:
  - Loads `prompts/claude_handoff.md` template (snapshot-resolved by caller).
  - Substitutes `{repo}`, `{date}`, `{run_id_short}`, `{audit_dir}`, `{findings_index}`, `{per_file_reports}`, `{top_findings_block}` placeholders.
  - `top_findings_block` = numbered list of up to 3 findings, format: `[<PRIORITY>]   <file>:<line> — <title>` (priority padding to 6 chars for visual alignment).
  - Self-check: for each finding in `top_findings`, assert `f.issue` and `f.why` and `f.fix` substrings are NOT in the rendered output. On detection, raise `HandoffPolicyViolation`.
  - Apply `SecretRedactor.redact()` to final string.
  - Write via `write_text_atomic(audit_dir / "claude-handoff.md", text)`.
  - Return final path.

- [ ] **Step 7.6.3: Run** tests → green.

- [ ] **Step 7.6.4: Commit** `feat(M7): claude-handoff.md writer (references-only per POL-2)`.

### Task 7.7: Atomic write helper

**Files:**
- Create: `senex/atomic_io.py`
- Create: `tests/unit/test_atomic_io.py`

- [ ] **Step 7.7.1: Failing tests**:
  - `test_write_text_atomic_creates_target` — `write_text_atomic(target, "hello")` produces a file with content `"hello"` and UTF-8 encoding.
  - `test_write_text_atomic_uses_lf_line_endings` — input `"a\r\nb\r\n"` is written as `"a\nb\n"` (or: caller is responsible for normalization, we just assert no `\r` injected by us — pin the choice).
  - `test_write_text_atomic_no_partial_on_rename_failure` — mock `os.replace` to raise `OSError`; original target file (pre-existing) is unchanged; tmp file may exist for inspection but `target` is intact.
  - `test_write_text_atomic_no_target_on_first_write_rename_failure` — mock `os.replace` to raise; target did not pre-exist → target still does not exist after the raise.
  - `test_write_text_atomic_fsyncs_before_rename` — patch `os.fsync` and `os.replace`; assert `fsync` is called BEFORE `replace` (call-order check).
  - `test_write_text_atomic_raises_disk_fatal_on_enospc` — mock `Path.write_text` to raise `OSError(errno=ENOSPC)`; assert `DiskFatalError` is raised (run-killing per §ARCH-13).
  - `test_write_text_atomic_returns_target_path` — return value is the input `target`, not the tmp.

- [ ] **Step 7.7.2: Implement `write_text_atomic(target, text, encoding="utf-8") -> Path`**:
  - `tmp = target.with_suffix(target.suffix + ".tmp")`
  - `tmp.write_bytes(text.encode(encoding))` — bytes-level so we control line endings exactly
  - Open `tmp` with `os.open(tmp, O_RDONLY)`; `os.fsync(fd)`; close
  - `os.replace(tmp, target)`
  - On `OSError` with `errno in {ENOSPC, EROFS, EDQUOT}`: raise `DiskFatalError(...) from e`
  - Return `target`.

- [ ] **Step 7.7.3: Update `Renderer.write_file_atomic`** to delegate to `write_text_atomic`. Update `Aggregator`, `write_handoff`, error-artifact writers to use it.

- [ ] **Step 7.7.4: Crash-injection integration test** — `test_renderer_crash_mid_write_leaves_target_intact`: pre-create `<file>.md` with sentinel content; mock `os.replace` to raise; call `Renderer.write_file_atomic`; assert `<file>.md` still has sentinel content; tmp may exist.

- [ ] **Step 7.7.5: Run** tests → green.

- [ ] **Step 7.7.6: Commit** `feat(M7): atomic write helper (tmp+fsync+rename) with disk-fatal escalation`.

### Task 7.8: Error/skipped/raw artifact writers

**Files:**
- Create: `senex/error_artifacts.py`
- Create: `tests/unit/test_error_artifacts.py`

- [ ] **Step 7.8.1: Failing tests** (one per writer):
  - `test_write_error_artifact_shape` — output file is `<audit_dir>/<relpath>.ERROR.md`; content includes `kind`, `error_message`, traceback section per §8.2.
  - `test_write_error_artifact_uses_atomic_write` — mock `os.replace` to raise; pre-existing target unchanged.
  - `test_write_error_artifact_applies_redactor` — error message containing `sk-FAKE-secret` is redacted in output.
  - `test_write_skipped_artifact_shape` — output file is `<audit_dir>/<relpath>.SKIPPED.md`; content includes `reason`.
  - `test_write_skipped_artifact_atomic` — same as above for atomic.
  - `test_write_raw_response_shape` — output file is `<audit_dir>/<relpath>.RAW.json`; content is the raw JSON string verbatim (no parse-rewrite — the whole point is to preserve the model's output for debugging).
  - `test_write_raw_response_redacts_secrets` — raw JSON containing `sk-FAKE` is redacted.
  - `test_write_render_error_artifact_shape` — output file is `<audit_dir>/<relpath>.RENDER_ERROR.md` (per §ARCH-13); content includes traceback + `response_dump` (the structured response that triggered the renderer crash).
  - `test_write_render_error_artifact_does_not_kill_run` — wrapper test asserting the writer returns normally (no re-raise).

- [ ] **Step 7.8.2: Implement the four writers** in `senex/error_artifacts.py`. Each:
  - Accepts `audit_dir: Path` + `relpath: str` + payload args
  - Builds markdown/JSON string per §8.2 shape
  - Applies `SecretRedactor.redact()` to the body (passed in via `Renderer.__init__` or module-level singleton — pin the choice in implementation)
  - Calls `write_text_atomic(audit_dir / f"{relpath}.<EXT>", body)`
  - Returns final path

- [ ] **Step 7.8.3: Run** all 9 tests → green.

- [ ] **Step 7.8.4: Commit** `feat(M7): error/skipped/raw/render-error artifact writers`.

## Acceptance criteria

- `pytest tests/golden/ tests/unit/test_findings_partial.py tests/unit/test_findings_aggregator.py tests/unit/test_cross_cutting.py tests/unit/test_handoff.py tests/unit/test_atomic_io.py tests/unit/test_error_artifacts.py tests/unit/test_render_combined.py -v` is 100% green (8 test files).
- `Renderer().render_file(input.json, metadata) == expected.md` byte-for-byte (golden test).
- Every `FileMetadata` field appears in the rendered per-file report (round-trip completeness assertion).
- `Aggregator.run()` on a partial log with 3 duplicate `finding_id`s produces a `findings.json` containing exactly 1 finding (last-wins).
- Sort order test: high before medium before low before healthy; within priority, alphabetical by file; within file, ascending by `line_start`.
- `Aggregator.run()` is byte-idempotent: two runs on the same input produce identical `findings.json` bytes.
- `Aggregator.run()` skips a corrupt LAST line in `findings.partial.jsonl` without raising (logs the skip).
- `CrossCutter.run()` with mocked LMS HTTP failure returns `None` and the run is not raised out of.
- `CrossCutter.run()` caps input to 50 high + 100 medium + 100 low (per `CrosscutCfg` defaults).
- `claude-handoff.md` test asserts no `issue`/`why`/`fix` text from any finding is embedded (only references + top-3 title summary). `HandoffPolicyViolation` raised on injected violation.
- A crash-injection test (mock `os.replace` to raise after tmp write) leaves the original `<file>.md` intact (or no file for first-write).
- `write_text_atomic` raises `DiskFatalError` on ENOSPC/EROFS/EDQUOT — verified by mocked `OSError`.
- Each of `<file>.ERROR.md`, `<file>.SKIPPED.md`, `<file>.RAW.json`, `<file>.RENDER_ERROR.md` writers has a test asserting (a) correct artifact shape per §8.2, (b) atomic-write usage, (c) `SecretRedactor` applied to body.
- `compute_finding_id` and `compute_theme_id` produce ids matching their respective patterns (`^f-[a-f0-9]{12}$`, `^t-[a-f0-9]{12}$`); stability + sensitivity tests pass.
- `mypy --strict senex/renderer.py senex/findings_partial.py senex/findings_aggregator.py senex/cross_cutting.py senex/handoff.py senex/atomic_io.py senex/error_artifacts.py senex/render_models.py` is clean.
- `ruff check senex/ tests/` is clean.
- Coverage on `renderer.py`, `findings_aggregator.py` ≥ 85% (per conventions §6 enforced thresholds); other M7 modules ≥ 60%.
