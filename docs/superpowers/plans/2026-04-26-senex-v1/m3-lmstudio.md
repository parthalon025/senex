# Milestone 3: LM Studio Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M3 delivers the heart of senex: the LMStudioClient that talks to a local LM Studio server, streams chat completions, extracts thinking content, validates structured output against JSON schemas, runs the bounded tool loop, triggers compaction, and verifies model fingerprints. Everything in M5 (tools), M6 (compaction integration), and M8 (per-file phase) calls into this client.

**Architectural intent:** The client is the *only* surface that touches LM Studio HTTP. All retries, fingerprint checks, redaction, and ANSI stripping happen here so callers can treat it as a trustworthy black box. Streaming happens at the protocol level (SSE) but is exposed as events on the bus, not as an iterator — this keeps the auditor's per-file loop a flat `await client.chat(...)` while the TUI sees ticks in real time.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness
- **Required modules from prior work:**
  - `senex/events.py` — `ToolCall`, `ToolResult`, `ToolError`, `ThinkingStarted/Tick/Complete`, `OutputStarted/Tick/Complete`, `FileLLMCall`, `ModelFingerprintChanged`
  - `senex/secret_redactor.py` — `SecretRedactor` (used post-response)
  - `senex/config.py` — `LmStudioCfg`, `SamplingCfg`, `ThinkingCfg`, `ToolsCfg`
  - `senex/schema/audit_response.schema.json` — used in `chat()` response validation
- **Required tools/state:**
  - LM Studio running on `localhost:1234` (default) for any `@live` tests (deferred to M10; M3 uses recorded fixtures)
  - `google/gemma-4-26b-a4b` available in LM Studio (for fixture capture, not required during M3 unit tests)

## Deliverable

This milestone creates the following files:

- `senex/lmstudio_client.py` — `LMStudioClient` with `async chat()`, streaming, schema fallback, retry, tool loop hook, fingerprint verification, redaction
- `tests/unit/test_lmstudio_client.py` — unit tests for streaming, schema fallback, retry policy, ANSI/redaction
- `tests/fixtures/lms_responses/simple_audit.json` — hand-crafted minimal valid audit response
- `tests/recorded/conftest.py` — `recorded_lms` fixture for replay
- `tests/recorded/test_audit_replay.py` — end-to-end test using replayed responses
- `tests/fixtures/lms_responses/<hash>.json` — captured-mode fixtures (`RECORD_LMS=1`)

## Downstream consumers

- **M4** Lifecycle wraps the client's `list_loaded_models()` for status reporting; M4 supplies the model fingerprint that M3 verifies per call.
- **M5** Tool loop is implemented inside the client (`_tool_loop()`); M5 supplies the tool dispatch callback.
- **M6** Compaction is triggered from inside the client's tool loop via an injected `compaction_callback`.
- **M8** FileAuditPhase calls `client.chat(task='file_audit', messages, schema, tools=lens.tools)`.
- **M10** Live validation gates 13a/13d/13e exercise this client end-to-end against a real LM Studio.

## Spec sections referenced

- §5.5 LM Studio Inference Controls — full streaming + tool-loop pattern; the canonical reference for this milestone
- §5.5 step list (steps 1-5) — bounded tool loop algorithm
- §5.5.2.3 Fingerprint computation — `sha256(f"{model_id}|{quant}|{checkpoint_digest}")`
- §SEC tag-strip recovery — `<think>...</think>` stripping in `content`
- §SEC-7 — ANSI control-sequence strip on `content`
- §ARCH-9 — pre-LMS token budget enforcement
- §8 Error handling — retry policy (5xx → 3 retries with `[5, 15, 45]`s; 4xx → no retry; TCP close → re-probe)
- ThinkingTick scoping — per-turn counter resets on each `*_Started`

## Key contracts

- **`LMStudioClient.__init__(config: LmStudioCfg, bus: EventBus, secret_redactor: SecretRedactor)`**
- **`async LMStudioClient.chat(task: str, messages: list, schema: dict, tools: list|None = None) -> ChatResponse`** — non-streaming first, then upgraded to streaming.
- **`ChatResponse(content_dict, reasoning_content, tool_calls_made, latency_ms, prompt_tokens, completion_tokens)`** — return shape.
- **`_chat_with_schema_fallback()`** — `json_schema` first; on 400 fall back to `json_object` + post-hoc validation.
- **`_tool_loop()`** — bounded by `max_calls_per_file`; emits `ToolCall`/`ToolResult`/`ToolError`/`ToolBudgetExhausted`; calls `compaction_callback(messages)` when token threshold crossed.
- **`_compute_fingerprint(model_info) -> str`** — sha256 of `model_id|quant|checkpoint_digest`.
- **`list_loaded_models() -> list[str]`** — `GET /v1/models`.
- **`probe_capabilities(model_id) -> dict`** — 1-token probe with tools + structured output.
- **`count_tokens(messages, model_id) -> int`** — `tiktoken cl100k_base` cached encoder; fallback char/4.

## Watch-outs

- **The `chat()` signature is shared across milestones.** Once M5/M6/M8 wire to it, changing the signature is a breaking change — get it right in 3.1.
- **Tick coalescer state is per-turn, not per-call.** Reset `last_tick_tokens` and `last_tick_time` on every `*_Started` event. ThinkingComplete's `total_thinking_tokens` is the sum of the deltas for *that* turn only.
- **Strip `<think>...</think>` from `content` (not just `reasoning_content`).** Some models leak think tags into the user-visible channel. Regex strip per §SEC tag-strip recovery.
- **Redaction is post-response, pre-history-append.** Run `secret_redactor` on `reasoning_content`, `content`, and tool_call `arguments` *before* they go into `messages` history (so a leaked secret never persists across turns).
- **ANSI strip is `content`-only** per §SEC-7. Do not strip from tool_call arguments (they are already validated structured input).
- **Schema fallback caches the choice per session.** Re-probing every file is slow; cache on first success.
- **Token budget enforcement happens *before* the HTTP request.** `if count_tokens(messages) > 0.9 * context_window: raise TokenBudgetExceeded` so the auditor can write a SKIPPED.md without burning a request.
- **Fingerprint mismatch is a hard error.** Emit `ModelFingerprintChanged` and raise `FingerprintChanged`. The lifecycle layer (M4) decides whether to continue.

## Patterns to follow

- §5.5 streaming + tool-loop pattern is the canonical algorithm. Implement it as the reference for the loop and recovery logic.
- **TDD with mocked transport:** use `respx` (httpx mock) or a hand-rolled async transport mock. Hand-craft fixture responses first, write the failing test, then implement.
- **Recorded-replay test harness** (Task 3.9) is the safety net for M8/M10 — once you have it, every end-to-end test can run offline.

## Tasks

### Task 3.1: Basic OpenAI-compatible chat client

**Files:**
- Create: `senex/lmstudio_client.py`
- Create: `tests/unit/test_lmstudio_client.py`
- Create: `tests/fixtures/lms_responses/simple_audit.json`

- [ ] **Step 3.1.1: Capture a real LMS response** by calling `senex audit` (when the tool exists) on a tiny fixture file. For now, hand-craft a minimal valid audit response JSON conforming to the schema and save as fixture.

- [ ] **Step 3.1.2: Failing test** `test_chat_returns_validated_response` — uses a mock OpenAI client (or `respx` for httpx) returning the fixture; asserts client returns a parsed `AuditResponse` pydantic instance.

- [ ] **Step 3.1.3: Implement `LMStudioClient`** with:
  - `__init__(config: LmStudioCfg, bus: EventBus, secret_redactor: SecretRedactor)`
  - `async chat(task: str, messages: list, schema: dict, tools: list|None = None) -> ChatResponse` — basic non-streaming version first
  - Returns `ChatResponse(content_dict, reasoning_content, tool_calls_made, latency_ms, prompt_tokens, completion_tokens)`
  - Validates content against schema

- [ ] **Step 3.1.4: Run** tests → green.

- [ ] **Step 3.1.5: Commit** `feat(M3): LMStudioClient basic chat with schema validation`.

### Task 3.2: Streaming + thinking content extraction

- [ ] **Step 3.2.1: Failing tests** for SSE parsing:
  - Stream returns `reasoning_content` token deltas → emits `ThinkingTick` events at most every 256 tokens / 500ms
  - Stream emits `ThinkingStarted` on first reasoning token, `ThinkingComplete` on phase transition
  - Stream emits `OutputStarted/Tick/Complete` on `content` deltas
  - Total `ThinkingComplete.total_thinking_tokens` = sum of all `delta_since_last_tick` for that turn
  - `<think>...</think>` tags interleaved in `content` are stripped before adding to `ChatResponse.content`

- [ ] **Step 3.2.2: Implement streaming version** of `chat()`:
  - `stream=True`, parse SSE chunks
  - Tick coalescer with `last_tick_tokens` + `last_tick_time` tracked per-turn
  - `<think>` strip via regex (per spec §SEC tag-strip recovery)
  - Per-turn counter resets on each `*_Started` event (per spec ThinkingTick scoping)

- [ ] **Step 3.2.3: Run** tests → green.

- [ ] **Step 3.2.4: Commit** `feat(M3): streaming chat with Tick coalescing + think-tag strip`.

### Task 3.3: Structured output negotiation

- [ ] **Step 3.3.1: Failing test** — when `response_format=json_schema` returns 4xx, client falls back to `response_format=json_object` + post-hoc Pydantic validation.

- [ ] **Step 3.3.2: Implement `_chat_with_schema_fallback()`** — try `json_schema` first; on 400 with schema-related error, retry with `json_object`; validate response post-hoc; on 2nd failure, raise `SchemaNegotiationFailed`.

- [ ] **Step 3.3.3: Cache backend choice** per session (avoid re-probing every file).

- [ ] **Step 3.3.4: Commit** `feat(M3): json_schema → json_object fallback path`.

### Task 3.4: Token counting (pre-LMS check)

- [ ] **Step 3.4.1: Failing tests** for `count_tokens(messages, model_id)`:
  - Returns >0 for non-empty messages
  - Different content lengths → different counts
  - Caches the encoder per model_id

- [ ] **Step 3.4.2: Implement** using `tiktoken.get_encoding("cl100k_base")` as default; fall back to character-count / 4 if model unknown. Add note that gemma tokenization ≠ tiktoken but it's a reasonable estimate.

- [ ] **Step 3.4.3: Implement preflight check in client**: before `chat()`, if `count_tokens(messages) > 0.9 * context_window`, raise `TokenBudgetExceeded` for caller to handle (per spec §ARCH-9).

- [ ] **Step 3.4.4: Commit** `feat(M3): pre-LMS token counting + budget enforcement`.

### Task 3.5: Retry + backoff policy

- [ ] **Step 3.5.1: Failing tests**:
  - 5xx response → 3 retries with backoff [5, 15, 45]s
  - 4xx → no retry, raises immediately
  - TCP close → pause loop, re-probe `/v1/models`, resume on success

- [ ] **Step 3.5.2: Implement retry decorator** wrapping `chat()`. Configurable from `LmStudioCfg.http_retries` and `backoff_seconds`.

- [ ] **Step 3.5.3: Commit** `feat(M3): bounded retry with exponential backoff`.

### Task 3.6: Tool-loop integration in chat()

- [ ] **Step 3.6.1: Failing test** — when `tools=[...]` provided and model returns `tool_calls`, client dispatches via injected callback, appends `tool` messages, re-sends until response without tool_calls AND content validates.

- [ ] **Step 3.6.2: Implement `_tool_loop()`** per spec §5.5 step list (steps 1-5). Bounded by `max_calls_per_file`. Budget-exhaustion injection on cap.

- [ ] **Step 3.6.3: Compaction trigger hook** — after each tool result append, check `count_tokens(messages) >= trigger_pct * context_window`; if so, await injected `compaction_callback(messages)`.

- [ ] **Step 3.6.4: Emit events** for each turn: `ToolCall`, `ToolResult`/`ToolError`, `ToolBudgetExhausted`.

- [ ] **Step 3.6.5: Commit** `feat(M3): bounded tool loop with compaction trigger hook`.

### Task 3.7: Fingerprint extraction

- [ ] **Step 3.7.1: Implement `_compute_fingerprint(model_info)`** per spec §5.5.2.3:
  ```
  fingerprint = sha256(f"{model_id}|{quant}|{checkpoint_digest}").hexdigest()
  ```
  When attaching to externally-loaded model: read these from `GET /v1/models` response (LM Studio provides quant + manifest hash).

- [ ] **Step 3.7.2: Verify fingerprint per chat** — if `chat()` response includes a fingerprint header that mismatches the one captured at runlock, emit `ModelFingerprintChanged` event and raise `FingerprintChanged` exception.

- [ ] **Step 3.7.3: Commit** `feat(M3): model fingerprint computation + per-call verification`.

### Task 3.8: Health probe / list models

- [ ] **Step 3.8.1: Implement `list_loaded_models()`** — `GET /v1/models`, parse, return list of model IDs.

- [ ] **Step 3.8.2: Implement `probe_capabilities(model_id)`** — sends a 1-token probe with `tools=[noop_tool]`, `response_format=json_schema`, captures whether model supports tools + structured output simultaneously. Used by preflight (§8.1).

- [ ] **Step 3.8.3: Commit** `feat(M3): LM Studio health + capability probing`.

### Task 3.9: Recorded-LMS replay test infrastructure

**Files:**
- Create: `tests/recorded/conftest.py`
- Create: `tests/recorded/test_audit_replay.py`

- [ ] **Step 3.9.1: Implement a `recorded_lms` fixture** that intercepts httpx requests and replays from `tests/fixtures/lms_responses/<hash>.json`. Hash = sha256 of (messages + tools + response_format).

- [ ] **Step 3.9.2: Capture mode** — `RECORD_LMS=1 pytest ...` writes responses to fixtures dir for future replay.

- [ ] **Step 3.9.3: Test** end-to-end audit on a 1-file fixture using replayed responses → produces valid AuditResponse + ChatResponse.

- [ ] **Step 3.9.4: Commit** `feat(M3): recorded-LMS replay test harness`.

### Task 3.10: ANSI strip + redaction integration

- [ ] **Step 3.10.1: Wire `secret_redactor`** into `chat()` post-response: redact `reasoning_content`, `content` text, and tool_call args before they're added to message history or persisted.

- [ ] **Step 3.10.2: ANSI control-sequence strip** on `content` per §SEC-7.

- [ ] **Step 3.10.3: Tests** that simulated AWS keys / control sequences in mock LMS responses are redacted/stripped.

- [ ] **Step 3.10.4: Commit** `feat(M3): redaction + ANSI strip integrated into chat path`.

## Acceptance criteria

- `pytest tests/unit/test_lmstudio_client.py tests/recorded/ -v` is 100% green.
- A test asserts that simulated AWS keys in mock LMS responses are redacted to `[REDACTED:aws_access_key]` *before* appearing in `messages` history.
- A test asserts that 5xx responses trigger exactly 3 retries with backoffs `[5, 15, 45]`s; a 400 response triggers zero retries.
- A test asserts that mocked `<think>foo</think>bar` in `content` results in `ChatResponse.content == "bar"`.
- `recorded_lms` fixture replays a 1-file end-to-end audit producing a valid `AuditResponse` with no live LM Studio connection.
- `RECORD_LMS=1 pytest tests/recorded/test_audit_replay.py` writes new fixture files to `tests/fixtures/lms_responses/` (manual capture mode works).
- Fingerprint mismatch test: a mocked response with a different `fingerprint` header raises `FingerprintChanged` and emits `ModelFingerprintChanged`.
- Live `@live` test (deferred to M10) is marked but not run.
