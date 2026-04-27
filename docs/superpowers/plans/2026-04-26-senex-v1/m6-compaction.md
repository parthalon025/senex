# Milestone 6: Compaction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M6 delivers context compaction: when the per-file message history grows past a configured fraction of the model's context window, senex calls the model itself to summarize the tool history into a compressed `[COMPACTED]` system message, then continues the audit. This is the safety net that prevents long tool sessions from hitting hard context limits.

**Architectural intent:** Compaction is a *meta* LLM call — same client, different prompt and schema, **no tools**. The trigger logic decides *when* (token-count check after each tool result append); the executor decides *how* (call client, validate, rewrite history). Keeping these as separate symbols inside `senex/compaction.py` means the trigger is a pure function (testable in isolation with crafted message lists) and the executor is reusable outside the tool loop if a future phase needs it.

**Compaction is small (4 tasks, single module) but critical.** It is the safety net for the tool loop in M5. Failures here surface as a per-file abort (§8.2 per-file recovery): the file gets `<file>.ERROR.md`, the run continues. There is no graceful degradation below this layer.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration, M5 Tool Framework
- **Required modules from prior work:**
  - `senex/lmstudio_client.py` — `LMStudioClient.chat(task, messages, schema, tools=None)` used by `Compactor` to call the model with the compaction prompt + slice and `tools=None` (M3)
  - `senex/lmstudio_client.py` — `count_tokens(messages: list[Message]) -> int` (the same tiktoken encoder used by the main client; cached at module load per §14) is reused by `should_trigger` so trigger math matches what the chat loop sees (M3)
  - `senex/llm_client.py` — `LLMClient` protocol; `Compactor` types its client argument as `LLMClient`, NOT `LMStudioClient`, so future backends drop in (per §3 type discipline / spec §5.5)
  - `senex/prompts/compaction.md` — versioned, hashed compaction instructions (M2 Task 2.5.3); content includes the `<UNTRUSTED_CONTENT>` trust-boundary clause (see Watch-outs)
  - `senex/schema/compaction_response.schema.json` — JSON Schema for `CompactionResult` (M1 Task 1.8.3)
  - `senex/events.py` — `CompactionTriggered`, `CompactionComplete`, `CompactionError` event classes; pydantic v2 with `extra="forbid"` (M1)
  - `senex/config.py` — `CompactionCfg` with `enabled: bool`, `trigger_pct: float`, `target_pct: float`, `preserve_recent_turns: int`, `max_compactions_per_file: int` (M1, per spec §5.5.1 Configuration block)
  - `senex/tools/loop.py` — `ToolLoop.run(..., compaction_cb)` accepts a `Callable[[list[Message]], Awaitable[list[Message]]]` hook (M5 Task 5.8.2). M6 Task 6.4 binds this callback to `Compactor.maybe_compact`.
  - `senex/secret_redactor.py` — `SecretRedactor.redact(s)` applied to the `[COMPACTED]` block before insertion (per §5 secret redaction at boundary)
- **Required tools/state:**
  - LM Studio MUST NOT be required for unit tests; M6 unit tests use a mocked `LLMClient` (per §6 testing — mocks only at external boundaries).
  - `@pytest.mark.live` end-to-end compaction test deferred to M10 gate 13e.

## Deliverable

This milestone creates the following files:

- `senex/compaction.py` — `Compactor` class (executor) + `should_trigger()` pure function + `Compactor.maybe_compact()` async entry point + per-instance `compactions_so_far` counter
- `tests/unit/test_compaction.py` — executor + trigger + counter + budget-isolation tests
- Modifies `senex/tools/loop.py` (M5 Task 5.8 already accepts the hook): wires `compactor.maybe_compact` into `ToolLoop.run` after each tool-result append, BEFORE the next chat call

## Downstream consumers

- **M8** `FileAuditPhase` instantiates one `Compactor` per file (so `compactions_so_far` resets between files); on `CompactionLoopExceeded` or `CompactionFailed` it writes `<file>.ERROR.md` with `kind="compaction_loop_exceeded"` or `kind="compaction_failed"`. On `ContextOverflow` (opt-out path) it writes `kind="context_overflow"`.
- **M10** Live gate 13e verifies compaction fires end-to-end (synthetic-bloat env var → `CompactionTriggered` → `CompactionComplete` → audit completes; recorded LMS fixture mode replays deterministically).

## Spec sections referenced

- **§5.5.1 Context Compaction** — full subsection: Trigger, Mechanism, Configuration, Failure mode, Events, Replay, Opt-out, Budget-accounting clause. Single source of truth.
- **§5.4.1 Cross-cutting Response Schema** — *separate* from compaction schema. Cited only to disambiguate: `crosscut_response.schema.json` is for Phase 4 (themes); `compaction_response.schema.json` is for the per-file safety net. Do NOT conflate the two when wiring schema paths.
- **§5.6** event-table additions for `CompactionTriggered`/`CompactionComplete`/`CompactionError`.
- **§8.1 + §8.5** — compaction prompt is hashed at preflight; the hash folds into `prompt_hash` (resume bucket).
- **§ARCH-4** — bucketing on `prompt_hash` (compaction prompt is one of the snapshotted prompt assets).
- **§8.2** — per-file recovery semantics: `CompactionFailed` and `CompactionLoopExceeded` abort the file, not the run.

## Conventions referenced

- §3 Async / concurrency — `Compactor.run` and `maybe_compact` are `async`; bounded iteration (`max_compactions_per_file` is the explicit cap); `CancelledError` propagates through the LMS call.
- §4 Error handling — named exceptions only (`CompactionFailed`, `CompactionLoopExceeded`, `ContextOverflow`); fail-closed on schema-invalid response; recovery is event-driven (emit `CompactionError`, raise typed exception, let `FileAuditPhase` decide).
- §5 Security — the **compaction prompt has its own trust-boundary clause** mirroring the main system prompt (§5.1) because the compressed slice may contain attacker-injected text from the audited file's source. Output validated against `CompactionResult` schema before re-insertion. Secret redaction applied to the `[COMPACTED]` block before insertion.
- §10 Pydantic — `CompactionResult` is a pydantic v2 model with `extra="forbid"`; validated via `pydantic.TypeAdapter(CompactionResult).validate_python(json_dict)`; `model_dump_json()` for any persisted form.

## Key contracts

- **`class Compactor:`** (one instance per file in M8)
  - **`def __init__(self, client: LLMClient, prompt_path: Path, schema_path: Path, config: CompactionCfg, bus: EventBus, run_id: str, path: Path) -> None`**
    - `client` typed as `LLMClient` protocol (not concrete `LMStudioClient`).
    - `prompt_path` and `schema_path` resolved at preflight (TOCTOU snapshot, §8.1) and passed in.
    - `path` is the file under audit — used for event payloads.
    - Stores `self._compactions_so_far: int = 0`.
  - **`async def run(self, messages: list[Message]) -> list[Message]`** — executor.
    - Builds the slice = everything in `messages` EXCEPT (a) the current system prompt (index 0), (b) the last `config.preserve_recent_turns` messages, (c) any prior `[COMPACTED]` system messages (detected by `content.startswith("[COMPACTED]\n")`).
    - Calls `client.chat(task="compaction", messages=<compaction_prompt + slice>, schema=<compaction_schema>, tools=None)`. **`tools=None` is mandatory** — passing tools causes the model to attempt tool calls during summarization.
    - Validates the JSON response via `pydantic.TypeAdapter(CompactionResult).validate_python(json_dict)`.
    - On validation failure: retry **once** with a stricter-prompt addendum appended to the user message (`"Re-emit ONLY valid JSON conforming to CompactionResult; previous response was invalid."`). On second failure raises `CompactionFailed("schema_invalid", path=...)`.
    - On any LMS transport error (timeout, connection lost, HTTP non-2xx not classified as transient): raises `CompactionFailed("lms_error", path=..., cause=...)`.
    - On success: builds the replacement system message:
      ```
      [COMPACTED]
      <evidence_summary>

      Key findings noted: <comma-joined key_findings_so_far>
      Unanswered: <comma-joined unanswered_questions>
      ```
      Applies `SecretRedactor.redact()` to that string. Strips ANSI/control sequences (per §5).
    - Returns the rewritten message list: `[messages[0]] + [<COMPACTED block>] + messages[-config.preserve_recent_turns:]` (with prior `[COMPACTED]` blocks among the preserved suffix kept verbatim if they fall in that window).
  - **`def should_trigger(self, messages: list[Message], context_window: int) -> bool`** — pure function modulo `self._config`.
    - Returns `False` immediately if `self._config.enabled is False`.
    - Returns `True` if `count_tokens(messages) >= self._config.trigger_pct * context_window`.
    - Uses the same tiktoken encoder import as `lmstudio_client.count_tokens` so the budget math the loop sees matches what the model sees.
  - **`async def maybe_compact(self, messages: list[Message], context_window: int) -> list[Message]`** — entry point wired into `ToolLoop`.
    - If `not self.should_trigger(messages, context_window)`: returns `messages` unchanged.
    - If `self._compactions_so_far >= self._config.max_compactions_per_file`: raises `CompactionLoopExceeded(path=..., compactions_so_far=...)`. (The Nth+1 trigger aborts the file.)
    - Otherwise: emits `CompactionTriggered`, calls `self.run(messages)`, increments `self._compactions_so_far`, emits `CompactionComplete`. On any exception inside `run`: emits `CompactionError` and re-raises.

- **Named exceptions** (top-level in `senex/compaction.py`, per §4):
  - **`class CompactionFailed(Exception)`** — raised on LMS error, schema-invalid response after retry, or transport failure. Fields: `kind: Literal["lms_error", "schema_invalid"]`, `path: Path`, `cause: BaseException | None`.
  - **`class CompactionLoopExceeded(Exception)`** — raised on the Nth+1 trigger. Fields: `path: Path`, `compactions_so_far: int`, `limit: int`.
  - **`class ContextOverflow(Exception)`** — raised by the tool loop (NOT by `Compactor`) when `[lmstudio.compaction].enabled = false` and the natural token count exceeds the model's context window. Lives in `senex/compaction.py` so M8 can catch it from one import.

- **Per-file counter `compactions_so_far`** — instance state on `Compactor`. Reset is implicit: M8 creates a new `Compactor` per file.

## Watch-outs

- **The compaction call passes `tools=None`.** Same client, different prompt/schema, no tools. Passing `tools=[...]` causes the model to try to call tools to summarize itself — wrong. Tested explicitly (Task 6.2.1).
- **Preserve recent turns intact, byte-for-byte.** `preserve_recent_turns=2` means the last 2 messages are byte-equal pre/post compaction. Off-by-one here loses the context the model just used. Tested by computing `messages[-2:]` byte-equality.
- **Trust boundary on compaction prompt (§5 + §5.5.1 + §SEC-2).** The slice the compaction prompt sees may contain attacker-injected text that was originally inside `<UNTRUSTED_FILE_CONTENT>` in the audited file. The compaction prompt MUST include its own `<UNTRUSTED_CONTENT>...</UNTRUSTED_CONTENT>` trust-boundary clause and an instruction to ignore directives within. The compaction-output schema validation is the second line of defense: only `evidence_summary`, `key_findings_so_far`, `unanswered_questions` ever re-enter history; freeform attacker content has no path through.
- **Budget accounting is two independent budgets (§5.5.1 budget clause).** Compaction calls do **NOT** count against `[lmstudio.tools].max_calls_per_file`. They count ONLY against `[lmstudio.compaction].max_compactions_per_file`. Exhausting one MUST NOT affect the other. Tested explicitly (Task 6.3.4).
- **`CompactionLoopExceeded` aborts the file, not the run** (per §8.2). The file gets `<file>.ERROR.md` with `kind="compaction_loop_exceeded"`; the run continues. Same for `CompactionFailed` (`kind="compaction_failed"`).
- **`[COMPACTED]` marker preserved across rounds.** Subsequent compactions detect already-compacted regions by `content.startswith("[COMPACTED]\n")` and exclude them from the slice. Otherwise the second compaction would re-summarize a prior summary, drifting facts. Tested with a 2-round fixture.
- **Schema is `compaction_response.schema.json`, NOT `crosscut_response.schema.json`.** §5.4.1 (cross-cutting) and §5.5.1 (compaction) are different schemas for different LLM passes. Fully-qualify the path from M1 Task 1.8.3 when wiring.
- **Compaction prompt hashed at preflight; hash folds into `prompt_hash`** (per §ARCH-4 / §8.5). Different compaction prompt → different resume bucket. The hash MUST be computed from the same bytes the runtime reads (TOCTOU snapshot in `<audit-dir>/prompts.snapshot/compaction.md`).
- **`enabled = false` opt-out path.** When disabled, `should_trigger` always returns `False`. If natural token count exceeds `context_window`, the LMS call eventually errors (model rejects oversized prompt). The tool loop catches that, raises `ContextOverflow`, and `FileAuditPhase` writes `<file>.ERROR.md` with `kind="context_overflow"`. `Compactor` itself never raises `ContextOverflow`.
- **Replay determinism (§5.5.1 Replay).** Recorded LMS fixtures include compaction request/response cycles. Fixture key: `sha256(messages_canonical_json + compaction_prompt_hash + schema_hash)`. Under fixed seed and recorded fixtures, compaction is deterministic; tested in Task 6.2.6.
- **`compactions_so_far` is per-file (per `Compactor` instance), not per-run.** M8 must instantiate one `Compactor` per file. A run-level singleton would leak budget across files.
- **Cancellation propagation (§3).** `Compactor.run` is awaiting `client.chat`. `asyncio.CancelledError` MUST propagate; do not swallow. The retry-once path catches `CompactionFailed`-precursors only, not `CancelledError`.

## Patterns to follow

- §5.5.1 is the single source of truth for compaction behavior. Implement the executor as a direct, line-for-line translation of that section.
- **TDD with synthetic bloat:** "artificially-bloated tool result fixtures" means: write a fixture file whose tool-result token count, when appended to the running history, pushes total tokens >> `trigger_pct * ctx_window`. Plug it as a mocked tool result; assert `should_trigger` returns `True`, `maybe_compact` calls `run`, `CompactionTriggered` fires, the rewritten history's token count is below `target_pct * ctx_window` (within tolerance — model output length is bounded but not exact).
- **Pydantic-first validation (§10).** Build `CompactionResult` as a pydantic v2 model with `extra="forbid"` and `Annotated[str, Field(max_length=16384)]` on `evidence_summary`. Schema validation is the boundary; business code never re-validates.
- **Observability (§7).** Every error path emits exactly one structured event. `CompactionTriggered` BEFORE the call; `CompactionComplete` after success; `CompactionError` on any failure (before raising). Never log without emitting; never emit without logging.

## Tasks

### Task 6.1: Compaction prompt + schema (already shipped)

The compaction prompt and schema are delivered in earlier milestones; M6 references them only.

- **Prompt:** see [m2-walker-graph.md Task 2.5.3](m2-walker-graph.md#task-25-crosscut--handoff--compaction-prompts) — `senex/prompts/compaction.md`. M6 verifies the prompt content includes the literal `<UNTRUSTED_CONTENT>` trust-boundary instruction (Task 6.2.1 step).
- **Schema:** see [m1-foundation.md Task 1.8.3](m1-foundation.md#task-18-schema-files-5-json-schemas) — `senex/schema/compaction_response.schema.json`. Copied verbatim from spec §5.5.1.
- **Hash folding:** the preflight (M3) snapshots `compaction.md`, computes its sha256, and folds it into `prompt_hash` per §ARCH-4 / §8.5. M6 has nothing to add here; verify with the existing M2 prompt-hash regression test.

No new files in this task. Validate the upstream artifacts exist and have the expected hashes before proceeding.

- [x] **Step 6.1.1: Verify** `senex/prompts/compaction.md` exists, contains the `<UNTRUSTED_CONTENT>` clause, and matches the hash in `tests/fixtures/expected_prompt_hashes.json` (M2 Task 2.5.4).
- [x] **Step 6.1.2: Verify** `senex/schema/compaction_response.schema.json` exists and matches the spec §5.5.1 verbatim copy committed in M1 Task 1.8.3.

### Task 6.2: Compaction executor (`Compactor.run`)

**Files:**
- Create: `senex/compaction.py`
- Create: `tests/unit/test_compaction.py`

- [x] **Step 6.2.1: Failing tests** (TDD per §6 — write all, run to confirm RED, then implement):
  - `test_compactor_run_returns_rewritten_history_with_compacted_marker` — assert the returned list contains exactly one `system` message starting with `"[COMPACTED]\n"` at the slice position.
  - `test_compactor_run_preserves_recent_turns_byte_equal` — with `preserve_recent_turns=2`, assert `messages[-2:]` is byte-equal pre/post.
  - `test_compactor_run_preserves_system_prompt_index_0` — `messages[0]` is byte-equal pre/post.
  - `test_compactor_run_excludes_prior_compacted_blocks_from_slice` — feed history with one existing `[COMPACTED]` system message; assert it is NOT included in the slice sent to the model (mock client receives messages without it) and is preserved verbatim in the output.
  - `test_compactor_run_passes_tools_none_to_client` — assert mock client was called with `tools=None` (the call MUST NOT include tools).
  - `test_compactor_run_raises_compaction_failed_on_lms_error` — mock client raises a transport error; assert `CompactionFailed(kind="lms_error")` is raised AND `CompactionError` event is emitted before raising.
  - `test_compactor_run_retries_once_on_schema_invalid_then_succeeds` — first response invalid JSON, second valid; assert success and exactly 2 client calls.
  - `test_compactor_run_raises_compaction_failed_after_retry_exhausted` — both responses invalid; assert `CompactionFailed(kind="schema_invalid")` raised after exactly 2 attempts.
  - `test_compactor_run_validates_response_with_pydantic_type_adapter` — assert `pydantic.TypeAdapter(CompactionResult).validate_python` is on the call path (use a model whose schema validation diverges from naive `json.loads`).
  - `test_compactor_run_emits_events_in_order` — `CompactionTriggered` (from `maybe_compact`) → call → `CompactionComplete` (success path); on failure: `CompactionTriggered` → call → `CompactionError`. Assert exact order on the event bus.
  - `test_compactor_run_redacts_secrets_in_compacted_block` — feed a `key_findings_so_far` containing a fake secret pattern (`"sk-test-FAKE-SECRET-..."`); assert the inserted `[COMPACTED]` block has it redacted.
  - `test_compaction_prompt_includes_untrusted_content_clause` — open `senex/prompts/compaction.md`, assert literal substring `"<UNTRUSTED_CONTENT>"` is present (trust-boundary regression).
  - `test_compactor_run_propagates_cancelled_error` — mock client raises `asyncio.CancelledError`; assert it propagates without being wrapped.

- [x] **Step 6.2.2: Implement `Compactor`** in `senex/compaction.py`:
  - `from __future__ import annotations` at top (per §2).
  - Module docstring: "M6 context compaction executor. Implements §5.5.1."
  - `class CompactionResult(BaseModel)` with `extra="forbid"`; fields per spec §5.5.1.
  - `class CompactionFailed(Exception)`, `class CompactionLoopExceeded(Exception)`, `class ContextOverflow(Exception)` at module top-level.
  - `class Compactor` per the Key contracts section above.
  - `async def run(self, messages)` builds the slice, calls `client.chat(task="compaction", ..., tools=None)`, validates with `TypeAdapter(CompactionResult).validate_python`, retries once on validation failure with prompt addendum, raises `CompactionFailed` on second failure or LMS error, builds the `[COMPACTED]` block, applies `SecretRedactor.redact` and ANSI strip, returns rewritten list.

- [x] **Step 6.2.3: Emit events** in the correct order (per §7): `CompactionTriggered` is emitted by `maybe_compact` BEFORE calling `run`; `CompactionComplete` after `run` returns; `CompactionError` on any exception path before re-raising.

- [x] **Step 6.2.4: Run** `pytest tests/unit/test_compaction.py -v` → green.

- [x] **Step 6.2.5: Lint + type** per §11/§12: `ruff check senex/compaction.py tests/unit/test_compaction.py && mypy senex/`.

- [x] **Step 6.2.6: Replay determinism test** — record an LMS fixture for one compaction cycle; run the test twice with `RECORD_LMS=0`; assert byte-identical rewritten histories. Document the fixture key derivation: `sha256(messages_canonical_json + compaction_prompt_hash + schema_hash)`.

- [x] **Step 6.2.7: Impact check + commit** — `gitnexus_impact({target: "Compactor", direction: "upstream"})` (expected: no upstream callers yet; M8 wires it). Commit `feat(M6): context compaction executor`.

### Task 6.3: Trigger logic + per-file loop counter

**Files:**
- Modify: `senex/compaction.py` (add `should_trigger`, `maybe_compact`)
- Modify: `tests/unit/test_compaction.py`

- [x] **Step 6.3.1: Failing tests**:
  - `test_should_trigger_returns_true_at_threshold` — craft messages totaling ≥ `trigger_pct * ctx_window` tokens; assert `True`.
  - `test_should_trigger_returns_false_below_threshold` — craft messages well below; assert `False`.
  - `test_should_trigger_returns_false_when_disabled` — `enabled=False`; assert always `False` regardless of token count.
  - `test_should_trigger_uses_same_tiktoken_encoder_as_main_client` — assert `count_tokens` is the import from `lmstudio_client`, not a re-implementation.
  - `test_maybe_compact_skips_when_should_trigger_false` — assert returns `messages` unchanged and `client.chat` is NOT called and no events fired.
  - `test_maybe_compact_increments_counter_on_success` — call once; assert `_compactions_so_far == 1`.
  - `test_maybe_compact_raises_loop_exceeded_at_n_plus_one` — set `max_compactions_per_file=2`, force 3 triggers; assert 3rd raises `CompactionLoopExceeded(compactions_so_far=2, limit=2)`.
  - `test_maybe_compact_emits_triggered_before_call` — event ordering check.

- [x] **Step 6.3.2: Implement `should_trigger`** as described in Key contracts. Pure function modulo `self._config`. Reuses `count_tokens` from `lmstudio_client`.

- [x] **Step 6.3.3: Implement `maybe_compact`** as described in Key contracts. Bounded by `max_compactions_per_file`; raises `CompactionLoopExceeded` on Nth+1.

- [x] **Step 6.3.4: Budget independence test** — craft a scenario where `[lmstudio.tools].max_calls_per_file` is exhausted but `max_compactions_per_file` is not (and vice versa); assert `Compactor` honors only its own budget. This is the explicit test for §5.5.1's budget-accounting clause.

- [x] **Step 6.3.5: Run** tests → green; lint + type clean.

- [x] **Step 6.3.6: Impact check + commit** `feat(M6): compaction trigger + per-file loop counter`.

### Task 6.4: Wire into `ToolLoop`

**Files:**
- Modify: `senex/tools/loop.py` (M5 already accepts `compaction_cb`; this task supplies the binding)
- Modify: `tests/unit/test_tool_loop.py` or add `tests/unit/test_compaction_integration.py`

- [x] **Step 6.4.1: Failing integration test**:
  - `test_tool_loop_invokes_compactor_after_tool_result_before_next_chat` — synthetic-bloat fixture: a tool result whose redacted/truncated form still pushes total tokens past `trigger_pct * ctx_window`. Assert the call order is: tool dispatch → tool result append → `compactor.maybe_compact` called → next `client.chat`.
  - `test_tool_loop_audit_completes_after_compaction` — full happy-path test: bloat fixture forces compaction; final assistant response validates against `audit_response.schema.json`. Assert no errors and audit completes.
  - `test_tool_loop_emits_compaction_triggered_event` — assert `CompactionTriggered` is on the bus exactly once for one bloat cycle.
  - `test_tool_loop_propagates_compaction_loop_exceeded` — force `max_compactions_per_file + 1` triggers; assert `CompactionLoopExceeded` propagates out of `ToolLoop.run` (M8 catches it).
  - `test_tool_loop_propagates_compaction_failed` — mock client compaction-call fails; assert `CompactionFailed` propagates.
  - `test_tool_loop_does_not_count_compaction_against_tool_budget` — set `max_calls_per_file=3`, `max_compactions_per_file=2`; trigger 2 compactions inside 3 tool calls; assert tool budget is exactly 3 used, compaction budget 2 used, no `ToolBudgetExhausted` early.

- [x] **Step 6.4.2: Wire** `ToolLoop` to call `compactor.maybe_compact(messages, client.context_window)` after each tool-result append, BEFORE the next chat call (per spec §5.5.1 Trigger). M5 Task 5.8.2 already exposes `compaction_cb: Callable[[list[Message]], Awaitable[list[Message]]]`; M6 supplies the bound method `compactor.maybe_compact` (closing over `client.context_window`).

- [x] **Step 6.4.3: Opt-out / `ContextOverflow` test** — set `[lmstudio.compaction].enabled=false`; force a chat call where the message list naturally exceeds `client.context_window`; assert `ToolLoop` raises `ContextOverflow` (not `CompactionFailed`). This is the path M8 maps to `<file>.ERROR.md` with `kind="context_overflow"`.

- [x] **Step 6.4.4: Run** all M5 + M6 tests: `pytest tests/unit/test_tool_loop.py tests/unit/test_compaction.py tests/unit/test_compaction_integration.py -v`.

- [x] **Step 6.4.5: `gitnexus_detect_changes` pre-commit** per §11 — verify only `senex/compaction.py`, `senex/tools/loop.py`, and the relevant test files changed.

- [x] **Step 6.4.6: Commit** `feat(M6): tool loop ↔ compaction integration`.

## Acceptance criteria

- `pytest tests/unit/test_compaction.py tests/unit/test_compaction_integration.py -v` is 100% green.
- `ruff check senex/compaction.py tests/unit/test_compaction.py tests/unit/test_compaction_integration.py` clean.
- `mypy senex/` clean (per §2 strict mode).
- A test asserts that with `preserve_recent_turns=2`, `messages[-2:]` is byte-equal before and after compaction.
- A test asserts compaction reduces token count: `count_tokens(after) < count_tokens(before)` on a synthetic-bloat fixture.
- A test asserts that on the `(max_compactions_per_file + 1)`-th trigger, `CompactionLoopExceeded` is raised with `compactions_so_far` and `limit` populated.
- A test asserts the `[COMPACTED]` system message is present at the slice position in the rewritten history and starts with `"[COMPACTED]\n"`.
- A test asserts the compaction call passes `tools=None` to the client (no tools during summarization).
- A test asserts `senex/prompts/compaction.md` content contains the literal `<UNTRUSTED_CONTENT>` trust-boundary clause.
- A test asserts schema-invalid response → retry once → second invalid response raises `CompactionFailed(kind="schema_invalid")` after exactly 2 client calls.
- A test asserts the budget-independence invariant: exhausting `max_calls_per_file` does not affect `max_compactions_per_file` accounting and vice versa.
- A test asserts deterministic replay under recorded LMS fixtures with fixed seed.
- A test asserts `[lmstudio.compaction].enabled=false` causes `should_trigger` to always return `False` and natural overflow surfaces as `ContextOverflow` (not `CompactionFailed`).
- A tool-loop integration test asserts call order: tool dispatch → tool result append → `maybe_compact` → next `client.chat`.
- Events `CompactionTriggered` and `CompactionComplete` are emitted in order on a successful compaction; `CompactionError` is emitted on any failure path before the exception is raised.
- `gitnexus_detect_changes({scope: "staged"})` shows changes confined to `senex/compaction.py`, `senex/tools/loop.py`, and matching test files.
