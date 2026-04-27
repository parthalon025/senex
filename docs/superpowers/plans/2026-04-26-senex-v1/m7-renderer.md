# Milestone 7: Renderer + Aggregator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M7 turns structured `AuditResponse` JSON into the human-facing artifacts: per-file Markdown reports, the streaming `findings.partial.jsonl` log, the deduped+sorted `findings.json`, the cross-cutting themes pass, the `combined.md` summary, the `claude-handoff.md`, and the failure-artifact writers (ERROR.md, SKIPPED.md, RAW.json). Every file the operator (or downstream Claude Code) actually reads comes from this milestone.

**Architectural intent:** Layer 4 is "rendering and aggregation" — it depends on Layers 0-3 (config, schemas, client, tools) but knows nothing about phases or the auditor coroutine. The renderer is byte-deterministic for golden tests; aggregation is content-deterministic (stable IDs, stable sort). Per-file writes are atomic so a crash mid-write never leaves a partial `<file>.md` on disk.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration, M5 Tool Framework, M6 Compaction
- **Required modules from prior work:**
  - `senex/schema/audit_response.schema.json` — renderer input shape (M1)
  - `senex/schema/crosscut_response.schema.json` — crosscut output shape (M1)
  - `senex/schema/findings_index.schema.json` — `findings.json` shape (M1)
  - `senex/lmstudio_client.py` — used by `CrossCutter` for the crosscut LLM call (M3)
  - `senex/prompts/cross_cutting.md` + `claude_handoff.md` (M2 Task 2.5)
- **Required tools/state:**
  - Hand-crafted `tests/golden/sample_file.input.json` and matching `sample_file.expected.md` for byte-match regression test
  - `tests/recorded/` LMS replay infrastructure from M3 (used for crosscut tests)

## Deliverable

This milestone creates the following files:

- `senex/renderer.py` — `Renderer.render(response, file_metadata)` (per-file MD) + `render_combined(...)` (combined.md)
- `senex/findings_partial.py` — `FindingsPartialWriter` (NDJSON streaming append)
- `senex/findings_aggregator.py` — `Aggregator.run(audit_dir, themes) -> Path` (dedupe + sort + atomic write)
- `senex/cross_cutting.py` — `CrossCutter.run(client, lens) -> CrosscutResponse | None` (LMS-backed; tolerant)
- `senex/handoff.py` — `HandoffWriter.write(audit_dir, findings, themes)` → `claude-handoff.md`
- Atomic-write helper used across renderers (tmp+fsync+rename)
- Failure-artifact writers (ERROR.md, SKIPPED.md, RAW.json) for spec §8.2 per-file recovery
- `tests/golden/test_renderer.py`, `sample_file.input.json`, `sample_file.expected.md`
- Unit tests for partial writer, aggregator, crosscut, handoff, atomic writes, error/skipped/raw writers

## Downstream consumers

- **M8** FileAuditPhase calls `Renderer.render()` per file (atomic write to `<file>.md.tmp` + rename) and `FindingsPartialWriter.append()` per finding.
- **M8** AggregatePhase calls `Aggregator.run()` then `HandoffWriter.write()` then `Renderer.render_combined()`.
- **M8** CrosscutPhase calls `CrossCutter.run()`; passes themes to AggregatePhase.
- **M8** FileAuditPhase calls the failure-artifact writers on per-file errors.
- **M10** Live gate 13a verifies `combined.md`, `findings.json`, `claude-handoff.md`, and at least one `<file>.md` exist after a successful audit.

## Spec sections referenced

- §7.1 Per-File Report — header format, A/B/C/Healthy categorization, recommendations with code blocks, best practices table
- §7.2 Combined Report (`combined.md`) — run header, priority rollup, themes, top findings table, files-with-no-findings, skipped, errored, run metadata
- §7.3 `findings.json` — schema, run + totals + themes + findings array
- §7.4 `claude-handoff.md` — handoff structure (top-3 findings summary, references, no full content embed)
- §8.2 Per-file Recovery — ERROR.md, SKIPPED.md, RAW.json artifact shapes
- §POL — pipe-escape rules in markdown tables (best_practices_table, top findings table)
- §POL-2 — handoff does not embed full findings content (references only)
- §ARCH-12 — crosscut input cap (50 high + 100 medium + 100 low)

## Key contracts

- **`Renderer.render(response: AuditResponse, file_metadata: dict) -> str`** — produces per-file markdown.
- **`Renderer.render_combined(run_metadata, aggregated_findings, themes) -> str`** — produces `combined.md`.
- **`FindingsPartialWriter.append(finding: dict)`** — single `write()` + `fsync()` per line; atomic per-line.
- **`Aggregator.run(audit_dir, themes) -> Path`** — reads `findings.partial.jsonl`, dedupes by `finding_id = sha256(file + symbol + line_start + title + prompt_hash)[:12]`, sorts (priority high→low, then file alphabetical), writes `findings.json` atomically.
- **`CrossCutter.run(client, lens, compressed_findings) -> CrosscutResponse | None`** — caps input per ARCH-12, calls LMS with crosscut prompt+schema, returns parsed response or None on failure.
- **`HandoffWriter.write(audit_dir, findings, themes)`** — writes `claude-handoff.md` per §7.4.
- **`write_file_atomically(path, content)`** — `<path>.tmp` + fsync + rename.
- **Failure artifact writers**: `write_error_md(path, exception)`, `write_skipped_md(path, reason)`, `write_raw_json(path, raw_lms_response)`.

## Watch-outs

- **Byte-match test is brittle by design.** A single newline drift fails it. That's the point — the renderer's output is referenced from the `claude-handoff.md` and embedded in CI artifacts; drift here means downstream consumers diff cleanly. When you intentionally change the format, regenerate the golden file in the same commit.
- **`finding_id` MUST be stable.** `sha256(file + symbol + line_start + title + prompt_hash)[:12]`. A re-audit with the same inputs produces the same id, enabling last-wins dedupe. Including `prompt_hash` means a prompt change creates new ids (intentional — different lens = different findings).
- **Pipe-escape in markdown tables.** Any finding title or recommendation snippet that contains `|` must be escaped as `\|` in the best_practices_table and top findings table. Otherwise the table breaks (§POL).
- **CrossCutter is tolerant.** On LMS failure, return `None` and let the run continue; combined.md notes "[crosscut unavailable]". Do NOT raise.
- **Atomic write is the *only* write pattern.** Every `<file>.md`, `findings.json`, `combined.md`, `claude-handoff.md` writes through `write_file_atomically()`. A crash mid-write must leave the previous version intact (or no file at all for first-write).
- **Crosscut input cap (ARCH-12):** 50 high + 100 medium + 100 low. Exceeding this is a silent over-cost. Truncate, don't error.
- **Handoff is reference-only.** `claude-handoff.md` summarizes top-3 findings and points at the per-file MD; it does NOT embed the full findings content (§POL-2). Otherwise it duplicates `combined.md`.

## Patterns to follow

- **Golden-file regression** for the renderer: hand-craft input + expected output once; subsequent edits regenerate the expected file. This is the strongest guarantee against silent format drift.
- **NDJSON for streaming**: one JSON object per line, each line schema-valid in isolation. Recovery from process death = "drop the last partial line, append new lines."
- **Atomic helper used everywhere**: implement `write_file_atomically()` once in M7 and have every renderer call it.

## Tasks

### Task 7.1: Renderer (structured → markdown)

**Files:**
- Create: `senex/renderer.py`
- Create: `tests/golden/test_renderer.py`
- Create: `tests/golden/sample_file.expected.md`

- [ ] **Step 7.1.1: Hand-craft `tests/golden/sample_file.input.json`** as a fully-populated `AuditResponse` instance (overall_assessment, 3 findings of varying priority, 2 recommendations with code_snippet, 3-row best_practices_table).

- [ ] **Step 7.1.2: Hand-craft `tests/golden/sample_file.expected.md`** matching the §7.1 spec format (header with all metadata, A/B/C/Healthy categories, recommendations, summary table).

- [ ] **Step 7.1.3: Failing test** `test_render_byte_match` — `Renderer.render(input_json, metadata) == expected_md`.

- [ ] **Step 7.1.4: Implement `Renderer.render(response, file_metadata)`**:
  - Header with Date, Run ID, Model, Lens, Tokens, Latency, Tools used, Compactions, GitNexus context
  - Group findings by priority (high → A, medium → B, low → C); healthy → Healthy section
  - Each finding: bold title + Issue/Why/Fix/Confidence/Location
  - Recommendations numbered, with code blocks if present (language from `recommendations[].language` or file's language)
  - Best practices table (escape pipes per §POL)

- [ ] **Step 7.1.5: Run** test → byte-match green.

- [ ] **Step 7.1.6: Commit** `feat(M7): renderer with golden-file regression test`.

### Task 7.2: Findings partial writer (NDJSON streaming)

**Files:**
- Create: `senex/findings_partial.py`

- [ ] **Step 7.2.1: Failing tests**:
  - `append(finding)` writes one valid JSON line
  - Each line schema-validates via `findings_index.schema.json`
  - Concurrent process death mid-write → next line is OK (atomic write per line via `write_text(... + "\n")` with single `write` syscall on flushed file)

- [ ] **Step 7.2.2: Implement** `FindingsPartialWriter` — opens `findings.partial.jsonl` in append mode; per-line atomic via single `write()` + `fsync()`.

- [ ] **Step 7.2.3: Commit** `feat(M7): streaming findings.partial.jsonl writer`.

### Task 7.3: Findings aggregator (Phase 5)

**Files:**
- Create: `senex/findings_aggregator.py`

- [ ] **Step 7.3.1: Failing test** `test_aggregate_dedup_sort` — given multiple per-file findings with stable IDs, aggregator produces single `findings.json` with sorted (priority, file) ordering.

- [ ] **Step 7.3.2: Implement `Aggregator.run(audit_dir, themes) -> Path`**:
  - Reads `findings.partial.jsonl`
  - Computes finding_id = sha256(file + symbol + line_start + title + prompt_hash)[:12]
  - Dedupe by id (last-wins for re-audits)
  - Sort: high > medium > low > healthy, then alphabetical by file
  - Writes `findings.json` with run metadata + totals + themes + findings array (atomic tmp+rename)

- [ ] **Step 7.3.3: Commit** `feat(M7): findings.json aggregator`.

### Task 7.4: Cross-cutting pass

**Files:**
- Create: `senex/cross_cutting.py`

- [ ] **Step 7.4.1: Failing test**:
  - Given a list of compressed findings (file, category, priority, title), `CrossCutter.run(client, lens)` returns a `CrosscutResponse` with themes
  - Cap to top-N per priority (50 high + 100 medium + 100 low) per spec ARCH-12
  - On LMS failure, returns `None` (run continues; combined report notes gap)

- [ ] **Step 7.4.2: Implement `CrossCutter`** — compresses findings, calls client with crosscut prompt + schema, returns parsed response.

- [ ] **Step 7.4.3: Commit** `feat(M7): cross-cutting themes pass`.

### Task 7.5: Combined report renderer

- [ ] **Step 7.5.1: Failing test** — given run metadata + aggregated findings + themes, render `combined.md` matching spec §7.2 structure.

- [ ] **Step 7.5.2: Implement `Renderer.render_combined(...)`** — run header, priority rollup, themes section, top findings table with links, files-with-no-findings, skipped, errored, run metadata.

- [ ] **Step 7.5.3: Commit** `feat(M7): combined report renderer`.

### Task 7.6: Handoff renderer

**Files:**
- Create: `senex/handoff.py`

- [ ] **Step 7.6.1: Implement `HandoffWriter.write(audit_dir, findings, themes)`** — produces `claude-handoff.md` per spec §7.4. References paths, summarizes top-3 findings (no full content embed per §POL-2).

- [ ] **Step 7.6.2: Test** + commit `feat(M7): claude-handoff.md writer`.

### Task 7.7: Atomic per-file write order

- [ ] **Step 7.7.1: Implement `write_file_atomically()` helper** — write to `<file>.md.tmp` + fsync + rename.

- [ ] **Step 7.7.2: Update Renderer** to use atomic write.

- [ ] **Step 7.7.3: Test** that crash mid-write leaves no partial `<file>.md`.

- [ ] **Step 7.7.4: Commit** `feat(M7): atomic per-file write`.

### Task 7.8: ERROR.md / SKIPPED.md / RAW.json writers

- [ ] **Step 7.8.1: Implement helper writers** for the four failure-artifact types per spec §8.2.

- [ ] **Step 7.8.2: Tests + commit** `feat(M7): error/skipped/raw artifact writers`.

## Acceptance criteria

- `pytest tests/golden/ tests/unit/test_findings_partial.py tests/unit/test_findings_aggregator.py tests/unit/test_cross_cutting.py tests/unit/test_handoff.py -v` is 100% green.
- `Renderer.render(input.json, metadata) == expected.md` byte-for-byte.
- `Aggregator.run()` on a partial log with 3 duplicate `finding_id`s produces a `findings.json` containing exactly 1 finding (last-wins).
- Sort order test: high-priority findings appear before medium; medium before low; within priority, files are alphabetical.
- `CrossCutter.run()` with a mocked LMS failure returns `None` and the run is not raised out of.
- `claude-handoff.md` test asserts no full finding content is embedded (only references + top-3 summary).
- A crash-injection test (raise after tmp write, before rename) leaves the original `<file>.md` intact (or no file for first-write).
- Each of ERROR.md / SKIPPED.md / RAW.json writers has a test asserting the correct artifact shape per §8.2.
