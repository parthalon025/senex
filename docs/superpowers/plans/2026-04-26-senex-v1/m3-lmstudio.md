# Milestone 3: LM Studio Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M3 delivers the protocol surface to LM Studio: `LMStudioClient` opens streamed chat completions, extracts thinking content, validates structured output, runs the schema-fallback decision tree, enforces a pre-call token budget, retries 5xx with bounded backoff, computes/verifies model fingerprints, redacts secrets, and strips ANSI control sequences. M3 also delivers the recorded-replay test harness that every later milestone relies on for offline end-to-end testing.

**Architectural intent.** The client is the *only* surface that touches LM Studio HTTP. All retries, fingerprint checks, redaction, ANSI stripping, and `<think>`-tag removal happen here so callers can treat it as a trustworthy black box. Streaming happens at the protocol level (SSE) but is exposed as events on the bus, not as an iterator — this keeps `auditor.py`'s per-file loop a flat `await client.chat(...)` while the TUI sees ticks in real time.

**M3 ↔ M5 contract reconciliation (CRITICAL).** `LMStudioClient.chat()` handles **one round-trip** only — one HTTP chat-completion call. When the model emits `tool_calls`, `chat()` returns a `ChatResponse` with `tool_calls` populated; the **iteration controller** (which dispatches tools, appends `tool` messages, re-calls, enforces the per-file budget, and triggers compaction between turns) lives in `senex/tools/loop.py` (M5 Task 5.8) and consumes `LMStudioClient` via dependency injection. M3 Task 3.6 wires the `tools=[...]` parameter through to the LMS request and surfaces `tool_calls` in the response — nothing more. The **compaction trigger hook** (`compaction_callback`) likewise lives in `ToolLoop` (M5/M6), not in `LMStudioClient`. See Task 3.6 for the precise boundary.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness
- **Required modules from prior work:**
  - `senex/events.py` — `ThinkingStarted`, `ThinkingTick`, `ThinkingComplete`, `OutputStarted`, `OutputTick`, `OutputComplete`, `FileLLMCall`, `ModelFingerprintChanged`, `EventBus`
  - `senex/secret_redactor.py` — `SecretRedactor.redact(text: str) -> str`
  - `senex/config.py` — `LmStudioCfg`, `SamplingCfg`, `ThinkingCfg`, `ToolsCfg`, `CompactionCfg`
  - `senex/schema/audit_response.schema.json` — used post-hoc for `json_object` fallback validation
  - `senex/llm_client.py` — `LLMClient` Protocol (declared in M1; implemented here)
- **Required tools/state:**
  - LM Studio running on `localhost:1234` only for `@live` tests (deferred to M10; M3 uses recorded fixtures)
  - `google/gemma-4-26b-a4b` available in LM Studio for fixture capture (`RECORD_LMS=1`); not required during M3 unit tests

## Deliverable

This milestone creates the following files:

- `senex/lmstudio_client.py` — `LMStudioClient` implementing the `LLMClient` protocol; `async chat()`, streaming, schema fallback, retry, fingerprint verification, redaction, ANSI strip
- `senex/lmstudio_errors.py` — named exception classes (see "Exception classes" below)
- `tests/unit/test_lmstudio_client.py` — unit tests for streaming, schema fallback, retry policy, ANSI/redaction, fingerprint, capability probe
- `tests/fixtures/lms_responses/simple_audit.json` — hand-crafted minimal valid audit response
- `tests/fixtures/lms_responses/<sha>.json` — captured-mode fixtures (`RECORD_LMS=1`)
- `tests/recorded/conftest.py` — `recorded_lms` pytest fixture for replay
- `tests/recorded/test_audit_replay.py` — end-to-end test using replayed responses

## Downstream consumers

- **M4** Lifecycle wraps `list_loaded_models()` for status reporting; supplies the model fingerprint that M3 verifies per call.
- **M5** `ToolLoop` (Task 5.8) is the iteration controller; consumes `LMStudioClient.chat()` per round-trip via dependency injection.
- **M6** Compaction is triggered by `ToolLoop` *between* M3 round-trips, not inside `LMStudioClient.chat()`.
- **M8** `FileAuditPhase` calls `tool_loop.run(client=lmstudio_client, task='file_audit', messages, schema, tools=lens.tools)`. For tool-disabled tasks (cross-cutting), `phase.run()` calls `client.chat(...)` directly with `tools=None`.
- **M10** Live validation gates 13a / 13d / 13e exercise this client end-to-end against a real LM Studio.

## Spec sections referenced

- §5.4 `audit_response.schema.json` — output schema validated post-hoc when `json_object` fallback is taken.
- §5.5 LM Studio Inference Controls — full streaming + schema-fallback + tools= parameter; canonical reference for this milestone.
- §5.5 step list (steps 1–5) — bounded tool loop algorithm; **implemented in M5**, M3 only handles step 1's HTTP layer.
- §5.5.1 Compaction trigger — **implemented in M6 by `ToolLoop`**, not in M3.
- §5.5.2.3 Fingerprint computation — `sha256(json.dumps((model_id, quant, checkpoint_digest), sort_keys=True))`.
- §5.6 Event Bus — `ThinkingTick` / `OutputTick` are coalesce-safe; **per-turn counter resets on each `*_Started`** event.
- §8.1 Pre-flight — capability probe runs here (`probe_capabilities()`).
- §8.2 Per-file Recovery — schema-mismatch retry once with stricter prompt, then `ERROR.md`; LMS 5xx retry, 4xx fail-fast, TCP close pause/probe.

## Conventions cross-refs

- §1 (Code style) — module docstring; one responsibility per file (split errors into `lmstudio_errors.py`).
- §2 (Type discipline) — full type hints; `from __future__ import annotations`; no `Any` in public signatures; mypy `--strict`.
- §3 (Async) — every method is `async`; `httpx.AsyncClient`; no sync I/O on the chat path; bounded retry (`max_iterations` cap on retry loop); cancellation re-raised after cleanup.
- §4 (Error handling) — named exceptions in module top of `lmstudio_errors.py`; fail-closed; never bare except.
- §5 (Security) — `SecretRedactor` post-response, pre-history-append; ANSI strip on `content`; never log secrets; trust-boundary tags wrap audited file source (set by callers, not by this module).
- §6 (Testing) — TDD strict; `respx` for httpx mocking only; `@pytest.mark.live` on the live test; recorded replay is the deterministic substitute.
- §10 (Pydantic) — `ChatResponse`, `ProbedCapabilities` are `BaseModel(extra="forbid")`.

## Exception classes (`senex/lmstudio_errors.py`)

All exceptions are module top-level, inherit from `LMStudioError(Exception)`, and carry an `error_kind: str` class attribute used in `FileError` events.

```python
from __future__ import annotations


class LMStudioError(Exception):
    """Base for all LM Studio client failures. Subclasses set ``error_kind``."""

    error_kind: str = "lmstudio_error"


class LMSConnectionLost(LMStudioError):
    """Underlying TCP connection dropped or refused; re-probe required."""

    error_kind = "connection_lost"


class LMSResponseSchemaInvalid(LMStudioError):
    """Final response was valid JSON but did not match the audit schema."""

    error_kind = "schema_invalid"


class LMSResponseInvalidJSON(LMStudioError):
    """Final response could not be parsed as JSON (after ``<think>`` strip)."""

    error_kind = "invalid_json"


class TokenBudgetExceeded(LMStudioError):
    """Pre-call ``count_tokens()`` exceeded ``0.9 * context_window``."""

    error_kind = "token_budget_exceeded"


class SchemaNegotiationFailed(LMStudioError):
    """Both ``json_schema`` and ``json_object`` paths failed."""

    error_kind = "schema_negotiation_failed"


class FingerprintChanged(LMStudioError):
    """Per-call fingerprint differs from the runlock-pinned fingerprint."""

    error_kind = "fingerprint_changed"


class ThinkingTokensExceeded(LMStudioError):
    """Reasoning stream exceeded ``[lmstudio.thinking].max_thinking_tokens``."""

    error_kind = "thinking_tokens_exceeded"
```

`auditor.py` (M8) maps each `error_kind` to the §8.2 recovery path. Only `LMSConnectionLost` propagates above the per-file boundary (§4 conventions: connection-lost is run-level, all others are per-file).

## Key contracts

```python
# senex/lmstudio_client.py — public surface

async def chat(
    self,
    *,
    task: str,                          # "file_audit" | "cross_cutting" | "compaction"
    messages: list[ChatMessage],        # OpenAI-format messages (typed)
    schema: dict[str, object] | None,   # JSON Schema for response_format; None for compaction unstructured
    tools: list[ToolSchema] | None = None,  # OpenAI tool schemas; None disables tool surface
) -> ChatResponse: ...

async def list_loaded_models(self) -> list[LoadedModelInfo]: ...

async def probe_capabilities(self, model_id: str) -> ProbedCapabilities: ...

def count_tokens(self, messages: list[ChatMessage], model_id: str) -> int: ...

def compute_fingerprint(self, model_info: LoadedModelInfo) -> str: ...
```

```python
# Pydantic models, all extra="forbid"

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # only on role="tool"

class ToolCall(BaseModel):
    id: str
    type: Literal["function"]
    function: ToolCallFunction

class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON string per OpenAI convention

class ChatResponse(BaseModel):
    content: str                         # ANSI-stripped, <think>-stripped, redacted
    content_dict: dict[str, object] | None  # parsed + validated when schema given
    reasoning_content: str               # redacted; <think>-stripped already
    tool_calls: list[ToolCall] | None    # populated when model emits tool_calls
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"]
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    fingerprint: str                      # per-call fingerprint observed

class ProbedCapabilities(BaseModel):
    supports_tools: bool
    supports_schema_with_tools: bool
    supports_streaming: bool
    supports_reasoning_effort: bool

class LoadedModelInfo(BaseModel):
    id: str
    quantization: str = ""
    path: str = ""
    digest: str = ""
```

## Watch-outs (pitfalls common to the whole milestone)

- **`chat()` signature is shared across milestones.** Once M5/M6/M8 wire to it, changing the signature is a breaking change. Lock it in 3.1.
- **Tick coalescer state is per-turn, not per-call.** Reset `last_tick_tokens` and `last_tick_time` on every `*_Started` event (§5.6). `ThinkingComplete.total_thinking_tokens` is the sum of deltas for *that* turn only; per-file totals come from summing across turns in the auditor.
- **Strip `<think>...</think>` from `content`, not just `reasoning_content`.** Some thinking models leak think tags into the user-visible channel. Apply the regex *before* JSON parse / schema validation.
- **Redaction is post-response, pre-history-append.** Run `secret_redactor` on `reasoning_content`, `content`, and tool-call `arguments` *before* returning `ChatResponse`. The caller (M5 `ToolLoop`) appends to `messages` history; if redaction were caller-side a leaked secret could persist across turns before the caller noticed.
- **ANSI strip is `content`-only** (§5 conventions). Do not strip from tool-call arguments — they are already validated structured input and ANSI escapes inside a JSON string would be legitimate data, not control sequences.
- **Schema fallback caches the choice per session.** Re-probing every file is slow; cache the chosen mode (`json_schema` | `json_object`) on the instance after first success.
- **Token budget enforcement happens *before* the HTTP request.** `if count_tokens(messages) > 0.9 * context_window: raise TokenBudgetExceeded`. The auditor writes a `SKIPPED.md` without burning a request.
- **Fingerprint mismatch is a hard error.** Emit `ModelFingerprintChanged`, raise `FingerprintChanged`. The lifecycle layer (M4) decides whether to continue.
- **Never instantiate `httpx.AsyncClient` per call.** A single client is held on the instance, reused across calls, closed in `aclose()`. Per-call clients leak file descriptors and forfeit connection pooling.
- **Hold `asyncio.Task` references for any background streaming task.** §3 conventions: unheld tasks silently swallow exceptions.

## Patterns to follow

- §5.5 streaming + tool-loop pattern is the canonical algorithm. Implement step 1 (HTTP round-trip) here; steps 2–5 belong to M5 `ToolLoop`.
- **TDD with mocked transport:** use `respx` (the httpx mock library) for unit tests. Hand-craft fixture responses first, write the failing test, then implement.
- **Recorded-replay test harness** (Task 3.9) is the safety net for M8/M10 — once it exists, every end-to-end test runs offline.

---

## Tasks

### Task 3.1: Basic OpenAI-compatible chat client

**Files:**
- Create: `senex/lmstudio_client.py`
- Create: `senex/lmstudio_errors.py`
- Create: `tests/unit/test_lmstudio_client.py`
- Create: `tests/fixtures/lms_responses/simple_audit.json`

**Spec/conv refs:** §5.4 (schema), §5.5 (chat API), §10 conventions (pydantic), §3 conventions (async).

- [x] **Step 3.1.1: Hand-craft the fixture.** `tests/fixtures/lms_responses/simple_audit.json` is a complete OpenAI-style response with one assistant message whose `content` is a valid `audit_response.schema.json` payload (one finding, one recommendation). Hand-built; not from a live LMS yet.

- [x] **Step 3.1.2: Failing test** `test_chat_returns_validated_response`:
  ```python
  @pytest.mark.asyncio
  async def test_chat_returns_validated_response(respx_mock, fixture_dir):
      payload = json.loads((fixture_dir / "simple_audit.json").read_text())
      respx_mock.post("http://localhost:1234/v1/chat/completions").respond(json=payload)
      client = LMStudioClient(config=test_cfg, bus=DummyBus(), redactor=NoopRedactor())
      resp = await client.chat(
          task="file_audit",
          messages=[ChatMessage(role="user", content="audit me")],
          schema=load_audit_schema(),
          tools=None,
      )
      assert resp.content_dict is not None
      assert resp.content_dict["schema_version"] == 1
      assert resp.finish_reason == "stop"
      assert resp.tool_calls is None
  ```
  Run; expect `ImportError` / `ModuleNotFoundError` since the module does not exist.

- [x] **Step 3.1.3: Implement `LMStudioClient`** (non-streaming first):

  ```python
  class LMStudioClient:
      """OpenAI-compatible client for LM Studio. Implements ``LLMClient`` protocol.

      Implements §5.5 chat() per spec. Handles ONE round-trip per call;
      iteration (tool dispatch + compaction) is delegated to senex.tools.loop.ToolLoop.
      """

      def __init__(
          self,
          config: LmStudioCfg,
          bus: EventBus,
          redactor: SecretRedactor,
      ) -> None:
          self._config = config
          self._bus = bus
          self._redactor = redactor
          self._http = httpx.AsyncClient(
              base_url=config.base_url,
              timeout=httpx.Timeout(
                  connect=config.connect_timeout,
                  read=config.read_timeout,
                  write=10.0,
                  pool=10.0,
              ),
              headers={"Authorization": f"Bearer {config.api_key}"},
          )
          self._schema_mode: Literal["json_schema", "json_object"] | None = None
          self._fingerprint_pinned: str | None = None  # set by M4 before first chat
          self._encoder_cache: dict[str, "tiktoken.Encoding"] = {}

      async def aclose(self) -> None:
          await self._http.aclose()

      async def chat(
          self,
          *,
          task: str,
          messages: list[ChatMessage],
          schema: dict[str, object] | None,
          tools: list[ToolSchema] | None = None,
      ) -> ChatResponse:
          # See subsequent tasks for full body. Step 3.1 implements only the
          # non-streaming JSON-schema happy path.
          ...
  ```

  Validation strategy in 3.1: send `response_format={"type":"json_schema","json_schema":{"name":"audit_response","schema":schema,"strict":True}}`; parse `response.choices[0].message.content` as JSON; validate via `pydantic.TypeAdapter(AuditResponseModel).validate_python(parsed)`.

- [x] **Step 3.1.4: Run** `pytest tests/unit/test_lmstudio_client.py::test_chat_returns_validated_response -v` → green.

- [x] **Step 3.1.5: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k chat_returns_validated
  ```
  Expected literal output: `1 passed` in the trailing summary line.

- [x] **Step 3.1.6: Pitfalls.**
  - Forgetting `extra="forbid"` on `ChatResponse` → silent acceptance of malformed responses.
  - Putting `httpx.AsyncClient(...)` inside `chat()` → resource leak; one per call.
  - Validating against the raw JSON Schema dict instead of a pre-built `TypeAdapter` → 10x slower; cache the adapter as a class-level constant.

- [x] **Step 3.1.7: Definition of done.** Test green; `mypy --strict senex/lmstudio_client.py` clean; `ruff check senex/lmstudio_client.py senex/lmstudio_errors.py` clean.

- [x] **Step 3.1.8: Commit** `feat(M3): LMStudioClient basic chat with schema validation`.

---

### Task 3.2: Streaming + thinking content extraction + `<think>` tag stripping

**Spec/conv refs:** §5.5 streaming clause, §5.6 Tick scoping ("per-turn counter resets on each `*_Started`"), §5 conventions (ANSI strip, `<think>` strip), §8.2 ("`<think>` tags leaked into JSON").

- [x] **Step 3.2.1: Failing tests.**
  - `test_stream_emits_thinking_lifecycle_events`: SSE chunks with `delta.reasoning_content` deltas; assert sequence `ThinkingStarted` → ≥1 `ThinkingTick` → `ThinkingComplete` → `OutputStarted` → ≥1 `OutputTick` → `OutputComplete`.
  - `test_thinking_tick_coalesces_at_256_tokens_or_500ms`: drive 1024 reasoning tokens through the parser; assert ≥4 ticks (one per 256), but never two ticks within 500ms.
  - `test_per_turn_counter_resets_between_thinking_starts`: simulate **two** turns by feeding two SSE streams back-to-back (one chat call returns; second call reuses the same client). Assert turn-2's first `ThinkingTick.tokens_so_far == delta_since_last_tick` (i.e. counter started at 0 for turn 2).
  - `test_think_tag_stripped_from_content`: SSE delivers `content="<think>internal</think>final answer"`; `ChatResponse.content == "final answer"`.
  - `test_interleaved_think_tags_stripped`: SSE delivers `content="prefix<think>x</think>middle<think>y</think>suffix"`; `ChatResponse.content == "prefixmiddlesuffix"`. Use `re.DOTALL` because tags may contain newlines.
  - `test_total_thinking_tokens_equals_sum_of_ticks`: sum `ThinkingTick.delta_since_last_tick` events for the turn; assert it equals `ThinkingComplete.total_thinking_tokens`.

- [x] **Step 3.2.2: Implement streaming `chat()`.**

  ```python
  _THINK_TAG_RE: Final = re.compile(r"<think>.*?</think>", re.DOTALL)
  _ANSI_RE: Final = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")
  _TICK_TOKEN_THRESHOLD: Final = 256
  _TICK_TIME_THRESHOLD_S: Final = 0.5

  @dataclass
  class _TickCoalescer:
      """Per-turn token counter. Reset on each *_Started emission.

      §5.6: 'tokens_so_far is per-turn; resets at ThinkingStarted/OutputStarted.'
      """
      tokens_so_far: int = 0
      tokens_since_last_tick: int = 0
      last_tick_time: float = 0.0

      def reset(self) -> None:
          self.tokens_so_far = 0
          self.tokens_since_last_tick = 0
          self.last_tick_time = time.monotonic()

      def maybe_tick(self, delta_tokens: int) -> int | None:
          """Returns delta_since_last_tick if a tick should fire, else None."""
          self.tokens_so_far += delta_tokens
          self.tokens_since_last_tick += delta_tokens
          now = time.monotonic()
          if (
              self.tokens_since_last_tick >= _TICK_TOKEN_THRESHOLD
              or (now - self.last_tick_time) >= _TICK_TIME_THRESHOLD_S
          ):
              delta = self.tokens_since_last_tick
              self.tokens_since_last_tick = 0
              self.last_tick_time = now
              return delta
          return None
  ```

  Streaming loop pseudocode (real implementation parses `httpx`'s SSE response line-by-line):

  ```python
  async def _stream_chat(self, body: dict, path: str | None) -> _StreamResult:
      thinking_tick = _TickCoalescer()
      output_tick = _TickCoalescer()
      reasoning_buf: list[str] = []
      content_buf: list[str] = []
      phase: Literal["pre", "thinking", "output"] = "pre"
      finish_reason: str | None = None

      async with self._http.stream("POST", "/chat/completions", json=body) as r:
          r.raise_for_status()
          async for line in r.aiter_lines():
              if not line.startswith("data: "):
                  continue
              data = line.removeprefix("data: ")
              if data.strip() == "[DONE]":
                  break
              chunk = json.loads(data)
              delta = chunk["choices"][0].get("delta", {})
              finish_reason = chunk["choices"][0].get("finish_reason") or finish_reason

              # Reasoning channel (DeepSeek/Qwen "reasoning_content" + Gemma equiv.)
              if (rd := delta.get("reasoning_content")):
                  if phase != "thinking":
                      thinking_tick.reset()
                      await self._bus.publish(ThinkingStarted(path=path))
                      phase = "thinking"
                  reasoning_buf.append(rd)
                  delta_tok = self._approx_tokens(rd)
                  if (d := thinking_tick.maybe_tick(delta_tok)) is not None:
                      await self._bus.publish(ThinkingTick(
                          path=path,
                          tokens_so_far=thinking_tick.tokens_so_far,
                          delta_since_last_tick=d,
                      ))

              # Output channel
              if (cd := delta.get("content")):
                  if phase == "thinking":
                      await self._bus.publish(ThinkingComplete(
                          path=path,
                          total_thinking_tokens=thinking_tick.tokens_so_far,
                          latency_ms=...,
                      ))
                      phase = "output"
                      output_tick.reset()
                      await self._bus.publish(OutputStarted(path=path))
                  elif phase == "pre":
                      output_tick.reset()
                      await self._bus.publish(OutputStarted(path=path))
                      phase = "output"
                  content_buf.append(cd)
                  delta_tok = self._approx_tokens(cd)
                  if (d := output_tick.maybe_tick(delta_tok)) is not None:
                      await self._bus.publish(OutputTick(
                          path=path,
                          tokens_so_far=output_tick.tokens_so_far,
                          delta_since_last_tick=d,
                      ))

      # phase transitions out of loop
      if phase == "output":
          await self._bus.publish(OutputComplete(
              path=path,
              total_output_tokens=output_tick.tokens_so_far,
              latency_ms=...,
          ))

      raw_content = "".join(content_buf)
      stripped_content = _ANSI_RE.sub("", _THINK_TAG_RE.sub("", raw_content))
      return _StreamResult(
          content=stripped_content,
          reasoning_content="".join(reasoning_buf),
          finish_reason=finish_reason or "stop",
          tool_calls=...,  # built from delta.tool_calls accumulator
      )
  ```

- [x] **Step 3.2.3: Run** tests → green.

- [x] **Step 3.2.4: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k "stream or think_tag or tick or counter_resets"
  ```
  Expected: `6 passed` in the trailing summary.

- [x] **Step 3.2.5: Pitfalls.**
  - Using `re.compile(r"<think>.*?</think>")` without `re.DOTALL` → tags spanning newlines leak through.
  - Forgetting to reset `_TickCoalescer` on the second turn → counter monotonically grows across the whole run; `tokens_so_far` becomes meaningless. The `test_per_turn_counter_resets_between_thinking_starts` test exists specifically to catch this.
  - Emitting `ThinkingTick` before `ThinkingStarted` (race when `phase == "pre"` and the first chunk has reasoning_content) → subscribers may drop tickets they assume are mid-stream. The phase guard MUST set `ThinkingStarted` before the first tick, never after.
  - Stripping `<think>` tags AFTER schema validation → JSON parse fails on the unstripped text; spec §8.2 says strip first, then if stripping leaves invalid JSON, retry once with stricter prompt (Task 3.3 handles the retry).
  - ANSI strip applied to `reasoning_content` → spec §5 conventions explicitly says ANSI strip on `content` only.

- [x] **Step 3.2.6: Definition of done.** All 6 streaming tests green; tick cadence asserted both by token count AND wall-clock time; per-turn reset asserted across two turns; `<think>` tag regex byte-matches the spec literal `re.compile(r"<think>.*?</think>", re.DOTALL)`.

- [x] **Step 3.2.7: Commit** `feat(M3): streaming chat with Tick coalescing + think-tag strip`.

---

### Task 3.3: Structured-output schema fallback decision tree

**Spec/conv refs:** §5.5 ("On `response_format=json_schema` failure under thinking, falls back to `json_object` and validates post-hoc with Pydantic"), §8.2 ("Response valid JSON but schema mismatch — retry once with stricter prompt; on second failure store raw response in `<path>.RAW.json`").

The fallback decision tree:

```
1. If self._schema_mode is cached → use it directly.
2. Try response_format = {"type": "json_schema", "json_schema": {...}}.
   On HTTP 400 with body matching /schema|response_format|json_schema/i:
     → set self._schema_mode = "json_object", proceed to step 3.
   On HTTP 400 unrelated to schema:
     → raise httpx.HTTPStatusError unmodified (Task 3.5 handles retry policy).
   On 2xx success:
     → set self._schema_mode = "json_schema", validate, return.
3. Try response_format = {"type": "json_object"} with a system-message reminder
   "Respond ONLY with JSON conforming to: <schema_summary>".
   On 2xx:
     → strip <think>, parse JSON.
     → If parse fails → raise LMSResponseInvalidJSON.
     → Validate parsed dict via TypeAdapter(AuditResponseModel).validate_python().
     → On ValidationError → raise LMSResponseSchemaInvalid.
   On HTTP 4xx → raise SchemaNegotiationFailed("both modes refused").
```

- [x] **Step 3.3.1: Failing tests.**
  - `test_schema_fallback_on_400_schema_error`: respx returns 400 with body `{"error":{"message":"response_format json_schema not supported"}}` on first request, 200 with valid `json_object` payload on second; assert client falls back, validates, and caches `self._schema_mode == "json_object"`.
  - `test_schema_fallback_caches_decision_per_session`: drive two `chat()` calls; assert only the **first** issues two requests; the second uses cached `json_object` and issues exactly one.
  - `test_schema_fallback_400_unrelated_does_not_fallback`: respx returns 400 with body `{"error":{"message":"context length exceeded"}}`; assert `httpx.HTTPStatusError` propagates (caller / Task 3.5 retry decides).
  - `test_post_hoc_validation_failure_raises_LMSResponseSchemaInvalid`: respx returns 200 in `json_object` mode with `{"schema_version": "wrong_type"}`; assert `LMSResponseSchemaInvalid` raised.
  - `test_invalid_json_after_think_strip_raises_LMSResponseInvalidJSON`: respx returns 200 with `content = "<think>...</think>{not valid"`; assert `LMSResponseInvalidJSON`.

- [x] **Step 3.3.2: Implement `_chat_with_schema_fallback()`** following the decision tree above. Key snippet:

  ```python
  _SCHEMA_ERROR_RE: Final = re.compile(r"schema|response_format|json_schema", re.IGNORECASE)

  async def _chat_with_schema_fallback(
      self,
      base_body: dict[str, object],
      schema: dict[str, object],
  ) -> dict[str, object]:
      if self._schema_mode == "json_object":
          return await self._post_validate(base_body, schema, mode="json_object")
      try:
          result = await self._post_validate(base_body, schema, mode="json_schema")
      except httpx.HTTPStatusError as e:
          if e.response.status_code == 400 and _SCHEMA_ERROR_RE.search(e.response.text):
              self._schema_mode = "json_object"
              # Caching the decision now means subsequent calls skip the retry.
              return await self._post_validate(base_body, schema, mode="json_object")
          raise
      self._schema_mode = "json_schema"
      return result
  ```

  `_post_validate()` does:
  - Build the `response_format` field per `mode`.
  - Stream the response (Task 3.2 path).
  - Strip `<think>` tags from `content`.
  - Try `json.loads(content)` → on JSONDecodeError raise `LMSResponseInvalidJSON`.
  - Validate via `_AUDIT_RESPONSE_ADAPTER.validate_python(parsed)` → on ValidationError raise `LMSResponseSchemaInvalid`.

- [x] **Step 3.3.3: One-shot retry on schema-invalid response.** Per §8.2, on the first `LMSResponseSchemaInvalid` or `LMSResponseInvalidJSON` from `_post_validate()`, retry **once** with a stricter system prompt prefix:
  ```python
  STRICT_RETRY_PREAMBLE: Final = (
      "Your previous response did not validate against the required schema. "
      "Respond ONLY with valid JSON conforming exactly to the provided schema. "
      "Do not include explanatory prose, markdown fences, or <think> tags in the response body."
  )
  ```
  Implementation: `_post_validate()` accepts `_strict_retry: bool = False`; the wrapper attempts once with `False`, on schema/JSON failure calls again with `True` (which prepends the preamble to the system message). Second failure raises the original exception.

  Test: `test_schema_invalid_retried_once_with_stricter_prompt` — first call returns `{"schema_version": "wrong"}`, second call returns valid; assert `chat()` ultimately succeeds AND emits exactly one `chat/completions` request after the strict-retry preamble appears in the body.

- [x] **Step 3.3.4: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k "schema_fallback or post_hoc or invalid_json or strict_retry"
  ```
  Expected: `6 passed`.

- [x] **Step 3.3.5: Pitfalls.**
  - Caching the schema decision **before** validation succeeds → a transient 400 falsely demotes the session to `json_object` permanently. Cache only after successful validation.
  - Forgetting `re.IGNORECASE` on `_SCHEMA_ERROR_RE` → upstream "Response_Format" capitalizations slip through.
  - Re-parsing JSON in the strict-retry path with stale stripped content → always re-strip and re-parse from the new response; never reuse the failed parse.
  - Treating `LMSResponseInvalidJSON` as identical to `LMSResponseSchemaInvalid` in the retry path → spec §8.2 lists them as the same recovery, but the distinct exception types matter for telemetry.

- [x] **Step 3.3.6: Definition of done.** All 6 fallback tests green; per-session caching observable; strict-retry preamble byte-matches the literal in the test fixture.

- [x] **Step 3.3.7: Commit** `feat(M3): json_schema → json_object fallback path with one-shot strict retry`.

---

### Task 3.4: Token counting (pre-LMS budget check)

**Spec/conv refs:** §5.5.1 ("the loop computes total message tokens using the active model's tokenizer"), §8.1 ("Pre-LMS token count > 90% of context window — Skip"), §14 conventions ("cache expensive computations once").

- [ ] **Step 3.4.1: Failing tests.**
  - `test_count_tokens_returns_positive_for_nonempty_messages`: 3 messages totaling ~30 chars → expect > 0.
  - `test_count_tokens_grows_monotonically_with_content_length`: 100-char message > 10-char message.
  - `test_count_tokens_caches_encoder_per_model`: call twice; assert `tiktoken.get_encoding` called once (patch + assert call count).
  - `test_count_tokens_unknown_model_falls_back_to_chars_div_4`: pass `model_id="completely-fake-model"`; assert result ≈ `total_chars // 4` (within ±1 for rounding).
  - `test_chat_raises_token_budget_exceeded_above_90_percent`: configure `context_window=1000`, build messages totaling ~950 tokens; assert `chat()` raises `TokenBudgetExceeded` BEFORE any HTTP call (assert respx received zero requests).

- [ ] **Step 3.4.2: Implement `count_tokens`.**

  ```python
  def count_tokens(self, messages: list[ChatMessage], model_id: str) -> int:
      """Approximate token count using tiktoken cl100k_base.

      Note: tiktoken is calibrated for OpenAI tokenizers; gemma/qwen tokenization
      differs by ~5-15%. This is a budget guardrail, not a billing-grade count.
      The fallback (chars/4) is even coarser but always available.
      """
      enc = self._encoder_cache.get(model_id)
      if enc is None:
          try:
              # cl100k_base is closest publicly available tokenizer to most modern models
              enc = tiktoken.get_encoding("cl100k_base")
          except Exception:  # noqa: BLE001 — tiktoken raises generic on missing encoding
              enc = None
          self._encoder_cache[model_id] = enc  # cache None to skip retry
      total = 0
      for m in messages:
          parts: list[str] = []
          if m.content:
              parts.append(m.content)
          if m.tool_calls:
              for tc in m.tool_calls:
                  parts.append(tc.function.name)
                  parts.append(tc.function.arguments)
          joined = "\n".join(parts)
          if enc is not None:
              total += len(enc.encode(joined))
          else:
              total += len(joined) // 4  # fallback per docstring
          total += 4  # OpenAI's per-message overhead approximation
      return total
  ```

- [ ] **Step 3.4.3: Implement preflight check in `chat()`** (top of method, before any HTTP):

  ```python
  context_window = self._config.context_window  # set per-model in M4
  budget = int(0.9 * context_window)
  used = self.count_tokens(messages, self._config.model)
  if used > budget:
      raise TokenBudgetExceeded(
          f"messages={used} tokens exceeds 0.9 * {context_window} = {budget}"
      )
  ```

- [ ] **Step 3.4.4: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k "count_tokens or token_budget"
  ```
  Expected: `5 passed`.

- [ ] **Step 3.4.5: Pitfalls.**
  - Caching `None` encoder is intentional: if `cl100k_base` is unavailable, the retry would always fail. Caching `None` triggers fallback consistently.
  - Forgetting per-message overhead → undercounts; OpenAI's chat-completion format adds ~4 tokens of structural overhead per message.
  - Counting `tool_calls.id` (a UUID) → wastes the budget. Only `function.name` + `function.arguments` carry semantic content; the IDs are bookkeeping.
  - Hardcoding `0.9` instead of `self._config.token_budget_pct` → the config field exists in `LmStudioCfg` and the test asserts it; do not lose configurability.

- [ ] **Step 3.4.6: Definition of done.** All 5 token-counting tests green; encoder cached observably; `TokenBudgetExceeded` fires before any HTTP.

- [ ] **Step 3.4.7: Commit** `feat(M3): pre-LMS token counting + budget enforcement`.

---

### Task 3.5: Retry + backoff policy

**Spec/conv refs:** §5.5 (`http_retries=3`, `backoff_seconds=[5,15,45]`), §8.2 ("LMS HTTP 5xx or timeout — 3 retries with backoff (5s, 15s, 45s); LMS HTTP 4xx — No retry; LMS connection lost — Pause loop; re-probe `/v1/models` every 10s; resume when reachable"), §3 conventions (bounded iteration; explicit `max_iterations`).

The retry decision tree:

| Exception | Behavior |
|---|---|
| `httpx.HTTPStatusError` with `status_code >= 500` | Retry up to `http_retries=3` with `backoff_seconds=[5, 15, 45]`. Sleep `await asyncio.sleep(backoff[attempt])` between attempts. After exhaustion → re-raise. |
| `httpx.ReadTimeout` | Treat as 5xx (same retry policy). |
| `httpx.HTTPStatusError` with `400 <= status < 500` | NO retry. Re-raise immediately. |
| `httpx.ConnectError` (TCP refused / network unreachable) | Raise `LMSConnectionLost` immediately. The auditor (M8) catches this, pauses the loop, calls `await self._reprobe_until_alive(interval=10s)`, then resumes. |

- [ ] **Step 3.5.1: Failing tests.**
  - `test_5xx_retried_with_correct_backoff`: respx returns 503 three times then 200; patch `asyncio.sleep` to a recording fake; assert sleep was called with `[5, 15, 45]` in order.
  - `test_5xx_exhausted_after_3_retries_reraises`: respx returns 503 four times; assert final `httpx.HTTPStatusError` propagates AFTER exactly 3 retries (4 total requests).
  - `test_4xx_not_retried`: respx returns 401 once; assert `httpx.HTTPStatusError` raised on first call; assert respx received exactly 1 request.
  - `test_read_timeout_retried_like_5xx`: respx raises `httpx.ReadTimeout` twice then succeeds; assert backoffs `[5, 15]` slept; assert success on third attempt.
  - `test_connect_error_raises_LMSConnectionLost`: respx raises `httpx.ConnectError`; assert `LMSConnectionLost` (NOT bare `httpx.ConnectError`) bubbles up.
  - `test_reprobe_until_alive_polls_models_every_10s`: drive `_reprobe_until_alive()` with respx returning ConnectError twice then 200 on `/v1/models`; assert two sleeps of 10s, returns when reachable. (This method is exercised by M8; M3 just provides it.)

- [ ] **Step 3.5.2: Implement retry decorator** as an instance method (configurable from `LmStudioCfg`):

  ```python
  async def _post_with_retry(
      self,
      path: str,
      *,
      json_body: dict[str, object],
      stream: bool = False,
  ) -> httpx.Response:
      max_attempts = self._config.http_retries + 1  # initial + retries
      backoffs = self._config.backoff_seconds  # e.g. [5, 15, 45]
      assert len(backoffs) >= self._config.http_retries, "backoff list too short"

      last_exc: BaseException | None = None
      for attempt in range(max_attempts):
          try:
              response = await self._http.post(path, json=json_body)
              if 500 <= response.status_code < 600:
                  response.raise_for_status()  # raises HTTPStatusError → caught below
              if 400 <= response.status_code < 500:
                  response.raise_for_status()  # 4xx — no retry; re-raised below
              return response
          except httpx.ConnectError as e:
              raise LMSConnectionLost(str(e)) from e
          except (httpx.ReadTimeout, httpx.HTTPStatusError) as e:
              if isinstance(e, httpx.HTTPStatusError) and 400 <= e.response.status_code < 500:
                  raise  # 4xx fail-fast
              last_exc = e
              if attempt < max_attempts - 1:
                  await asyncio.sleep(backoffs[attempt])
                  continue
              raise
      assert last_exc is not None, "unreachable"
      raise last_exc

  async def _reprobe_until_alive(self, interval_s: float = 10.0, max_iterations: int = 360) -> None:
      """Block until /v1/models returns 200. Bounded by max_iterations (default 1h).

      §3 conventions: every loop has an explicit max_iterations cap.
      """
      for _ in range(max_iterations):
          try:
              r = await self._http.get("/models")
              if r.status_code == 200:
                  return
          except (httpx.ConnectError, httpx.ReadTimeout):
              pass
          await asyncio.sleep(interval_s)
      raise LMSConnectionLost(f"reprobe gave up after {max_iterations * interval_s}s")
  ```

- [ ] **Step 3.5.3: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k "retry or backoff or reprobe or connect_error"
  ```
  Expected: `6 passed`.

- [ ] **Step 3.5.4: Pitfalls.**
  - Retrying 4xx → wastes the budget; 4xx is by definition "your request is wrong, retrying won't help". Test `test_4xx_not_retried` exists to catch regressions here.
  - Forgetting to convert `ConnectError` → `LMSConnectionLost` → callers receive an httpx-specific type instead of the senex exception hierarchy; downstream `error_kind` mapping breaks.
  - Sleeping on the **last** attempt → adds 45s of latency before the final exception. The `if attempt < max_attempts - 1` guard skips that sleep.
  - Using `asyncio.sleep()` without making it patch-able → tests can't assert backoff cadence. Tests must use `monkeypatch.setattr(asyncio, "sleep", recording_fake)` or use `freezegun`.
  - Reprobe loop without `max_iterations` cap → §3 conventions violation. Default cap is 1 hour (360 × 10s); after that, fail loudly.

- [ ] **Step 3.5.5: Definition of done.** All 6 retry-policy tests green; backoff cadence asserted by recording sleep calls; 4xx fail-fast asserted with respx call count; `_reprobe_until_alive` bounded.

- [ ] **Step 3.5.6: Commit** `feat(M3): bounded retry with exponential backoff + reprobe loop`.

---

### Task 3.6: Tools= parameter (single round-trip — NOT iteration controller)

**CRITICAL — read this first.** This task implements `tools=[...]` plumbing in `LMStudioClient.chat()` for **one round-trip**. When the model emits `tool_calls`, `chat()` returns a `ChatResponse` with `tool_calls` populated and `finish_reason="tool_calls"`. **The iteration controller — which dispatches tools, appends `tool` messages, re-calls until no tool_calls, enforces `max_calls_per_file`, and triggers compaction — lives in `senex/tools/loop.py` (M5 Task 5.8) and consumes `LMStudioClient` via dependency injection.** The `compaction_callback` likewise lives in `ToolLoop`, NOT in this client.

**Spec/conv refs:** §5.5 ("Send chat request with `tools=[...]`, `tool_choice='auto'`"); §5.5 step list step 1 ONLY. Steps 2–5 belong to M5/M6.

The contract:

| Concern | Where it lives |
|---|---|
| Pass `tools=[...]` and `tool_choice="auto"` through to LMS | M3 (this task) |
| Parse `delta.tool_calls` from SSE; assemble final `tool_calls` list in `ChatResponse` | M3 (this task) |
| Set `finish_reason = "tool_calls"` when model emits tool calls | M3 (this task) |
| Dispatch tool calls to `tools.registry.dispatch()` | M5 (`ToolLoop`) |
| Append `role="tool"` messages with results | M5 (`ToolLoop`) |
| Re-call `chat()` with augmented messages | M5 (`ToolLoop`) |
| Enforce `max_calls_per_file` budget | M5 (`ToolLoop`) |
| Inject budget-exhaustion final-turn system message | M5 (`ToolLoop`) |
| Trigger compaction at `trigger_pct * context_window` | M6 (called from `ToolLoop` between turns) |
| Emit `ToolCall` / `ToolResult` / `ToolError` / `ToolBudgetExhausted` events | M5 (`ToolLoop`) |

- [ ] **Step 3.6.1: Failing test** `test_chat_with_tools_returns_tool_calls`:
  ```python
  @pytest.mark.asyncio
  async def test_chat_with_tools_returns_tool_calls(respx_mock):
      respx_mock.post("/chat/completions").respond(json={
          "id": "x", "model": "gemma",
          "choices": [{
              "index": 0, "finish_reason": "tool_calls",
              "message": {
                  "role": "assistant", "content": None,
                  "tool_calls": [{
                      "id": "call_1", "type": "function",
                      "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                  }],
              },
          }],
          "usage": {"prompt_tokens": 100, "completion_tokens": 20},
      })
      client = build_client()
      tool_schemas = [{"type": "function", "function": {"name": "read_file", ...}}]
      resp = await client.chat(
          task="file_audit",
          messages=[ChatMessage(role="user", content="audit a.py")],
          schema=load_audit_schema(),
          tools=tool_schemas,
      )
      # M3 surface: tool_calls is populated; iteration is the caller's job.
      assert resp.finish_reason == "tool_calls"
      assert resp.tool_calls is not None
      assert len(resp.tool_calls) == 1
      assert resp.tool_calls[0].function.name == "read_file"
      assert resp.content_dict is None  # no JSON content yet — tool calls in flight
  ```

- [ ] **Step 3.6.2: Failing test** `test_chat_with_tools_passes_tools_and_tool_choice_to_request`:
  Verify the request body contains `"tools": [...]` and `"tool_choice": "auto"` and forwards `tool_schemas` byte-for-byte.

- [ ] **Step 3.6.3: Failing test** `test_chat_without_tools_omits_tools_field`:
  When `tools=None`, request body MUST NOT contain `tools` or `tool_choice`. (Some LMS backends 400 on empty `tools=[]`.)

- [ ] **Step 3.6.4: Failing test** `test_chat_streamed_tool_calls_assembled_correctly`:
  SSE chunks deliver `tool_calls` deltas progressively (`{"index":0,"id":"call_1"}` then `{"index":0,"function":{"name":"read_file"}}` then `{"index":0,"function":{"arguments":"{\\"path\\":"}}` then `{"index":0,"function":{"arguments":"\\"a.py\\"}"}`); assert the final `ChatResponse.tool_calls[0].function.arguments == '{"path":"a.py"}'`.

- [ ] **Step 3.6.5: Implement** `chat()` extension:

  ```python
  async def chat(
      self,
      *,
      task: str,
      messages: list[ChatMessage],
      schema: dict[str, object] | None,
      tools: list[ToolSchema] | None = None,
  ) -> ChatResponse:
      # 1. Pre-call token budget check (Task 3.4)
      # 2. Build request body
      body: dict[str, object] = {
          "model": self._config.model,
          "messages": [m.model_dump(exclude_none=True) for m in messages],
          "stream": True,
          **self._config.sampling.model_dump(),
      }
      if schema is not None:
          # Schema fallback decision tree (Task 3.3)
          body["response_format"] = self._build_response_format(schema)
      if tools:  # explicitly truthy — None or [] both omit
          body["tools"] = tools
          body["tool_choice"] = "auto"

      # 3. Stream + retry (Tasks 3.2, 3.5)
      stream_result = await self._stream_with_retry(body, path=...)

      # 4. Redaction + ANSI strip (Task 3.10) — already applied inside _stream_with_retry
      # 5. Fingerprint verification (Task 3.7)
      observed_fp = self._extract_fingerprint(stream_result.headers)
      if self._fingerprint_pinned and observed_fp != self._fingerprint_pinned:
          await self._bus.publish(ModelFingerprintChanged(
              path=...,
              expected_fingerprint=self._fingerprint_pinned,
              observed_fingerprint=observed_fp,
          ))
          raise FingerprintChanged(
              f"expected={self._fingerprint_pinned} observed={observed_fp}"
          )

      # 6. Parse content (only when finish_reason="stop" AND schema provided)
      content_dict: dict[str, object] | None = None
      if stream_result.finish_reason == "stop" and schema is not None:
          content_dict = self._parse_and_validate(stream_result.content, schema)

      return ChatResponse(
          content=stream_result.content,
          content_dict=content_dict,
          reasoning_content=stream_result.reasoning_content,
          tool_calls=stream_result.tool_calls,  # populated when model emitted them
          finish_reason=stream_result.finish_reason,
          latency_ms=stream_result.latency_ms,
          prompt_tokens=stream_result.prompt_tokens,
          completion_tokens=stream_result.completion_tokens,
          fingerprint=observed_fp,
      )
  ```

- [ ] **Step 3.6.6: Documentation in the docstring (CRITICAL).** Add this verbatim to `chat()`:

  ```
  This method handles ONE chat-completion round-trip. When the assistant
  emits tool_calls, the response is returned with `tool_calls` populated and
  `finish_reason == "tool_calls"`; the caller (senex.tools.loop.ToolLoop)
  is responsible for dispatching the tools, appending tool messages, and
  re-invoking chat(). The compaction trigger likewise lives in ToolLoop,
  not here. See spec §5.5 step list — this method implements step 1 only.

  Iteration controller: senex.tools.loop.ToolLoop (M5 Task 5.8).
  ```

- [ ] **Step 3.6.7: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k "tools or tool_calls or tool_choice"
  ```
  Expected: `4 passed`.

- [ ] **Step 3.6.8: Pitfalls.**
  - Implementing iteration / `_tool_loop()` here violates the M3↔M5 boundary. **Do not** dispatch tools from `LMStudioClient`. If you find yourself writing `await tools.registry.dispatch(...)` inside this file, you have wandered into M5's territory — stop.
  - Sending `tools=[]` (empty list) instead of omitting the field — some LMS backends 400 on empty arrays. The `if tools:` guard (truthy check) handles both `None` and `[]`.
  - Assembling `tool_calls` from SSE deltas requires accumulating by `index` across chunks; argument strings arrive piecewise. A naive `latest_chunk.tool_calls` overwrite loses earlier deltas.
  - Setting `finish_reason="stop"` on a tool-calls response — if the model emits tool_calls, the OpenAI spec sets `finish_reason="tool_calls"`. Validate the field; do not invent it.
  - Schema validation when `finish_reason="tool_calls"` — the `content` is `None` (or empty); validating it as JSON fails. Skip schema validation entirely on tool-calls responses; M5 `ToolLoop` will validate the eventual `finish_reason="stop"` response.

- [ ] **Step 3.6.9: Definition of done.** All 4 tests green; docstring includes the M3↔M5 boundary note byte-matching the literal above; `tool_calls` propagation works under both single-chunk and split-across-chunks SSE delivery.

- [ ] **Step 3.6.10: Commit** `feat(M3): tools= parameter passes through to LMS request; single round-trip only`.

---

### Task 3.7: Fingerprint computation + per-call verification

**Spec/conv refs:** §5.5.2.3 (canonical fingerprint protocol), §5.6 (`ModelFingerprintChanged` event), §8.2 (mid-run fingerprint change → file abort).

**Canonical computation (§5.5.2.3):**

```
fingerprint = sha256(
    json.dumps((model_id, quant, checkpoint_digest), sort_keys=True).encode("utf-8")
).hexdigest()
```

Where:
- `model_id` — resolved id from `GET /v1/models` (sentinel `@auto`/`@first` already collapsed by M4).
- `quant` — `LoadedModelInfo.quantization` (e.g. `"Q4_K_M"`); empty string if absent.
- `checkpoint_digest` — sha256 of the manifest file (`config.json` or equivalent) at `LoadedModelInfo.path`, NEVER over multi-GB weight files. If `path` is a directory, hash its `config.json`. If `path` is absent, set `checkpoint_digest = "unknown"` and emit a `ModelFingerprintIndeterminate` warning.

**Per-call verification.** LM Studio doesn't (yet) emit a fingerprint header. The fallback: re-probe `/v1/models` lazily — once per N calls (configurable, default 50) OR on any heartbeat-detected model state change. Cache the probe result for the run; on mismatch with `self._fingerprint_pinned`, raise `FingerprintChanged`.

- [ ] **Step 3.7.1: Failing tests.**
  - `test_compute_fingerprint_canonical`: given `LoadedModelInfo(id="gemma-4", quantization="Q4_K_M", digest="abc123")`, assert `compute_fingerprint(...)` returns the literal sha256 of `'["gemma-4","Q4_K_M","abc123"]'` (sort_keys=True → tuple-as-list). Lock the byte-exact value in the test.
  - `test_compute_fingerprint_missing_quant_uses_empty_string`: `quantization=""` does not crash; produces a stable hash.
  - `test_compute_fingerprint_missing_path_marks_unknown`: `digest=""` → `checkpoint_digest="unknown"` baked into the tuple; emits `ModelFingerprintIndeterminate` warning event.
  - `test_per_call_fingerprint_mismatch_raises_FingerprintChanged`: pin `client._fingerprint_pinned = "abc..."`; respx returns a chat completion AND a separate `/v1/models` probe with a different model digest; assert `FingerprintChanged` raised AND `ModelFingerprintChanged` event published.
  - `test_per_call_fingerprint_match_proceeds_silently`: pin matches probe; assert no event published; chat returns normally.

- [ ] **Step 3.7.2: Implement.**

  ```python
  def compute_fingerprint(self, model_info: LoadedModelInfo) -> str:
      digest = model_info.digest if model_info.digest else "unknown"
      tup = (model_info.id, model_info.quantization or "", digest)
      payload = json.dumps(list(tup), sort_keys=True, separators=(",", ":")).encode()
      return hashlib.sha256(payload).hexdigest()

  async def _verify_fingerprint(self, *, path: str | None) -> str:
      """Cheap path: cached for N calls. Re-probe on heartbeat change.

      Returns the observed fingerprint. Raises FingerprintChanged on mismatch
      with self._fingerprint_pinned.
      """
      now = time.monotonic()
      if (
          self._fingerprint_cached is not None
          and (now - self._fingerprint_cached_at) < self._config.fingerprint_recheck_interval_s
      ):
          return self._fingerprint_cached
      models = await self.list_loaded_models()
      target = next((m for m in models if m.id == self._config.model), None)
      if target is None:
          raise LMSConnectionLost(f"model {self._config.model} not loaded")
      observed = self.compute_fingerprint(target)
      self._fingerprint_cached = observed
      self._fingerprint_cached_at = now
      if self._fingerprint_pinned and observed != self._fingerprint_pinned:
          await self._bus.publish(ModelFingerprintChanged(
              path=path,
              expected_fingerprint=self._fingerprint_pinned,
              observed_fingerprint=observed,
          ))
          raise FingerprintChanged(
              f"expected={self._fingerprint_pinned} observed={observed}"
          )
      return observed
  ```

  Call `_verify_fingerprint()` at the start of every `chat()`, before the request body is built. M4's lifecycle layer pins `self._fingerprint_pinned` once at runlock acquisition.

- [ ] **Step 3.7.3: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k "fingerprint"
  ```
  Expected: `5 passed`.

- [ ] **Step 3.7.4: Pitfalls.**
  - Hashing the raw weight file → multi-GB read on every preflight; spec §5.5.2.3 explicitly forbids this. Hash the manifest (`config.json`) only.
  - `json.dumps` without `separators=(",", ":")` → whitespace differences across Python versions yield divergent fingerprints. Lock the canonical separators.
  - Probing `/v1/models` on every chat call → unnecessarily slow under tool-loop iteration. Cache for `fingerprint_recheck_interval_s` (default 60s).
  - Forgetting to set `self._fingerprint_pinned` from M4 → first chat treats every observed fingerprint as a match (since pinned is None); set explicitly during M4 wiring.

- [ ] **Step 3.7.5: Definition of done.** All 5 fingerprint tests green; canonical bytes asserted byte-exactly; `ModelFingerprintChanged` event published before `FingerprintChanged` raised (event order matters for replay).

- [ ] **Step 3.7.6: Commit** `feat(M3): model fingerprint computation + per-call verification`.

---

### Task 3.8: Health probe (`list_loaded_models`) + capability probe

**Spec/conv refs:** §8.1 ("LM Studio reachable at `/v1/models` — Exit 3"; "LM Studio model supports `tools` parameter (probe)"; "LM Studio model supports `tools` + `response_format=json_schema` simultaneously (probe)"; "Streaming works").

- [ ] **Step 3.8.1: Implement `list_loaded_models()`** — `GET /v1/models`, parse `{"data": [{"id": ..., "quantization": ..., "path": ...}, ...]}` into `list[LoadedModelInfo]`. On non-200 → `LMSConnectionLost`. Used by M4 lifecycle (status reporting) and by `_verify_fingerprint` (Task 3.7).

- [ ] **Step 3.8.2: Implement `probe_capabilities(model_id) -> ProbedCapabilities`.** Sends ONE chat call with:
  - `messages=[{"role":"user","content":"hi"}]`
  - `tools=[NOOP_TOOL_SCHEMA]` where `NOOP_TOOL_SCHEMA` is a fixed `{"type":"function","function":{"name":"noop","description":"...","parameters":{"type":"object"}}}`
  - `response_format={"type":"json_schema","json_schema":{...minimal schema...}}`
  - `max_tokens=1`
  - `stream=True`

  Decision tree from response:

  | Outcome | `supports_tools` | `supports_schema_with_tools` | `supports_streaming` |
  |---|---|---|---|
  | 2xx, SSE delivered | True | True | True |
  | 400 with `/tools/i` | False | False | True (via separate streaming probe) |
  | 400 with `/schema|response_format/i` | True | False | True |
  | 400 unrelated | propagate (preflight will exit 3) | | |
  | Non-streamed body (no SSE chunks) | computed independently | | False |

  Cache result for the run on the instance; M4 lifecycle reads it during preflight.

  ```python
  _NOOP_TOOL: Final = {
      "type": "function",
      "function": {
          "name": "noop",
          "description": "no-op probe tool; never invoked",
          "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
      },
  }
  _NOOP_SCHEMA: Final = {
      "type": "object",
      "properties": {"ok": {"type": "boolean"}},
      "required": ["ok"],
      "additionalProperties": False,
  }

  async def probe_capabilities(self, model_id: str) -> ProbedCapabilities:
      if self._caps_cached is not None:
          return self._caps_cached
      # ... probe logic ...
      caps = ProbedCapabilities(
          supports_tools=...,
          supports_schema_with_tools=...,
          supports_streaming=...,
          supports_reasoning_effort=...,
      )
      self._caps_cached = caps
      return caps
  ```

- [ ] **Step 3.8.3: Failing tests.**
  - `test_list_loaded_models_parses_response`: respx returns `/v1/models` JSON; assert `[LoadedModelInfo(id=..., quantization=..., path=...)]`.
  - `test_list_loaded_models_unreachable_raises_LMSConnectionLost`: respx raises `httpx.ConnectError`; assert `LMSConnectionLost`.
  - `test_probe_capabilities_full_support`: respx returns 2xx SSE; assert all four flags True.
  - `test_probe_capabilities_tools_unsupported`: respx returns 400 with body `{"error":{"message":"tools parameter not supported"}}`; assert `supports_tools=False`.
  - `test_probe_capabilities_schema_with_tools_unsupported`: respx returns 400 with body `{"error":{"message":"response_format json_schema cannot combine with tools"}}`; assert `supports_schema_with_tools=False, supports_tools=True`.
  - `test_probe_capabilities_cached`: two calls; assert respx received exactly the probe-burst-count of requests on first call and ZERO on second.

- [ ] **Step 3.8.4: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k "list_loaded or probe_capabilities"
  ```
  Expected: `6 passed`.

- [ ] **Step 3.8.5: Pitfalls.**
  - Forgetting `additionalProperties: False` on the noop tool's parameters → some backends reject the probe with a confusing error, masking real capability detection.
  - Confusing `supports_tools` and `supports_schema_with_tools` → the spec lists them as **separate** preflight probes; don't collapse them. A model can support tools alone but not tools-with-json-schema.
  - Caching probe failure as success → on transient 503, `self._caps_cached` should remain `None`. Cache only on successful determinations.
  - Probing with `max_tokens=0` → some backends reject; use `max_tokens=1` per spec.

- [ ] **Step 3.8.6: Definition of done.** All 6 probe tests green; capability cache observable; noop tool schema byte-exact match the literal in the test fixture.

- [ ] **Step 3.8.7: Commit** `feat(M3): LM Studio health + capability probing`.

---

### Task 3.9: Recorded-LMS replay test infrastructure

**Spec/conv refs:** §9 testing strategy ("Recorded LMS — pytest + JSON fixtures; auditor coroutine end-to-end with replayed responses"), §6 conventions ("recorded LMS replay for end-to-end tests; capture once with `RECORD_LMS=1`").

**Files:**
- Create: `tests/recorded/conftest.py`
- Create: `tests/recorded/test_audit_replay.py`
- Use existing: `tests/fixtures/lms_responses/`

**Hash key.** Each fixture is named by:
```
sha256(json.dumps({"messages": [...], "tools": [...], "response_format": {...}}, sort_keys=True).encode()).hexdigest()
```
This makes replay deterministic for identical request bodies and forces re-capture when bodies change.

- [ ] **Step 3.9.1: Implement the `recorded_lms` fixture** in `tests/recorded/conftest.py`:

  ```python
  @pytest.fixture
  def recorded_lms(respx_mock, request, fixture_dir):
      """Intercept httpx requests; replay from fixtures or capture if RECORD_LMS=1.

      Capture mode: pass-through to the live LMS at base_url; write the response
      body to tests/fixtures/lms_responses/<sha>.json keyed by request hash.

      Replay mode (default): read fixture from disk; respond with stored body.
      """
      record = bool(int(os.environ.get("RECORD_LMS", "0")))
      fixtures_dir = fixture_dir / "lms_responses"
      fixtures_dir.mkdir(exist_ok=True)

      def hash_request(req: httpx.Request) -> str:
          body = json.loads(req.content)
          # Stable subset — exclude transient fields like timestamps if any
          key = {
              "messages": body.get("messages"),
              "tools": body.get("tools"),
              "response_format": body.get("response_format"),
          }
          serialized = json.dumps(key, sort_keys=True).encode()
          return hashlib.sha256(serialized).hexdigest()

      def replay_or_capture(req: httpx.Request) -> httpx.Response:
          h = hash_request(req)
          path = fixtures_dir / f"{h}.json"
          if record:
              # Forward to real LMS, then save
              with httpx.Client() as live:
                  real = live.send(req)
              path.write_text(json.dumps({
                  "request_hash": h,
                  "status_code": real.status_code,
                  "headers": dict(real.headers),
                  "body": real.json(),
              }, indent=2))
              return httpx.Response(
                  status_code=real.status_code,
                  headers=real.headers,
                  json=real.json(),
              )
          if not path.exists():
              raise AssertionError(
                  f"No fixture for request hash {h}; "
                  f"run with RECORD_LMS=1 to capture: {req.method} {req.url}"
              )
          stored = json.loads(path.read_text())
          return httpx.Response(
              status_code=stored["status_code"],
              headers=stored["headers"],
              json=stored["body"],
          )

      respx_mock.route(host="localhost").mock(side_effect=replay_or_capture)
      yield respx_mock
  ```

- [ ] **Step 3.9.2: Sample fixture file structure** at `tests/fixtures/lms_responses/<sha>.json`:

  ```json
  {
    "request_hash": "a1b2c3...",
    "status_code": 200,
    "headers": {"content-type": "application/json"},
    "body": {
      "id": "chatcmpl-...",
      "model": "google/gemma-4-26b-a4b",
      "choices": [
        {
          "index": 0,
          "finish_reason": "stop",
          "message": {
            "role": "assistant",
            "content": "{\"schema_version\":1,\"overall_assessment\":\"...\",\"findings\":[...],\"recommendations\":[]}",
            "reasoning_content": "..."
          }
        }
      ],
      "usage": {"prompt_tokens": 1234, "completion_tokens": 567}
    }
  }
  ```

  Note: streaming responses are stored as `{"sse_chunks": ["data: ...\\n\\n", "data: [DONE]\\n\\n"]}` instead of `body` so replay can reconstruct the SSE stream.

- [ ] **Step 3.9.3: End-to-end test** `tests/recorded/test_audit_replay.py::test_replayed_audit_produces_valid_response`:
  ```python
  @pytest.mark.asyncio
  async def test_replayed_audit_produces_valid_response(recorded_lms, fixture_dir):
      client = LMStudioClient(...)
      messages = [
          ChatMessage(role="system", content="audit this Python file..."),
          ChatMessage(role="user", content="<UNTRUSTED>x = 1\\n</UNTRUSTED>"),
      ]
      response = await client.chat(
          task="file_audit",
          messages=messages,
          schema=load_audit_schema(),
          tools=None,
      )
      assert response.content_dict is not None
      assert response.content_dict["schema_version"] == 1
      assert isinstance(response.content_dict["findings"], list)
  ```

- [ ] **Step 3.9.4: Verification commands.**
  - Replay (default):
    ```bash
    pytest tests/recorded/test_audit_replay.py -v
    ```
    Expected: `1 passed`.
  - Capture (manual, requires live LMS):
    ```bash
    RECORD_LMS=1 pytest tests/recorded/test_audit_replay.py -v
    ```
    Expected: `1 passed` AND new files appear under `tests/fixtures/lms_responses/`. Verify by `git status` showing untracked `.json` fixtures.

- [ ] **Step 3.9.5: Pitfalls.**
  - Including transient fields (timestamps, request IDs, random seeds when `seed_random=true`) in the hash key → cache key changes per run; replay always misses. The `key = {messages, tools, response_format}` subset ignores them.
  - Hashing the raw `req.content` bytes → ordering of dict keys may differ; use `json.loads` + `sort_keys=True`.
  - Forgetting to add `tests/fixtures/lms_responses/` to git → CI runs without fixtures and every test fails. Commit fixtures with the test changes.
  - `RECORD_LMS=1` mode silently overwriting fixtures → mass-fixture corruption if the live LMS gives a different response. Print a warning on overwrite; gate destructive overwrites behind `RECORD_LMS=overwrite`.

- [ ] **Step 3.9.6: Definition of done.** Replay test green offline (no LMS running); capture mode tested manually once with the live LMS and the resulting fixture committed; `RECORD_LMS` env var documented in test docstring.

- [ ] **Step 3.9.7: Commit** `feat(M3): recorded-LMS replay test harness`.

---

### Task 3.10: ANSI strip + secret redaction integration

**Spec/conv refs:** §5 conventions ("Secret redaction at the boundary"; "ANSI/control-sequence strip on every string that flows from LLM/tool output to a TUI widget or persisted markdown"), §SEC-7 (ANSI strip on `content`).

**Required regexes (literal, byte-exact):**

```python
_ANSI_RE: Final = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")
# Matches CSI sequences (\x1b [ ... letter) and OSC sequences (\x1b ] ... \x07)
_THINK_TAG_RE: Final = re.compile(r"<think>.*?</think>", re.DOTALL)
```

**Order of operations on the response (CRITICAL):**

1. Strip `<think>` tags from `content` (Task 3.2 already does this).
2. Apply ANSI strip to `content` (this task — `content` only, NOT `reasoning_content`, NOT tool-call arguments).
3. Apply `secret_redactor.redact()` to `content`, `reasoning_content`, and each `tool_call.function.arguments`.
4. ONLY after all three transforms, parse JSON / validate schema / return `ChatResponse`.

If any step is reordered, secrets can leak into the JSON validation error message (which gets logged). Fail-closed.

- [ ] **Step 3.10.1: Failing tests.**
  - `test_ansi_stripped_from_content`: SSE delivers `content="\x1b[31mERROR\x1b[0m: failed"`; assert `ChatResponse.content == "ERROR: failed"`.
  - `test_ansi_osc_sequence_stripped`: SSE delivers `content="\x1b]0;title\x07hello"`; assert content `== "hello"`.
  - `test_ansi_NOT_stripped_from_reasoning_content`: reasoning_content contains ANSI; assert it survives untouched (reasoning is rendered separately; ANSI in reasoning is informational).
  - `test_ansi_NOT_stripped_from_tool_call_arguments`: tool_call arguments contain `\x1b[...m`; assert the JSON string is preserved byte-for-byte (these are validated structured input, not display strings).
  - `test_aws_key_redacted_from_content`: SSE delivers `content="key=AKIAIOSFODNN7EXAMPLE"`; assert `ChatResponse.content == "key=[REDACTED:aws_access_key]"`.
  - `test_secret_redacted_from_reasoning_content`: same for `reasoning_content`.
  - `test_secret_redacted_from_tool_call_arguments`: tool call arguments contain `{"token": "ghp_xxxx..."}`; assert the redacted form appears in `ChatResponse.tool_calls[0].function.arguments`.
  - `test_secret_redacted_BEFORE_history_append`: drive a chat call where the response contains `AKIA...`; verify that the value RETURNED to the caller is already redacted. (The fact that the caller appends to history is the caller's concern — what M3 guarantees is that everything in `ChatResponse` is post-redaction.)
  - `test_redaction_order_strip_then_redact`: response has `content="<think>secret=AKIA...EXAMPLE</think>safe"`; assert ChatResponse.content is `"safe"` (think strip happens first; the secret was inside the think tag and never reaches the redactor's input — but if it had leaked, it would still be redacted).

- [ ] **Step 3.10.2: Wire `secret_redactor`** into the post-stream path:

  ```python
  async def _finalize_stream_result(self, raw: _RawStreamResult) -> _StreamResult:
      """Apply think-strip, ANSI-strip, redaction in spec order."""
      content = _THINK_TAG_RE.sub("", raw.content)
      content = _ANSI_RE.sub("", content)
      content = self._redactor.redact(content)

      reasoning = self._redactor.redact(raw.reasoning_content)
      # NOTE: reasoning is NOT ANSI-stripped (per §SEC-7, content-only).
      # NOTE: <think> tags from reasoning are kept as-is (the whole reasoning channel
      # IS the thinking content; stripping its delimiters would corrupt it).

      tool_calls: list[ToolCall] | None = None
      if raw.tool_calls:
          tool_calls = []
          for tc in raw.tool_calls:
              redacted_args = self._redactor.redact(tc.function.arguments)
              tool_calls.append(ToolCall(
                  id=tc.id,
                  type=tc.type,
                  function=ToolCallFunction(
                      name=tc.function.name,
                      arguments=redacted_args,
                  ),
              ))

      return _StreamResult(
          content=content,
          reasoning_content=reasoning,
          tool_calls=tool_calls,
          finish_reason=raw.finish_reason,
          ...,
      )
  ```

- [ ] **Step 3.10.3: Verification command:**
  ```bash
  pytest tests/unit/test_lmstudio_client.py -v -k "ansi or redact or secret"
  ```
  Expected: `9 passed`.

- [ ] **Step 3.10.4: Pitfalls.**
  - ANSI-stripping `reasoning_content` → reasoning rendered with ANSI escapes is informational; `<file>.thinking.md` consumers handle ANSI safely. Spec §SEC-7 explicitly says `content`-only.
  - ANSI-stripping `tool_call.function.arguments` → ANSI bytes inside a JSON string literal are legitimate structured data, not display control. Stripping them silently corrupts tool inputs.
  - Redacting **before** ANSI strip → secret regex can match across ANSI escape boundaries and miss the secret; strip ANSI first so redactor sees clean text.
  - Redacting **after** schema validation → if validation fails on a response containing a secret, the error message gets logged with the secret embedded. Always redact first; validation runs on already-redacted text.
  - Mutating the original `raw.tool_calls` list in-place → if the same object is referenced by event payloads (already published), the published events would silently get redacted retroactively. Build a new list of new `ToolCall` instances.

- [ ] **Step 3.10.5: Definition of done.** All 9 ANSI/redaction tests green; ANSI regex byte-matches `re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")` exactly; order-of-operations test passes asserting strip-then-redact.

- [ ] **Step 3.10.6: Commit** `feat(M3): redaction + ANSI strip integrated into chat path`.

---

## Acceptance criteria

All criteria below must hold simultaneously before this milestone is considered complete.

### Test suite

- `pytest tests/unit/test_lmstudio_client.py tests/recorded/ -v` exits 0 with no skipped tests except those marked `@pytest.mark.live`.
- `mypy --strict senex/lmstudio_client.py senex/lmstudio_errors.py` exits 0.
- `ruff check senex/lmstudio_client.py senex/lmstudio_errors.py tests/unit/test_lmstudio_client.py tests/recorded/` exits 0.
- Coverage on `senex/lmstudio_client.py` ≥ 85% (per §6 conventions; this module is in the high-coverage list).

### Functional assertions

- A test asserts that simulated AWS keys in mock LMS responses are redacted to `[REDACTED:aws_access_key]` *before* appearing in the returned `ChatResponse.content`.
- A test asserts that 5xx responses trigger exactly 3 retries with backoffs `[5, 15, 45]` seconds (sleep recording + assertion); a 4xx response triggers zero retries.
- A test asserts that mocked `<think>foo</think>bar` in `content` results in `ChatResponse.content == "bar"`, then `ChatResponse.content_dict` is `None` if `bar` isn't valid JSON OR validates if `bar` is valid JSON.
- A test asserts that `<think>foo</think>{"schema_version":1,...}` parses correctly and validates against the audit schema.
- A test asserts that the recorded-LMS fixture replays a 1-file end-to-end audit producing a valid `AuditResponse` with no live LM Studio connection.
- A test asserts that `RECORD_LMS=1 pytest tests/recorded/test_audit_replay.py` writes new fixture files to `tests/fixtures/lms_responses/` (manual; verified by `git status` showing untracked `.json` files after).
- A test asserts that a mocked response with a different `fingerprint` (computed from a different `LoadedModelInfo`) raises `FingerprintChanged` AND emits `ModelFingerprintChanged` (event published BEFORE exception raised).
- A test asserts that the ANSI strip regex byte-equals `re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")` and the `<think>` regex byte-equals `re.compile(r"<think>.*?</think>", re.DOTALL)`.
- A test asserts that the schema fallback caches its decision per session (second call uses cached mode; respx receives only one request when first call resolved to `json_schema`).
- A test asserts that the Tick coalescer resets between turns (per-turn counter starts at 0 on each new `ThinkingStarted` / `OutputStarted`).
- The live `@pytest.mark.live` test exists (validates `LMSConnectionLost` on a stopped server) but is excluded from default `pytest` runs.

### M3 ↔ M5 boundary check (mandatory)

- `senex/lmstudio_client.py` MUST NOT import `senex.tools.registry` or `senex.tools.loop`. Verified by:
  ```bash
  grep -E "from senex\\.tools|import senex\\.tools" senex/lmstudio_client.py
  ```
  Expected output: empty (no matches).
- `senex/lmstudio_client.py` MUST NOT contain the string `compaction_callback`. Compaction is M5/M6's concern; M3 only exposes the `tools=` parameter.
- `senex/lmstudio_client.py` MUST NOT contain a `_tool_loop`, `tool_loop`, `iterate_tools`, or similar method. The file's public surface is `chat`, `list_loaded_models`, `probe_capabilities`, `count_tokens`, `compute_fingerprint`, `aclose`. Anything else is over-reach.
- `LMStudioClient.chat()`'s docstring MUST contain the substring `"ONE chat-completion round-trip"` and `"senex.tools.loop.ToolLoop"` (case-sensitive). Verified by grep.

### Async / lifecycle

- `LMStudioClient` is closeable: `await client.aclose()` exists and is tested.
- Every `await self._http.post(...)` is wrapped in either `_post_with_retry` or `_stream_with_retry`. No raw httpx calls escape the retry envelope. Verified by grep.
- All exception classes are imported from `senex.lmstudio_errors`, not redefined inline.
