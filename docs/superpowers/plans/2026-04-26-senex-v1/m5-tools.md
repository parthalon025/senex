# Milestone 5: Tool Framework

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M5 delivers the 6-tool framework that lets the auditor LLM ask for evidence: graph queries, file reads, regex grep, and semantic search. Each tool is a hardened subprocess/HTTP/file-IO callable that validates its input via a pydantic model, runs the underlying operation safely, redacts and truncates the output, and returns a `ToolResult` or `ToolError`. The bounded `ToolLoop` controller enforces the budget cap and integrates the compaction trigger hook from M3.

**Architectural intent:** Tools are the largest external attack surface — every one calls a subprocess (`npx gitnexus`, `lms`) or reads attacker-influenced filesystem paths. The `tools/safety.py` primitives are the choke point: every handler imports `validate_repo_path`, `validate_regex_pattern`, `strip_ansi`, `redact_tool_result` *before* doing any work. Get safety right once; the 6 tool handlers become thin wrappers.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration
- **Required modules from prior work:**
  - `senex/lens.py` — `Lens.tools` lists enabled tool names (M1)
  - `senex/secret_redactor.py` — `SecretRedactor.redact()` (M1)
  - `senex/events.py` — `ToolCall`, `ToolResult`, `ToolError`, `ToolBudgetExhausted` (M1)
  - `senex/lmstudio_client.py` — the client whose `_tool_loop()` hook will be wired in M3 Task 3.6 already, but the actual `ToolLoop` controller is implemented here as the standalone composable (M3)
  - `senex/config.py` — `ToolsCfg.max_calls_per_file`, `ToolsCfg.max_result_tokens`, `ToolsCfg.enabled_tools` (M1)
- **Required tools/state:**
  - `npx gitnexus` available on PATH (used by 3 of 6 tools)
  - claude-context MCP server reachable on local HTTP (for `search_code`); tool returns `ToolError(kind="unavailable")` when not reachable
  - `tests/fixtures/repos/tiny_python/` from M2 (used as test repo for `read_file`, `grep`)

## Deliverable

This milestone creates the following files:

- `senex/tools/__init__.py`
- `senex/tools/safety.py` — `validate_repo_path`, `validate_regex_pattern`, `strip_ansi`, `redact_tool_result`
- `senex/tools/registry.py` — `ToolRegistry` with `register()`, `openai_tools(enabled)`, `dispatch(call_id, name, raw_input)`
- `senex/tools/loop.py` — `ToolLoop` controller (bounded, compaction-aware)
- `senex/tools/gitnexus_query.py` — query tool
- `senex/tools/gitnexus_context.py` — context tool (the *tool*; distinct from `senex/graph_awareness.py`)
- `senex/tools/gitnexus_impact.py` — impact tool
- `senex/tools/read_file.py` — file slice reader
- `senex/tools/grep.py` — regex search with timeout
- `senex/tools/search_code.py` — claude-context MCP HTTP client
- `tests/unit/test_tools_safety.py`, `test_tool_loop.py`, `test_tool_<name>.py` (×6)
- Wired into `senex/lens.py`: `Lens.openai_tools_for(registry, config_subset)` intersection logic
- Wired into checkpoint: `compute_tool_pack_hash()` → checkpoint + RunStart event + findings.json metadata

## Downstream consumers

- **M6** Compaction integrates with `ToolLoop` via the `compaction_callback` hook (Task 6.4 wires the existing trigger hook from Task 5.8 to the M6 `Compactor`).
- **M8** FileAuditPhase passes `lens.openai_tools_for(registry, config.lmstudio.tools.enabled_tools)` into `client.chat(..., tools=...)`. The auditor uses `tool_pack_hash` for resume hash discipline.
- **M10** Live gate 13d verifies at least one `<file>.thinking.md` shows tool-call evidence (a finding quoting a tool result).

## Spec sections referenced

- §5.11 Tool framework — full tool specification (input schemas, handler contracts, output shapes)
- §5.5 step list — bounded tool loop algorithm (M3 referenced; M5 implements the standalone `ToolLoop`)
- §6.1 Configuration resolution — config can subset, not extend, lens-declared tools
- §SEC-3 / §SEC-4 — path validation + subprocess hardening (shared with M2)
- §POL — pipe-escape rules for tool results that flow into Markdown tables (relevant when tool results are quoted in reports)
- §ARCH — tool_pack_hash as resume discipline (every checkpoint/RunStart records the hash)

## Key contracts

- **`validate_repo_path(p, repo_root)`** — rejects `..`, UNC paths, drive-absolute outside repo, symlinks. Returns resolved `Path` or raises.
- **`validate_regex_pattern(p)`** — rejects > 256 chars, catastrophic backtracking patterns.
- **`strip_ansi(s)`** / **`redact_tool_result(s, redactor)`** — output sanitizers.
- **`ToolRegistry.register(name, schema, handler)`** — `schema` is OpenAI tools format; `handler` is async.
- **`ToolRegistry.openai_tools(enabled: list[str]) -> list[dict]`** — filtered subset by name.
- **`ToolRegistry.dispatch(call_id, name, raw_input) -> ToolResult|ToolError`** — validates input via pydantic, runs handler, redacts, truncates to `max_result_tokens`, returns.
- **6 tool handlers**, each with a pydantic input model:
  - `gitnexus_query`: `{query: str, limit: int (≤ 5)}`
  - `gitnexus_context`: `{symbol: str, file: str|None}`
  - `gitnexus_impact`: `{target: str, direction: "upstream"|"downstream", depth: int (≤ 3)}`
  - `read_file`: `{relpath: str, line_start: int|None, line_end: int|None}`
  - `grep`: `{pattern: str, glob: str|None, max_matches: int (≤ 20)}`
  - `search_code`: `{query: str, limit: int (≤ 5)}`
- **`ToolLoop.run(client, messages, schema, lens_tools, registry, max_calls, compaction_cb) -> ChatResponse`** — iteration cap = `max_calls + 1`; budget-exhaustion injects system message + final no-tools call.
- **`Lens.openai_tools_for(registry, config_subset)`** — intersection of lens-declared and config-enabled (config can subset, not extend).
- **`compute_tool_pack_hash(enabled_tools, registry) -> str`** — sha256 of JSON-serialized (enabled_tools + each tool's input schema).

## Watch-outs

- **`validate_repo_path` is the filesystem boundary.** Reject `..`, UNC, drive-absolute, *and* symlinks. Resolve before checking — don't trust the input string. This is the same check from M2 walker; reuse the helper if you can.
- **`validate_regex_pattern` MUST reject ReDoS.** Use the `regex` library's compile-time check or a length cap + complexity heuristic. A pattern like `(a+)+$` against attacker-controlled input is a denial-of-service.
- **All 6 tool handlers redact + truncate output.** A tool that returns 100KB of grep matches will blow the context. Truncate to `max_result_tokens` *after* redaction, *before* return.
- **`grep` uses the `regex` library, not `re`.** `regex` supports a timeout argument; `re` doesn't. Combined with the pattern validator, this is the second line of defense against ReDoS.
- **`search_code` is best-effort.** When the claude-context MCP server isn't reachable, return `ToolError(kind="unavailable")` — do NOT fail the audit.
- **Config can subset, not extend.** If the lens declares 6 tools and config sets `enabled_tools = ["read_file", "grep"]`, the model gets 2 tools. If config sets `enabled_tools = ["read_file", "grep", "shell"]`, that's an error — `shell` isn't in the lens. Enforce intersection-only.
- **`tool_pack_hash` is part of resume hash discipline.** Checkpoint, RunStart event, and findings.json all record it. Resuming with a different tool pack invalidates the run (treated like a config change).
- **Tool failures count toward the budget.** A `ToolError` is still a tool call. The model's behavior under repeated failures is on the model; the loop just enforces the cap.

## Patterns to follow

- **Tasks 5.2-5.7 share the same TDD shape** — write failing test, implement handler, run test, commit. The first one (`gitnexus_query`) is the reference; the other 5 follow the same pattern. A subagent can be dispatched per tool.
- **Pydantic input model pattern:** every tool has `class GitnexusQueryInput(BaseModel): query: str = Field(min_length=1, max_length=500); limit: int = Field(default=3, le=5)` shape. Validation errors map to `ToolError(kind="invalid_input")`.
- **Subprocess pattern (3 tools):** `subprocess.run(["npx", "gitnexus", verb, "--repo", repo_name, ...], shell=False, list-form-args, capture_output=True, timeout=...)`.
- **Compaction trigger hook (Task 5.8.2):** the loop's `compaction_callback` is wired in M6 Task 6.4 — for now the M5 implementation accepts a callable that may be a no-op stub.

## Tasks

### Task 5.1: Tool registry + safety primitives

**Files:**
- Create: `senex/tools/registry.py`
- Create: `senex/tools/safety.py`
- Create: `tests/unit/test_tools_safety.py`

- [ ] **Step 5.1.1: Failing tests for safety**:
  - `validate_repo_path(p, repo_root)` rejects `..`, UNC paths, drive-absolute outside repo, symlinks
  - `validate_regex_pattern(p)` rejects > 256 chars, catastrophic backtracking patterns (use `regex` library timeout)
  - `strip_ansi(s)` removes `\x1b]...\x07` and control chars
  - `redact_tool_result(s, redactor)` applies SecretRedactor

- [ ] **Step 5.1.2: Implement** safety helpers; pydantic input models per tool.

- [ ] **Step 5.1.3: Implement `ToolRegistry`**:
  - `register(name, schema, handler)` — schema is OpenAI tools format
  - `openai_tools(enabled: list[str]) -> list[dict]` — filtered subset
  - `dispatch(call_id, name, raw_input) -> ToolResult|ToolError` — validates input via pydantic, runs handler, redacts output, truncates to `max_result_tokens`, returns

- [ ] **Step 5.1.4: Tests** for registry round-trip + dispatch error cases.

- [ ] **Step 5.1.5: Commit** `feat(M5): tool registry + safety primitives`.

### Task 5.2-5.7: Six tool implementations

For each of the 6 tools, the same pattern:

**Files (per tool):**
- Create: `senex/tools/<name>.py`
- Test: `tests/unit/test_tool_<name>.py`

#### Task 5.2: gitnexus_query
- Pydantic input: `{query: str, limit: int (≤ 5)}`
- Handler: `subprocess.run(["npx", "gitnexus", "query", "--repo", repo_name, ...])` list form
- Output: list of (process, summary, files) tuples

#### Task 5.3: gitnexus_context
- Input: `{symbol: str, file: str|None}`
- Handler: `subprocess.run(["npx", "gitnexus", "context", "--repo", ..., "--name", symbol, ...])`
- Output: `{callers_d1, callees_d1, processes, cluster}`

#### Task 5.4: gitnexus_impact
- Input: `{target: str, direction: "upstream"|"downstream", depth: int (≤ 3)}`
- Handler: `subprocess.run(["npx", "gitnexus", "impact", ...])`
- Output: dependents grouped by depth + risk rating

#### Task 5.5: read_file
- Input: `{relpath: str, line_start: int|None, line_end: int|None}`
- Handler: `validate_repo_path` → read file → return slice
- Output: text

#### Task 5.6: grep
- Input: `{pattern: str, glob: str|None, max_matches: int (≤ 20)}`
- Handler: `validate_regex_pattern` → walk repo with glob filter → regex search with timeout → list of `{file, line, text}` matches
- Use `regex` library, not `re`, with timeout argument

#### Task 5.7: search_code (claude-context MCP)
- Input: `{query: str, limit: int (≤ 5)}`
- Handler: HTTP call to local claude-context MCP server (when available); else returns `ToolError(kind="unavailable")`
- Output: list of `{file, score, snippet}`

For each: write failing test, implement, run test, commit. Each ~30 min agent work.

### Task 5.8: Tool loop controller

**Files:**
- Create: `senex/tools/loop.py`
- Create: `tests/unit/test_tool_loop.py`

- [ ] **Step 5.8.1: Failing tests**:
  - Loop terminates when model returns content without tool_calls
  - Loop terminates with budget exhaustion message after `max_calls_per_file` calls
  - Each turn emits ToolCall, ToolResult/ToolError correctly
  - Compaction trigger hook fires when `count_tokens(messages) >= trigger_pct * ctx`
  - Tool failure (ToolError) does not break the loop; counts toward budget

- [ ] **Step 5.8.2: Implement `ToolLoop`**:
  - `async run(client, messages, schema, lens_tools, registry, max_calls, compaction_cb) -> ChatResponse`
  - Iteration cap = `max_calls + 1` (bounded iteration §spec)
  - Budget-exhaustion: after Nth call, append system message and call once more with `tools=None`

- [ ] **Step 5.8.3: Tests + commit** `feat(M5): bounded tool-call loop with budget enforcement`.

### Task 5.9: Lens → tools wiring

- [ ] **Step 5.9.1: Implement `Lens.openai_tools_for(registry, config_subset)`** — intersects lens-declared tools with config `enabled_tools` (config can subset, not extend per §6.1).

- [ ] **Step 5.9.2: Test** intersection logic.

- [ ] **Step 5.9.3: Commit** `feat(M5): lens-driven tool pack resolution`.

### Task 5.10: Tool pack hash for resume bucket

- [ ] **Step 5.10.1: Implement `compute_tool_pack_hash(enabled_tools, registry)`** — sha256 of JSON-serialized (enabled_tools + each tool's input schema).

- [ ] **Step 5.10.2: Add to checkpoint, RunStart event, findings.json metadata.**

- [ ] **Step 5.10.3: Test** hash stability + different packs produce different hashes.

- [ ] **Step 5.10.4: Commit** `feat(M5): tool_pack_hash for resume discipline`.

## Acceptance criteria

- `pytest tests/unit/test_tools_safety.py tests/unit/test_tool_loop.py tests/unit/test_tool_*.py -v` is 100% green (8 test files: safety, loop, 6 tools).
- Each of 6 tools dispatches successfully via `ToolRegistry.dispatch()` with mocked subprocess/HTTP — verified by 6 unit tests.
- Tool loop test asserts: at `max_calls_per_file + 1` calls, `ToolBudgetExhausted` is emitted and the final call is made with `tools=None`.
- Tool loop test asserts compaction callback is invoked exactly once when token threshold is crossed (synthetic large tool result).
- `Lens.openai_tools_for(registry, ["read_file", "grep", "shell"])` raises an error because `shell` is not lens-declared (intersection-only enforcement).
- `compute_tool_pack_hash([...6 tools...])` is stable across runs; subsetting to 3 tools produces a different hash.
- A test asserts `validate_regex_pattern("(a+)+$")` raises (ReDoS guard); `validate_regex_pattern("safe.*pattern")` passes.
- A test asserts `validate_repo_path("../etc/passwd", repo_root)` raises before any file read.
