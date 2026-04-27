# Milestone 6: Compaction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M6 delivers context compaction: when the per-file message history grows past a configured fraction of the model's context window, senex calls the model itself to summarize the tool history into a compressed `[COMPACTED]` system message, then continues the audit. This is the safety net that prevents long tool sessions from hitting hard context limits.

**Architectural intent:** Compaction is a *meta* LLM call — same client, different prompt and schema, no tools. The trigger logic (M3 already wired the hook in Task 3.6.3) decides *when*; the executor here decides *how*. Keeping these as separate modules means the trigger can be tested in isolation and the executor can be reused outside the tool loop if needed.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration, M5 Tool Framework
- **Required modules from prior work:**
  - `senex/lmstudio_client.py` — used by Compactor to call the model with the compaction prompt (M3)
  - `senex/prompts/compaction.md` — compaction instructions (M2 Task 2.5)
  - `senex/schema/compaction_response.schema.json` — output schema (M1 Task 1.8)
  - `senex/events.py` — `CompactionTriggered`, `CompactionComplete`, `CompactionError` (M1)
  - `senex/config.py` — `CompactionCfg.trigger_pct`, `CompactionCfg.preserve_recent_turns`, `CompactionCfg.max_compactions_per_file` (M1)
  - `senex/tools/loop.py` — `ToolLoop.run()` accepts a `compaction_cb` (M5 Task 5.8)
- **Required tools/state:**
  - LM Studio running for any `@live` test (deferred to M10 gate 13e); M6 unit tests use mocked client

## Deliverable

This milestone creates the following files:

- `senex/compaction.py` — `Compactor` (executor) + `should_trigger()` helper + per-file `compactions_so_far` counter
- `tests/unit/test_compaction.py` — executor + trigger tests
- Wired into `senex/tools/loop.py`: `ToolLoop` calls `compactor.run()` when `compactor.should_trigger()` returns True

## Downstream consumers

- **M8** FileAuditPhase wraps the per-file LLM call; on `CompactionLoopExceeded` it writes ERROR.md with reason="compaction_loop_exceeded".
- **M10** Live gate 13e verifies compaction fires end-to-end (synthetic-bloat env var → CompactionTriggered → CompactionComplete → audit completes).

## Spec sections referenced

- §5.5.1 Context Compaction — full specification: trigger, prompt, schema, message rewrite, preserve_recent_turns, loop counter
- §5.5.1 message rewrite rule — replace the slice with a single system message: `[COMPACTED]\n<summary>...`
- §5.5.1 preserve_recent_turns — last N messages stay verbatim
- §5.5.1 max_compactions_per_file — bound on compaction loop; on N+1 trigger, raise `CompactionLoopExceeded` (file aborts)

## Key contracts

- **`Compactor.__init__(client, lens, config)`**
- **`async Compactor.run(messages: list) -> tuple[list, CompactionResult]`** — calls client with compaction prompt + slice; validates response against schema; rewrites messages; emits events; returns new history + result.
- **`Compactor.should_trigger(messages, client_or_ctx_window) -> bool`** — `count_tokens(messages) >= trigger_pct * context_window`.
- **Per-file counter `compactions_so_far`** — bound by `max_compactions_per_file`; on `N+1`, raise `CompactionLoopExceeded`.

## Watch-outs

- **The compaction call uses NO tools.** It's a single non-streaming (or short-streaming) chat with the compaction prompt and the compaction schema. Passing `tools=[...]` here is wrong — the model would try to call tools to summarize itself.
- **Preserve recent turns intact.** `preserve_recent_turns=2` means the last 2 messages stay byte-for-byte; only the *earlier* tool history is replaced by the `[COMPACTED]` block. Off-by-one here loses context the model just used.
- **Schema is `compaction_response.schema.json`** with `evidence_summary`, `key_findings_so_far`, `unanswered_questions`. Validate the model's output against it; on validation failure, emit `CompactionError` and let the caller decide.
- **`CompactionLoopExceeded` aborts the file, not the run.** Per spec §8.2 per-file recovery, the file gets an ERROR.md and the run continues with the next file.
- **`compactions_so_far` counter is per-file.** Reset between files. Track on the `ToolLoop` instance or pass it through messages context.
- **Preserve `[COMPACTED]` marker in the history.** Subsequent compactions need to recognize already-compacted regions to avoid double-compacting.

## Patterns to follow

- §5.5.1 is the single source of truth for compaction behavior. Implement the executor as a direct translation of that section.
- **TDD with synthetic bloat:** Test 6.4.2's "artificially-bloated tool result fixtures" mean: write a fixture file >> trigger_pct * ctx_window, plug it as a tool result, assert the loop triggers compaction.

## Tasks

### Task 6.1: Compaction prompt + schema

(Already shipped in M2 task 2.5 + M1 task 1.8.) See [m1-foundation.md Task 1.8](m1-foundation.md#task-18-schema-files-5-json-schemas) and [m2-walker-graph.md Task 2.5](m2-walker-graph.md#task-25-crosscut--handoff--compaction-prompts).

### Task 6.2: Compaction executor

**Files:**
- Create: `senex/compaction.py`
- Create: `tests/unit/test_compaction.py`

- [ ] **Step 6.2.1: Failing tests**:
  - `Compactor.run(messages, client)` returns CompactionResult conformant to schema
  - The result message replaces the slice in history with a single system message containing `[COMPACTED]\n<summary>...`
  - `preserve_recent_turns=2` keeps the last 2 messages intact
  - Compaction failure (LMS error) raises `CompactionFailed`

- [ ] **Step 6.2.2: Implement `Compactor`** with `async run()`. Calls client with compaction prompt + slice; validates response; rewrites messages.

- [ ] **Step 6.2.3: Emit events** `CompactionTriggered`, `CompactionComplete`, `CompactionError`.

- [ ] **Step 6.2.4: Run** tests → green.

- [ ] **Step 6.2.5: Commit** `feat(M6): context compaction executor`.

### Task 6.3: Compaction trigger logic

- [ ] **Step 6.3.1: Implement `should_trigger(messages, client, config) -> bool`** — `count_tokens(messages) >= trigger_pct * client.context_window`.

- [ ] **Step 6.3.2: Implement loop counter `compactions_so_far`** — bound by `max_compactions_per_file`; on N+1 trigger, raise `CompactionLoopExceeded` (the file aborts).

- [ ] **Step 6.3.3: Tests + commit** `feat(M6): compaction trigger + loop counter`.

### Task 6.4: Wire compaction into tool loop

- [ ] **Step 6.4.1: Update `ToolLoop`** to call `compactor.run()` when `compactor.should_trigger()` returns True.

- [ ] **Step 6.4.2: Test** end-to-end: artificially-bloated tool result fixtures cross trigger_pct → compaction fires → audit completes.

- [ ] **Step 6.4.3: Commit** `feat(M6): tool loop ↔ compaction integration`.

## Acceptance criteria

- `pytest tests/unit/test_compaction.py -v` is 100% green.
- A test with `preserve_recent_turns=2` asserts the last 2 messages are byte-equal before and after compaction.
- A test asserts compaction reduces token count (`count_tokens(after) < count_tokens(before)`).
- A test asserts `compactions_so_far == max_compactions_per_file + 1` raises `CompactionLoopExceeded`.
- An end-to-end tool-loop test with synthetic-bloat fixtures: `should_trigger()` returns True, `Compactor.run()` is called, audit response is still valid against `audit_response.schema.json`.
- Events `CompactionTriggered` and `CompactionComplete` are emitted in order on a successful compaction; `CompactionError` is emitted on LMS failure.
- The `[COMPACTED]` marker is present in the rewritten history at the position of the replaced slice.
