# Migration: LMStudio → Ollama + ReAct Tool Calling

## Context

Senex's primary LLM backend is LM Studio, accessed via an OpenAI-compatible
HTTP client (`senex/lmstudio_client.py`, ~1300 LOC) plus a 958-LOC
lifecycle module that brokers model load/unload over the `lmstudio` SDK
or `lms` CLI. This migration:

1. **Replaces LM Studio entirely with Ollama** as the sole LLM backend.
   No fallback. Ollama is targeted because it is the most common local
   LLM runtime, has a stable Python SDK, and supports both a native
   `/api/chat` and an OpenAI-compatible `/v1/chat/completions` surface.
2. **Introduces a ReAct loop as the universal tool-calling mechanism.**
   Many local models (especially smaller open weights) have weak or
   absent native tool-call support; a prompt-driven `Thought / Action /
   Action Input / Observation / Final Answer` loop is more reliable
   across the model zoo. ReAct replaces (rather than augments) native
   tool calls everywhere senex invokes a tool-using LLM call.
3. **Persists ReAct thoughts via the existing `ThinkingTick` /
   `save_traces` plumbing**, so the trace UX is unchanged.

The architecture review (`docs/ARCHITECTURE.md`) confirms that the
abstraction is clean: only `auditor.py:331` instantiates the concrete
client, and the rest of the codebase depends on `LLMClient` Protocol.
Type imports of `ChatMessage` / `ChatResponse` / `ToolCall*` from
`lmstudio_client` are the wider surface to rename.

## Sequencing (each phase keeps `main` green)

| Phase | Goal | Deliverable |
|---|---|---|
| **A** | Add Ollama in parallel; LMS untouched. New errors / client / lifecycle / config block; backend selector key. | Both backends pass unit tests; LMS still default. |
| **B** | ReAct loop behind a `tools.style` flag. ToolLoop branches on style. | Ollama wired to ReAct; LMS still on native tool calls. |
| **C** | Default backend → Ollama. Rewrite preflight checks. Re-record cassettes against live Ollama. | Integration + recorded tests green on Ollama. |
| **D** | Delete LMStudio. Mechanical rename across CLI, wizard, doctor, lifecycle CLI, tests, docs. | `grep -ri lmstudio` returns 0 hits. |
| **E** | Finalize `ARCHITECTURE.md`, README, CHANGELOG, example config. | Docs reflect post-migration state. |

## New Files

| Path | Purpose |
|---|---|
| `senex/ollama_errors.py` | Exception hierarchy mirroring the 8 LMS classes (rename `LMStudioError`→`OllamaError`, `LMSConnectionLost`→`OllamaConnectionLost`, etc.). All keep `error_kind` strings for the per-file recovery matrix. |
| `senex/ollama_client.py` | `LLMClient` Protocol implementation against Ollama's `/api/chat` (native) or `/v1/chat/completions` (OpenAI-compat) — selectable via `api_style` config. NDJSON streaming for native; SSE for compat. Houses the canonical `ChatMessage`/`ChatResponse`/`ToolCall*`/`LoadedModelInfo`/`ProbedCapabilities`/`ToolSchema` dataclasses. |
| `senex/ollama_lifecycle.py` | `Lifecycle` + `OllamaSDKBackend` (uses `ollama` Python pkg) + `OllamaCLIBackend` (`ollama list`/`ps`/`pull`/`run`) + `LifecycleBackendFactory`. Maps LMS lifecycle semantics onto Ollama's `keep_alive` model. |
| `senex/react_format.py` | Pure parser/renderer helpers: regexes, label tolerance, JSON fence extraction, message rendering. Unit-testable in isolation. |
| `senex/react_loop.py` | Driver: build prompt, dispatch via `ToolRegistry`, inject `Observation:`, emit `ThinkingTick` per `Thought`, validate `Final Answer` against schema. Returns a synthetic `ChatResponse` so callers are unchanged. |

## Files to Delete (Phase D)

- `senex/lmstudio_client.py`
- `senex/lmstudio_lifecycle.py`
- `senex/lmstudio_errors.py`
- `tests/unit/test_lmstudio_client.py`, `tests/unit/test_lifecycle.py`
- `tests/fixtures/lms_responses/` (entire directory)
- LMS-shaped cassettes under `tests/recorded/` (regenerated against Ollama)

## Files to Modify

| File | Change |
|---|---|
| `senex/llm_client.py:18` | `TYPE_CHECKING` import points at `senex.ollama_client`; Protocol unchanged in shape but `tools` semantics now mean "tool schemas surfaced into the ReAct prompt". |
| `senex/auditor.py:51,331,560` | Swap `LMStudioClient` import; rebuild fingerprint-pin block (`auditor.py:341-358`) against Ollama's `/api/show` digest (single canonical source — no two-surface mismatch); update `LifecycleError` import path. |
| `senex/config.py:137,196,204,288-289` | Rename `LmStudioCfg`→`OllamaCfg`; `RepoCfg.lmstudio`→`RepoCfg.ollama`; `SenexConfig.lmstudio`→`SenexConfig.ollama`; update deep-merge path. Drop `api_key`, `preset`, `strict_json_schema`. Add `keep_alive`, `format_mode`, `api_style`, `tools.style`, `lifecycle.keep_alive_on_unload`. Rename `sampling.max_tokens`→`sampling.num_predict` with one-release deprecation shim. |
| `senex/cli_audit.py:411-417` | CLI flag names unchanged (`--model`, `--auto-load`, `--auto-unload`); override key dict goes to `ollama`. |
| `senex/cli_doctor.py` | Client import swap; printed labels updated. |
| `senex/cli_wizard.py` | Replace `[lmstudio]` block emission with `[ollama]`. `list_loaded_models()` reads `/api/ps`; install candidates from `/api/tags`. |
| `senex/lifecycle_cli.py:18-21` | Import swap. Add `senex lifecycle pull <model>` (Ollama-only). |
| `senex/phases/preflight.py:222-379` | Rewrite all 6 checks: `check_ollama_reachable` (`GET /api/version`), `check_model_loaded_or_loadable` (`/api/tags` + `/api/ps`), `check_sampling_ranges`, `check_format_support` (probe `format: <schema>` payload), `check_streaming` (NDJSON), `check_lifecycle_backend` (probe `ollama` pkg + CLI). |
| `senex/phases/file_audit.py:11-12,449-509` | Swap exception types in error matrix; add `ReActParseFailed` (`error_kind="react_parse_failed"`), `ReActLoopExceeded` (`error_kind="react_loop_exceeded"`). |
| `senex/phases/crosscut.py`, `senex/cross_cutting.py:64` | Type imports move to new home (`senex.ollama_client` or `senex.llm_client`). |
| `senex/compaction.py` | Type imports updated. The Compactor sees ReAct-shaped histories: `assistant: "Thought:...\nAction:..."` + `user: "Observation:..."` pairs. Keep "summarize old user/assistant pairs" logic — it is ReAct-compatible. |
| `senex/tools/loop.py:53-59,192-410` | Branch on `config.tools.style`: `"react"` (Phase B onward, Ollama default) drives ReAct via `react_loop`; `"native"` (legacy LMS path) used during Phases A-C. After Phase D, only `"react"` is implemented. |
| `senex/tools/__init__.py`, `senex/tools/exceptions.py` | Add `ReActParseFailed`, `ReActLoopExceeded`. |
| `senex/tui/launcher.py`, `tests/tui/test_launcher.py` | Label updates "LM Studio"→"Ollama". |
| `senex.config.toml.example:28-111` | Replace `[lmstudio]` block per §"Config Schema" below. |
| `README.md`, `CHANGELOG.md` | Setup docs (`ollama serve`, `ollama pull`); breaking-change callout + migration recipe. |
| `requirements.txt` / `pyproject.toml` | Drop `lmstudio`; add `ollama` (optional). |
| `CLAUDE.md` | LMS terminology references. |

## Config Schema (post-migration)

```toml
[ollama]
base_url                       = "http://localhost:11434"   # native API; no /v1
api_style                      = "native"                   # "native" | "openai_compat"
auth_header                    = ""                         # for proxied deployments
connect_timeout                = 10
read_timeout                   = 600
http_retries                   = 3
backoff_seconds                = [5, 15, 45]
model                          = "qwen2.5-coder:32b"
keep_alive                     = "30m"                      # "0" = unload after request, "-1" = forever
allow_non_loopback             = false
context_window                 = 32768                      # → num_ctx
token_budget_pct               = 0.9
fingerprint_recheck_interval_s = 60.0
format_mode                    = "schema"                   # "schema" | "json" | "raw"

[ollama.sampling]
temperature       = 0.6
top_p             = 0.95
top_k             = 40
min_p             = 0.0
repeat_penalty    = 1.0
frequency_penalty = 0.0
presence_penalty  = 0.0
seed              = 42
seed_random       = true
num_predict       = 8192
stop              = []

[ollama.thinking]
enabled              = true
save_traces          = true       # ReAct Thought blocks persisted here
include_in_report    = false
max_thinking_tokens  = 32768

[ollama.tasks.file_audit]
temperature = 0.6
num_predict = 8192

[ollama.tasks.cross_cutting]
temperature = 0.7
num_predict = 4096

[ollama.tools]
enabled              = true
max_calls_per_file   = 5
max_result_tokens    = 2048
tool_timeout_seconds = 30
style                = "react"        # "react" today; "native" reserved

[ollama.compaction]
enabled                  = true
trigger_pct              = 0.80
target_pct               = 0.50
preserve_recent_turns    = 2
max_compactions_per_file = 3

[ollama.lifecycle]
auto_load                       = true
auto_unload                     = true
load_timeout_seconds            = 120
load_wait_timeout_seconds       = 600
load_wait_poll_interval_seconds = 5.0
runlock_dir                     = ""
keep_alive_on_unload            = "0"   # forces eviction on release
```

**Removed**: `api_key`, `preset`, `strict_json_schema`. **Added**:
`keep_alive`, `format_mode`, `api_style`, `tools.style`,
`lifecycle.keep_alive_on_unload`. **Renamed**: `sampling.max_tokens` →
`sampling.num_predict`.

## ReAct Design

### Prompt template

```
You are an audit assistant. Use this loop EXACTLY:

Thought: <one paragraph of reasoning>
Action: <tool_name>
Action Input: <single-line JSON object>

After each Action you will receive:
Observation: <tool result>

Continue Thought/Action/Observation until you have enough information.
Then emit:
Thought: <final reasoning>
Final Answer:
```json
<JSON object matching the schema below>
```

Schema:
<json schema>

Available tools:
- read_file(path: str, start: int=1, end: int|null=null) -> str
- search_code(...) -> str
- ...
```

### Parser strategy

1. **Primary**: regex extractor with named groups for `Thought | Action |
   Action Input | Observation | Final Answer`. Tolerant to leading
   whitespace, optional Markdown decoration (`**`, `###`), mixed-case
   labels, fenced or unfenced JSON.
2. **Fallback**: "last fenced JSON block" heuristic — if no `Final
   Answer:` token but a trailing ` ```json ``` ` block parses against
   the schema, treat it as the final answer.
3. **v1**: full-buffer parse after the model finishes a turn. Streaming
   (incremental Thought/Action parse with early stream close) is a
   follow-up; full-buffer is simpler to test and debug.

### Failure matrix

| Failure | Exception | `error_kind` | Recovery |
|---|---|---|---|
| Cannot parse Thought/Action/Final Answer | `ReActParseFailed` | `react_parse_failed` | One auto-correction injection (`Observation: You must use the format Thought/Action/Action Input or emit Final Answer.`); on second failure → per-file `<file>.ERROR.md`. |
| `Action: <tool>` not in registry | `UnknownToolError` (existing) | `unknown_tool` | Inject `Observation: Tool '<n>' not registered. Available: [...]`; counts toward budget. |
| `Action Input` not valid JSON | `ReActParseFailed` | `schema_invalid` | Inject `Observation: invalid JSON: <err>`. |
| Loop hits `max_calls_per_file` without `Final Answer` | `ReActLoopExceeded` | `react_loop_exceeded` | Per-file `<file>.ERROR.md`. |
| `Final Answer` JSON fails Pydantic schema | `OllamaResponseSchemaInvalid` | `schema_invalid` | Existing per-file recovery (auto-repair pass: ask model to fix once). |

### Schema reconciliation

`react_loop` extracts the fenced JSON in `Final Answer:`, runs Pydantic
`model_validate` against the per-task schema (file_audit findings,
cross_cutting summary, etc.), and on success returns a synthetic
`ChatResponse(content=<final_json_string>, tool_calls=[],
finish_reason="stop", usage=...)` so `phases/file_audit.py`,
`phases/crosscut.py`, and the renderer are unchanged.

### Token budget + compaction inside the loop

Before each `client.chat()`, recompute `count_tokens(messages)` using
`tiktoken cl100k_base` (Ollama has no remote tokenizer endpoint we want
to depend on; the approximation is good enough for budget gating). If
`prompt + num_predict > token_budget_pct * num_ctx`, trigger compaction
**before** the next chat call. Compactor preserves the system prompt
and the last `preserve_recent_turns` turns; older `assistant`/`user`
pairs (Thought/Action / Observation) get summarized.

## Lifecycle Equivalence

| LMS surface | Ollama equivalent |
|---|---|
| `Lifecycle.acquire(model_id)` | `POST /api/generate {model, prompt:"", keep_alive:"30m"}` (force load) |
| `Lifecycle.release()` | `POST /api/generate {model, prompt:"", keep_alive:"0"}` (force unload) |
| `Lifecycle.acquire_for_resume(fingerprint)` | `GET /api/show` → compare `digest` to checkpoint |
| `LMStudioSDKBackend` | `OllamaSDKBackend` (`ollama` pkg: `pull`, `list`, `ps`, `generate`) |
| `LMSCLIBackend` | `OllamaCLIBackend` (`ollama list`, `ollama ps`, `ollama pull`) |
| `is_loaded()` poll | `GET /api/ps` membership check |
| 14 lifecycle events | Same names, emitted at equivalent points |
| Fingerprint | `/api/show` → `digest` (single canonical source) |

**Asymmetry**: Ollama loads implicitly on first request and unloads
after `keep_alive`. We model this explicitly:
- `auto_load=true` → send a warm-up empty `/api/generate` with
  `keep_alive` set to span the audit duration.
- `auto_unload=true` → send `keep_alive: "0"` on release to force
  eviction.

A new `ModelPulling` event covers the case where Ollama needs to
download the model first (LMS users had to do this via the LMS app).

## Test Migration

| Test file | Action |
|---|---|
| `tests/unit/test_lmstudio_client.py` | Delete; replace with `test_ollama_client.py` (respx-mocked `localhost:11434`). |
| `tests/unit/test_lifecycle.py` | Delete; replace with `test_ollama_lifecycle.py`. |
| `tests/unit/test_lifecycle_cli.py` | Update import paths + table assertions. |
| `tests/unit/test_preflight_checks.py` | Rewrite all 6 LMS-specific checks. |
| `tests/unit/test_phases/test_file_audit_phase.py` | Inject `OllamaClient`; add `ReActParseFailed` + `ReActLoopExceeded` cases. |
| `tests/unit/test_tool_loop.py` | Most invasive: rewrite to test ReAct dispatch instead of native tool_calls. Keep budget + compaction-hook tests. |
| `tests/unit/test_compaction*.py` | Update mock client; verify compaction with ReAct-shaped histories. |
| `tests/unit/test_auditor*.py` | Client swap; new exception types. |
| `tests/unit/test_cli_wizard.py`, `test_cli_config_show.py`, `test_config*.py` | `lmstudio` → `ollama` key renames. |
| `tests/integration/test_tool_loop_with_client.py` | Wire `OllamaClient` to respx; replay a 3-turn ReAct conversation. |
| `tests/recorded/*` | Re-record cassettes against live Ollama with `temperature=0`, fixed `seed`, pinned model digest in `tests/recorded/MODEL_DIGEST`. Document the recipe in `tests/recorded/README.md`. |
| `tests/tui/test_launcher.py` | Label updates. |
| `tests/golden/*` | Renderer goldens may include backend name; refresh. |
| **NEW**: `tests/unit/test_react_format.py` | Parser tolerance, malformed inputs, fenced final-answer extraction. |
| **NEW**: `tests/unit/test_react_loop.py` | Driver behavior: dispatch → observation → continue; unknown tool injection; loop exceeded; budget exhaustion. |

## Verification Plan

1. **Static**: `ruff check senex tests`, `mypy senex` — no `lmstudio_*` references after Phase D.
2. **Unit**: `pytest tests/unit -x`.
3. **Integration**: `pytest tests/integration -x` (respx-backed).
4. **Recorded**: `OLLAMA_RECORD=1 pytest tests/recorded` (one-time), then `pytest tests/recorded` against new cassettes.
5. **CLI smoke** (with `ollama serve` + `ollama pull qwen2.5-coder:7b`):
   - `senex config show` — `[ollama]` block renders.
   - `senex doctor` — all 6 preflight checks pass.
   - `senex lifecycle status` — running model + `keep_alive`.
   - `senex audit tests/fixtures/repos/tiny_python` — completes; `events.jsonl` contains `LifecycleBegan`, `ModelLoaded` (or `ModelPulling`+`ModelLoaded`), tool-dispatch events from ReAct, `ThinkingTick` events from `Thought:` blocks. Per-file `<f>.audit.md` schema-validated; `<f>.thoughts.md` present when `save_traces=true`.
6. **Failure injection**: corrupt model name → `senex doctor` reports clearly; stop `ollama serve` → `check_ollama_reachable` fails with `connection_refused`.
7. **Resume**: kill mid-audit; `senex audit --resume` — fingerprint check passes against `/api/show` digest.

## Risk Register

1. **No `reasoning_content` stream from most Ollama models.** ReAct
   `Thought:` blocks become the sole reasoning trace — emit them as
   `ThinkingTick`. Models that DO stream `<think>` blocks (deepseek-r1,
   qwen3-thinking) can additionally emit those — additive, not
   exclusive.
2. **Small / chat-tuned models loop or skip the format.** Mitigation:
   parser fallback ("last fenced JSON block") catches no-ReAct case;
   one-shot auto-correction injection on parse failure;
   `tools.style="react_strict"` vs `"react_lenient"` toggle for users
   who want fail-fast.
3. **Ollama `format: <jsonschema>` enforcement weaker than LMS strict
   mode.** Compliance varies by model. Mitigation: post-hoc Pydantic
   validation (already the codebase pattern); add an auto-repair pass
   on schema invalid.
4. **`keep_alive` ↔ runlock semantics mismatch.** Two senex runs share
   the loaded model in Ollama; runlock is per-process. Mitigation:
   document; rely on Ollama's per-model serial request handling.
5. **Cassette regeneration is high-effort but one-time.** Mitigation:
   pin model digest, `temperature=0`, fixed seed; record-mode flag.
