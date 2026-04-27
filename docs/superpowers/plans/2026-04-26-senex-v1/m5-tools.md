# Milestone 5: Tool Framework

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M5 delivers the 6-tool framework that lets the auditor LLM ask for evidence: graph queries, file reads, regex grep, and semantic search. Each tool is a hardened subprocess/HTTP/file-IO callable that validates its input via a pydantic model, runs the underlying operation safely, redacts and truncates the output, and returns a `ToolResult` or `ToolError`. The bounded `ToolLoop` controller enforces the budget cap and integrates the compaction trigger hook from M3.

**Architectural intent.** Tools are the largest external attack surface — every one calls a subprocess (`npx gitnexus`, `lms`) or reads attacker-influenced filesystem paths. The `tools/safety.py` primitives are the choke point: every handler imports `validate_repo_path`, `validate_regex_pattern`, `strip_ansi`, `redact_tool_result` *before* doing any work. Get safety right once; the 6 tool handlers become thin wrappers.

**M5 ↔ M3 reconciliation (CRITICAL).** M3's `LMStudioClient.chat(messages, schema, tools=...)` handles **one** chat-completion round-trip: send messages, parse the response, validate, redact, return `ChatResponse`. M5's `ToolLoop` is the **iteration controller** that wraps the client across multiple round-trips: dispatch tool calls, append results, check budget, possibly compact, call the client again. Put differently:

- **M3 owns the per-call mechanics** (HTTP, SSE streaming, schema fallback, retry, fingerprint, redaction of one response).
- **M5 owns the multi-call iteration** (registry dispatch, budget cap, compaction trigger, budget-exhaustion injection).

M3 Task 3.6 wired a placeholder `_tool_loop` inside the client; M5 supersedes that with the standalone `ToolLoop` composable. M8 wires both: it constructs the `ToolLoop`, gives it a real `LMStudioClient`, and calls `await loop.run(messages, schema, lens_tools)`.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration
- **Required modules from prior work:**
  - `senex/lens.py` — `Lens.tools` lists enabled tool names (M1)
  - `senex/secret_redactor.py` — `SecretRedactor.redact()` (M1)
  - `senex/events.py` — `ToolCall`, `ToolResult`, `ToolError`, `ToolBudgetExhausted`, `CompactionTriggered`, `CompactionComplete` (M1)
  - `senex/lmstudio_client.py` — `LMStudioClient.chat(messages, schema, tools=...)` returns `ChatResponse` for **one** turn (M3)
  - `senex/config.py` — `ToolsCfg.max_calls_per_file`, `ToolsCfg.max_result_tokens`, `ToolsCfg.tool_timeout_seconds`, `ToolsCfg.enabled_tools` (M1)
- **Required tools/state:**
  - `npx gitnexus` available on PATH; absolute path resolved at preflight and pinned in `ToolContext.npx_path`
  - claude-context MCP server reachable on local HTTP (for `search_code`); tool returns `ToolError(kind="unavailable")` when not reachable
  - `tests/fixtures/repos/tiny_python/` from M2 (used as test repo for `read_file`, `grep`)

## Deliverable

This milestone creates the following files:

- `senex/tools/__init__.py` — re-exports public API
- `senex/tools/exceptions.py` — `ToolInputInvalid`, `PathOutsideRepo`, `SymlinkRefused`, `RegexTooComplex`, `RegexTimeoutExceeded`, `ToolDispatchFailed`, `ToolUnavailable`, `ToolBudgetExhausted`
- `senex/tools/safety.py` — `validate_repo_path`, `validate_regex_pattern`, `strip_ansi`, `redact_tool_result`
- `senex/tools/context.py` — `ToolContext` dataclass
- `senex/tools/registry.py` — `ToolRegistry` with `register()`, `openai_tools(enabled)`, `dispatch(call_id, name, raw_input_json)`
- `senex/tools/loop.py` — `ToolLoop` controller (bounded, compaction-aware, drives `LMStudioClient` across turns)
- `senex/tools/gitnexus_query.py` — query tool
- `senex/tools/gitnexus_context.py` — context tool (the *tool*; distinct from `senex/graph_awareness.py`)
- `senex/tools/gitnexus_impact.py` — impact tool
- `senex/tools/read_file.py` — file slice reader
- `senex/tools/grep.py` — regex search with timeout
- `senex/tools/search_code.py` — claude-context MCP HTTP client
- `senex/tools/pack_hash.py` — `compute_tool_pack_hash`
- `tests/unit/test_tools_safety.py`
- `tests/unit/test_tool_registry.py`
- `tests/unit/test_tool_loop.py`
- `tests/unit/test_tool_<name>.py` (×6: gitnexus_query, gitnexus_context, gitnexus_impact, read_file, grep, search_code)
- `tests/unit/test_lens_tool_intersection.py`
- `tests/unit/test_tool_pack_hash.py`
- `tests/integration/test_tool_loop_with_client.py` — uses real `LMStudioClient` with `respx`-mocked LMS HTTP plus real `ToolLoop`
- Wired into `senex/lens.py`: `Lens.openai_tools_for(registry, config_subset)` intersection logic
- Wired into checkpoint, RunStart event, findings.json: `compute_tool_pack_hash()` recorded everywhere

## Downstream consumers

- **M6** Compaction integrates with `ToolLoop` via `compactor.maybe_compact(messages)` (Task 6.4 supplies the real `Compactor`; M5 accepts a callable conforming to `MaybeCompact = Callable[[list[dict]], Awaitable[list[dict] | None]]`).
- **M8** FileAuditPhase constructs `ToolLoop(client=..., registry=..., repo_root=..., redactor=..., compactor=..., max_calls=...)` and calls `await loop.run(messages, schema, lens_tools)`. The auditor uses `tool_pack_hash` for resume hash discipline.
- **M10** Live gate 13d verifies at least one `<file>.thinking.md` shows tool-call evidence (a finding quoting a tool result).

## Spec sections referenced

- §5.11 Tool framework — full tool specification
  - §5.11.1 v1 tool set (6 tools, input schemas, output shapes)
  - §5.11.2 per-lens tool packs
  - §5.11.3 loop semantics (bounded iteration, budget exhaustion, post-processing)
  - §5.11.4 tool safety (path, regex, subprocess, ANSI, redaction)
- §5.5 step list — bounded tool loop algorithm (M3 referenced; M5 implements the standalone `ToolLoop`)
- §5.10 SecretRedactor — applied to every tool result
- §5.7 ANSI stripping — applied to every tool result
- §SEC-1 path-safety rule (mirrored by `validate_repo_path`)
- §SEC-2 prompt-injection trust boundary — tool results from attacker-influenced source flow back to the model; redaction + the existing `<UNTRUSTED_FILE_CONTENT>` boundary are the mitigation
- §SEC-4 subprocess hardening — list-form args, `shell=False`, absolute `npx`, validated path-like inputs
- §SEC-7 ANSI strip on textual outputs
- §6.1 Configuration resolution — config can subset, not extend, lens-declared tools (Exit 2 on extend)
- §11.1 threat model — tool rows: `read_file` traversal, exfiltration via prompt injection, subprocess injection, ReDoS, tool-loop denial, ANSI injection
- §ARCH — `tool_pack_hash` as resume discipline

## Key contracts

### Exceptions (`senex/tools/exceptions.py`)

All inherit from a base `ToolingError(SenexError)`:

| Exception | Raised by | Mapped to `ToolError.kind` |
|---|---|---|
| `ToolInputInvalid` | pydantic validation failure in `dispatch()` | `"schema_invalid"` |
| `PathOutsideRepo` | `validate_repo_path` (resolved path not under repo root) | `"path_rejected"` |
| `SymlinkRefused` | `validate_repo_path` (`p.is_symlink()` true) | `"path_rejected"` |
| `RegexTooComplex` | `validate_regex_pattern` (length cap exceeded) | `"regex_invalid"` |
| `RegexTimeoutExceeded` | `validate_regex_pattern` test-match exceeds budget; or `regex.match()` at runtime exceeds `tool_timeout_seconds` | `"regex_timeout"` |
| `ToolDispatchFailed` | uncaught handler exception (subprocess crash, IOError) | `"dispatch_failed"` |
| `ToolUnavailable` | `search_code` MCP server not reachable | `"unavailable"` |
| `ToolBudgetExhausted` | `ToolLoop` reaches `max_calls + 1` iteration | (event, not `ToolError`) |

### Safety primitives (`senex/tools/safety.py`)

```python
def validate_repo_path(p: str | Path, repo_root: Path) -> Path:
    """Resolve p, assert is_relative_to(repo_root), refuse symlinks, UNC, drive-abs outside repo.

    Raises:
        PathOutsideRepo: resolved path escapes repo_root.
        SymlinkRefused: p (or any ancestor) is a symlink.
    Returns:
        Resolved Path under repo_root.
    """

def validate_regex_pattern(
    pat: str,
    max_len: int = 256,
    timeout_seconds: float = 0.1,
) -> None:
    """Compile pattern with `regex` library; run a tiny test-match against random
    input within timeout to detect catastrophic backtracking. Cap length at max_len.

    Raises:
        RegexTooComplex: len(pat) > max_len, OR regex.compile fails, OR test-match
            exceeds timeout (catastrophic backtracking heuristic).
    """

def strip_ansi(s: str) -> str:
    """Strip C0/C1 control chars (\x00-\x08, \x0b-\x1f, \x7f) and OSC sequences
    via a single compiled regex. Preserves \\t (\\x09), \\n (\\x0a), \\r (\\x0d).
    """

def redact_tool_result(s: str, redactor: SecretRedactor) -> str:
    """Compose: strip_ansi(s) -> redactor.redact(...). Always in this order
    (redaction patterns assume ANSI-free input)."""
```

### `ToolContext` (`senex/tools/context.py`)

Dataclass passed to every handler:

```python
@dataclass(frozen=True)
class ToolContext:
    repo_root: Path        # absolute, resolved
    repo_name: str         # for `npx gitnexus --repo <name>`
    redactor: SecretRedactor
    npx_path: Path         # absolute, resolved at preflight
    tool_timeout_seconds: float = 30.0
    max_result_tokens: int = 2048
```

### `ToolRegistry` (`senex/tools/registry.py`)

```python
class ToolRegistry:
    def register(
        self,
        name: str,
        input_model: type[BaseModel],
        handler: Callable[[BaseModel, ToolContext], Awaitable[Any]],
        description: str,
    ) -> None: ...

    def openai_tools(self, enabled_names: list[str]) -> list[dict]:
        """Emit OpenAI function-calling schema list, ordered by enabled_names.
        Raises KeyError if any name is not registered (caller should pre-validate
        via Lens.openai_tools_for)."""

    async def dispatch(
        self,
        call_id: str,
        name: str,
        raw_input_json: str,
        ctx: ToolContext,
    ) -> ToolResult | ToolError:
        """Strict pipeline (every step in its own try/except producing a structured
        ToolError so the model receives well-formed feedback):
          1. Lookup handler by name -> ToolError(kind="unknown_tool") on miss.
          2. json.loads(raw_input_json) -> ToolError(kind="schema_invalid") on parse error.
          3. input_model.model_validate(...) -> ToolError(kind="schema_invalid").
          4. await handler(input_obj, ctx) under asyncio.wait_for(ctx.tool_timeout_seconds)
             -> ToolError(kind="timeout") on timeout
             -> ToolError(kind="dispatch_failed") on uncaught exception.
          5. Serialize result to text, redact via redact_tool_result(...).
          6. Truncate to ctx.max_result_tokens (token-count via tiktoken cl100k_base);
             if truncated, append "[truncated: N tokens omitted]" marker.
          7. Return ToolResult(call_id, name, content=truncated_text).
        """
```

### `ToolLoop` (`senex/tools/loop.py`) — supersedes M3 `_tool_loop`

```python
MaybeCompact = Callable[[list[dict]], Awaitable[list[dict] | None]]
# Returns the rewritten message list when compaction fires, None otherwise.

class ToolLoop:
    def __init__(
        self,
        client: LMStudioClient,
        registry: ToolRegistry,
        repo_root: Path,
        repo_name: str,
        secret_redactor: SecretRedactor,
        compactor: MaybeCompact,             # callable; M5 accepts a no-op stub, M6 supplies real one
        max_calls: int,
        tool_timeout_seconds: float = 30.0,
        max_result_tokens: int = 2048,
        npx_path: Path | None = None,
        bus: EventBus | None = None,
    ) -> None: ...

    async def run(
        self,
        messages: list[dict],
        schema: dict,
        lens_tools: list[str],
    ) -> ChatResponse:
        """Drive the model <-> tool loop until convergence or budget exhaustion.

        Algorithm (bounded by max_calls + 1 outer iterations):
          for i in range(self.max_calls + 1):
              tools_for_call = registry.openai_tools(lens_tools) if i < max_calls else None

              response = await client.chat(messages, schema, tools=tools_for_call)
              messages.append(response.assistant_message)

              if not response.tool_calls:
                  return response                            # success exit

              # Dispatch each tool call -> append `tool` messages.
              for tc in response.tool_calls:
                  bus.publish(ToolCall(call_id=tc.id, name=tc.name, ...))
                  result = await registry.dispatch(tc.id, tc.name, tc.arguments_json, ctx)
                  bus.publish(ToolResult(...) if isinstance(result, ToolResult) else ToolError(...))
                  messages.append({"role": "tool", "tool_call_id": tc.id,
                                   "content": result.content})
                  calls_made += 1

              # Compaction hook (between turns; does NOT count against max_calls).
              compacted = await self.compactor(messages)
              if compacted is not None:
                  messages = compacted

              if calls_made >= self.max_calls:
                  # Final-turn budget-exhaustion injection.
                  bus.publish(ToolBudgetExhausted(calls_made=calls_made, ...))
                  messages.append({"role": "system",
                                   "content": "Tool budget exhausted. Emit your final "
                                              "structured response now. Do not call any "
                                              "more tools."})
                  final = await client.chat(messages, schema, tools=None)
                  return final

          # Unreachable: loop bound is max_calls + 1, the last iteration always returns.
          raise AssertionError("ToolLoop bound exceeded - programmer error")
        """
```

**Note (verbatim, included in the docstring):** *"M3 owns the per-call mechanics (one HTTP round-trip, streaming, schema fallback, retry, redaction of one response). M5 owns the multi-call iteration (registry dispatch, budget cap, compaction trigger, budget-exhaustion injection)."*

### `Lens.openai_tools_for` (wired into `senex/lens.py`)

```python
def openai_tools_for(
    self,
    registry: ToolRegistry,
    config_subset: list[str] | None,
) -> list[str]:
    """Return effective tool name list for this lens.

    Rules (per spec §6.1):
      - lens_declared = self.tools (from lens/<name>/tools.toml).
      - config_subset, when None, returns lens_declared as-is.
      - config_subset, when set, MUST be a subset of lens_declared.
        Names in config_subset NOT in lens_declared raise ConfigError
        (Exit 2 - per spec §6.1 'config can subset, not extend').
      - Result is the intersection in lens-declared order.

    Logs a warning for each name in config_subset that is NOT in lens_declared
    BEFORE raising (so the user sees all violations, not just the first).
    """
```

### `compute_tool_pack_hash` (`senex/tools/pack_hash.py`)

```python
def compute_tool_pack_hash(enabled_tools: list[str], registry: ToolRegistry) -> str:
    """sha256 of JSON-serialized [(name, registry.input_schema(name)) for name
    in sorted(enabled_tools)].

    Stable across runs for the same (names, schemas). Sensitive to:
      - adding/removing tools from enabled_tools
      - changing any tool's input schema (e.g. raising max length cap)
    """
```

Used in `Checkpoint`, `RunStart` event, `findings.json` metadata. Resuming with a different `tool_pack_hash` invalidates the run unless `--allow-mixed-resume` is passed (M10).

## Watch-outs

- **`validate_repo_path` is the filesystem boundary.** Reject `..`, UNC, drive-absolute, *and* symlinks (any ancestor that is a symlink). Resolve before checking — don't trust the input string. This is the same check from M2 walker; reuse the helper.
- **`validate_regex_pattern` MUST reject ReDoS.** Use the `regex` library's compile + a test-match against random input within `timeout_seconds=0.1`. Length cap at 256. A pattern like `(a+)+$` against attacker-controlled input is a denial-of-service.
- **All 6 tool handlers redact + truncate output.** A tool that returns 100KB of grep matches will blow the context. Order: `strip_ansi` -> `redact` -> `truncate`. Truncation is token-based (tiktoken cl100k_base), not character-based.
- **`grep` uses the `regex` library, not `re`.** `regex` supports a timeout argument; `re` doesn't. Combined with the pattern validator, this is the second line of defense against ReDoS.
- **`search_code` is best-effort.** When the claude-context MCP server isn't reachable, return `ToolError(kind="unavailable")` — do NOT fail the audit. Preflight (M4/M10) marks `search_code` as removed from the lens's enabled_tools when unreachable; the runtime check is defense in depth.
- **Config can subset, not extend.** If the lens declares 6 tools and config sets `enabled_tools = ["read_file", "grep"]`, the model gets 2 tools. If config sets `enabled_tools = ["read_file", "grep", "shell"]`, it's an error — `shell` isn't in the lens. Enforce intersection-only at `Lens.openai_tools_for` (Exit 2 with warning logs for ALL violations before raising).
- **`tool_pack_hash` is part of resume hash discipline.** Checkpoint, RunStart event, and findings.json all record it. Resuming with a different tool pack invalidates the run (treated like a config change).
- **Tool failures count toward the budget.** A `ToolError` is still a tool call. The model's behavior under repeated failures is on the model; the loop just enforces the cap.
- **Compaction calls do NOT count against `max_calls`.** The compaction hook fires between turns; it can rewrite `messages` but does not consume the budget.
- **Subprocess args are list-form, `shell=False`, absolute `npx_path`.** Never interpolate user input into a shell command. Validate path-like inputs against `^[A-Za-z0-9_./\\-]+$` per §5.11.4.
- **Tool result ordering in events.** For each `tool_calls` batch from the model, emit one `ToolCall` *before* dispatching, then one `ToolResult` OR `ToolError` *after* dispatch — in the same order as the model's `tool_calls` array. Test verifies the captured event stream.

## Patterns to follow

- **Tasks 5.2-5.7 share TDD shape but are NOT identical** — input/output models, error mappings, edge cases, and security guards differ per tool. Each has its own task block below with explicit test cases.
- **Pydantic input model pattern:** every tool has a strict `BaseModel` with `model_config = ConfigDict(extra="forbid")`, `Field` constraint annotations, and post-validators where path/regex hardening applies (so invalid input fails *before* the handler runs).
- **Subprocess pattern (3 tools):** `await asyncio.create_subprocess_exec(ctx.npx_path, "gitnexus", verb, "--repo", ctx.repo_name, ...)` with `stdout=PIPE`, `stderr=PIPE`, wrapped in `asyncio.wait_for(timeout=ctx.tool_timeout_seconds)`. List-form args. Never `shell=True`. Validate every path-like arg against `^[A-Za-z0-9_./\\-]+$`.
- **Compaction trigger hook (Task 5.8.2):** the loop's `compactor` callable is wired in M6 Task 6.4 — for now M5 uses a no-op stub that returns `None`. The integration test confirms the hook fires.

## Tasks

### Task 5.1: Tool registry + safety primitives + exceptions

**Files:**
- Create: `senex/tools/__init__.py`
- Create: `senex/tools/exceptions.py`
- Create: `senex/tools/safety.py`
- Create: `senex/tools/context.py`
- Create: `senex/tools/registry.py`
- Create: `tests/unit/test_tools_safety.py`
- Create: `tests/unit/test_tool_registry.py`

- [x] **Step 5.1.1: Failing tests — exceptions.** In `tests/unit/test_tools_safety.py`:
  - `test_exception_hierarchy_is_tooling_error` — every named exception inherits from `ToolingError` and `SenexError`.
  - `test_exceptions_carry_structured_message` — each accepts a `message: str` and exposes `.kind` matching the table in Key contracts.

- [x] **Step 5.1.2: Failing tests — `validate_repo_path`.** In `tests/unit/test_tools_safety.py`:
  - `test_rejects_dotdot_traversal` — `validate_repo_path("../etc/passwd", repo_root)` raises `PathOutsideRepo`.
  - `test_rejects_unc_path` — `validate_repo_path("\\\\server\\share\\file", repo_root)` raises `PathOutsideRepo` (Windows-only test gated by `sys.platform == "win32"`).
  - `test_rejects_drive_absolute_outside_repo` — `validate_repo_path("C:/Windows/System32/cmd.exe", repo_root=Path("E:/repo"))` raises `PathOutsideRepo`.
  - `test_rejects_symlink_target` — create a symlink inside `repo_root` pointing to `repo_root/../outside.txt`; `validate_repo_path("link", repo_root)` raises `SymlinkRefused`.
  - `test_rejects_symlinked_ancestor` — `repo_root/dir` is a symlink to elsewhere; `validate_repo_path("dir/file.py", repo_root)` raises `SymlinkRefused`.
  - `test_accepts_valid_relative_path` — `validate_repo_path("src/main.py", repo_root)` returns the resolved `Path`.
  - `test_accepts_absolute_path_inside_repo` — absolute path under `repo_root` returns resolved `Path`.

- [x] **Step 5.1.3: Failing tests — `validate_regex_pattern`.** In `tests/unit/test_tools_safety.py`:
  - `test_rejects_oversize_pattern` — pattern of 257 chars raises `RegexTooComplex`.
  - `test_rejects_catastrophic_backtracking` — `validate_regex_pattern("(a+)+$")` raises `RegexTooComplex` or `RegexTimeoutExceeded` (test-match against `"a"*30 + "X"` exceeds 0.1s).
  - `test_accepts_safe_pattern` — `validate_regex_pattern("safe.*pattern")` returns None.
  - `test_rejects_uncompilable_pattern` — `validate_regex_pattern("[unclosed")` raises `RegexTooComplex`.

- [x] **Step 5.1.4: Failing tests — `strip_ansi` and `redact_tool_result`.** In `tests/unit/test_tools_safety.py`:
  - `test_strip_ansi_removes_csi` — input `"\x1b[31mred\x1b[0m"` returns `"red"`.
  - `test_strip_ansi_removes_osc` — input `"\x1b]0;title\x07after"` returns `"after"`.
  - `test_strip_ansi_preserves_whitespace` — `"\tline1\nline2\r\n"` is unchanged.
  - `test_redact_tool_result_chains_strip_then_redact` — input with both ANSI and an AWS key returns ANSI-free, redacted string in that order.

- [x] **Step 5.1.5: Implement** `senex/tools/exceptions.py`, `senex/tools/safety.py`, `senex/tools/context.py`. Run tests -> green.

- [x] **Step 5.1.6: Failing tests — `ToolRegistry`.** In `tests/unit/test_tool_registry.py`:
  - `test_register_then_openai_tools_returns_schema` — register a noop tool; `openai_tools(["noop"])` returns `[{"type": "function", "function": {"name": "noop", ...}}]`.
  - `test_openai_tools_unknown_name_raises` — `openai_tools(["does_not_exist"])` raises `KeyError`.
  - `test_openai_tools_orders_by_enabled_names` — register `a` then `b`; `openai_tools(["b", "a"])` returns `[b, a]`.
  - `test_dispatch_validates_input` — bad JSON arguments -> `ToolError(kind="schema_invalid")`.
  - `test_dispatch_runs_handler_and_redacts` — handler returns string containing AWS key; result content has `[REDACTED:aws_access_key]`.
  - `test_dispatch_truncates_oversize_output` — handler returns 10000-token string with `max_result_tokens=100`; result content ends with `[truncated: ... tokens omitted]` and is <= 100 tokens + marker.
  - `test_dispatch_handler_timeout` — handler sleeps 5s with `tool_timeout_seconds=0.1` -> `ToolError(kind="timeout")`.
  - `test_dispatch_handler_exception` — handler raises `RuntimeError` -> `ToolError(kind="dispatch_failed")` with redacted message.
  - `test_dispatch_unknown_tool` — `dispatch(call_id, "nope", "{}", ctx)` returns `ToolError(kind="unknown_tool")`.

- [x] **Step 5.1.7: Implement `ToolRegistry`** in `senex/tools/registry.py`. Run tests -> green.

- [x] **Step 5.1.8: Verify.**
  - Run `pytest tests/unit/test_tools_safety.py tests/unit/test_tool_registry.py -v` -> all pass.
  - Run `mypy senex/tools/ --strict` -> no errors.

- [x] **Step 5.1.9: Commit** `feat(M5): tool registry + safety primitives + exception hierarchy`.

### Task 5.2: gitnexus_query tool

**Files:**
- Create: `senex/tools/gitnexus_query.py`
- Create: `tests/unit/test_tool_gitnexus_query.py`

**Pydantic models:**
```python
class GitnexusQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)

class GitnexusQueryHit(BaseModel):
    process: str
    summary: str
    files: list[str]

class GitnexusQueryOutput(BaseModel):
    hits: list[GitnexusQueryHit]
```

**Handler signature:** `async def gitnexus_query_handler(inp: GitnexusQueryInput, ctx: ToolContext) -> GitnexusQueryOutput`.

**Subprocess invocation:**
```python
proc = await asyncio.create_subprocess_exec(
    str(ctx.npx_path), "gitnexus", "query",
    "--repo", ctx.repo_name,
    "--query", inp.query,
    "--limit", str(inp.limit),
    "--json",
    stdout=PIPE, stderr=PIPE,
)
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=ctx.tool_timeout_seconds)
```

**Error mapping:**
- pydantic validation failure -> `ToolError(kind="schema_invalid")` (handled in registry).
- `proc.returncode != 0` -> `ToolDispatchFailed` -> `ToolError(kind="dispatch_failed", message=stderr_redacted)`.
- `asyncio.TimeoutError` -> `ToolError(kind="timeout")`.
- JSON parse failure on stdout -> `ToolDispatchFailed` -> `ToolError(kind="dispatch_failed", message="malformed gitnexus output")`.

- [x] **Step 5.2.1: Failing tests** in `tests/unit/test_tool_gitnexus_query.py` (mock `asyncio.create_subprocess_exec` via `unittest.mock.patch`):
  - `test_happy_path_returns_hits` — mocked subprocess returns valid JSON; output parses into `GitnexusQueryOutput`; result content includes process names.
  - `test_invalid_input_query_too_long` — `query="x" * 501` -> `ToolInputInvalid` raised by pydantic; registry returns `ToolError(kind="schema_invalid")`.
  - `test_invalid_input_limit_too_high` — `limit=10` -> `schema_invalid`.
  - `test_subprocess_failure_nonzero_exit` — mocked subprocess returns rc=1, stderr `"graph not indexed"` -> `ToolError(kind="dispatch_failed")` with redacted stderr.
  - `test_subprocess_timeout` — mocked subprocess hangs; `tool_timeout_seconds=0.1` -> `ToolError(kind="timeout")`.
  - `test_empty_results_returns_empty_hits` — subprocess stdout `'{"hits": []}'` -> `ToolResult` with `"hits": []`.
  - `test_oversize_result_truncated` — subprocess returns 10000-token JSON -> result content ends with truncation marker.

- [x] **Step 5.2.2: Implement** `senex/tools/gitnexus_query.py`. Register with `ToolRegistry` in module-level `register_gitnexus_query(registry)` helper.

- [x] **Step 5.2.3: Run** `pytest tests/unit/test_tool_gitnexus_query.py -v` -> green. Verify expected output: `7 passed`.

- [x] **Step 5.2.4: Commit** `feat(M5): gitnexus_query tool`.

### Task 5.3: gitnexus_context tool

**Files:**
- Create: `senex/tools/gitnexus_context.py`
- Create: `tests/unit/test_tool_gitnexus_context.py`

**Pydantic models:**
```python
class GitnexusContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$")
    file: str | None = Field(default=None, max_length=500)

    @field_validator("file")
    @classmethod
    def validate_file_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not re.fullmatch(r"^[A-Za-z0-9_./\\-]+$", v):
            raise ValueError("file must be a relative path matching ^[A-Za-z0-9_./\\\\-]+$")
        return v

class GitnexusContextOutput(BaseModel):
    callers_d1: list[str]
    callees_d1: list[str]
    processes: list[str]
    cluster: str | None
```

**Handler signature:** `async def gitnexus_context_handler(inp: GitnexusContextInput, ctx: ToolContext) -> GitnexusContextOutput`.

**Subprocess invocation:** `npx gitnexus context --repo {ctx.repo_name} --name {inp.symbol} [--file {inp.file}] --json`. Same async pattern as 5.2.

**Error mapping:** identical to 5.2.

- [x] **Step 5.3.1: Failing tests** in `tests/unit/test_tool_gitnexus_context.py`:
  - `test_happy_path_returns_360_view` — mocked subprocess returns `{"callers_d1": [...], "callees_d1": [...], "processes": [...], "cluster": "..."}`; result parses correctly.
  - `test_invalid_symbol_with_special_chars` — `symbol="foo;rm -rf /"` -> `schema_invalid` (regex pattern fails).
  - `test_invalid_file_with_dotdot` — `file="../etc/passwd"` -> `schema_invalid` (note: this is the *input* validator; the file is not opened, the gitnexus subprocess sees it).
  - `test_subprocess_symbol_not_found` — rc=2, stderr `"no symbol named X"` -> `ToolError(kind="dispatch_failed")`.
  - `test_optional_file_omitted` — `file=None` -> subprocess called without `--file` flag.
  - `test_oversize_result_truncated` — large callers list -> truncation marker present.

- [x] **Step 5.3.2: Implement** `senex/tools/gitnexus_context.py`.

- [x] **Step 5.3.3: Run** `pytest tests/unit/test_tool_gitnexus_context.py -v` -> green. Expected: `6 passed`.

- [x] **Step 5.3.4: Commit** `feat(M5): gitnexus_context tool`.

### Task 5.4: gitnexus_impact tool

**Files:**
- Create: `senex/tools/gitnexus_impact.py`
- Create: `tests/unit/test_tool_gitnexus_impact.py`

**Pydantic models:**
```python
class GitnexusImpactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$")
    direction: Literal["upstream", "downstream"] = "upstream"
    depth: int = Field(default=1, ge=1, le=3)

class ImpactDependent(BaseModel):
    name: str
    depth: int
    confidence: float = Field(ge=0.0, le=1.0)

class GitnexusImpactOutput(BaseModel):
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    dependents_by_depth: dict[int, list[ImpactDependent]]
```

**Handler signature:** `async def gitnexus_impact_handler(inp: GitnexusImpactInput, ctx: ToolContext) -> GitnexusImpactOutput`.

**Subprocess invocation:** `npx gitnexus impact --repo {ctx.repo_name} --target {inp.target} --direction {inp.direction} --depth {inp.depth} --json`.

**Error mapping:** identical to 5.2 + 5.3.

- [x] **Step 5.4.1: Failing tests** in `tests/unit/test_tool_gitnexus_impact.py`:
  - `test_happy_path_upstream_d1` — mocked subprocess returns `risk_level=HIGH`, dependents at d=1; result parses.
  - `test_invalid_direction_value` — `direction="sideways"` -> `schema_invalid` (Literal violation).
  - `test_invalid_depth_too_high` — `depth=4` -> `schema_invalid`.
  - `test_invalid_target_special_chars` — `target="foo'bar"` -> `schema_invalid`.
  - `test_subprocess_failure` — rc=1, stderr `"target not in graph"` -> `dispatch_failed` with redacted stderr.
  - `test_empty_dependents_d2_d3` — output has only d=1 dependents, d=2/3 missing -> parses with empty lists.

- [x] **Step 5.4.2: Implement** `senex/tools/gitnexus_impact.py`.

- [x] **Step 5.4.3: Run** `pytest tests/unit/test_tool_gitnexus_impact.py -v` -> green. Expected: `6 passed`.

- [x] **Step 5.4.4: Commit** `feat(M5): gitnexus_impact tool`.

### Task 5.5: read_file tool

**Files:**
- Create: `senex/tools/read_file.py`
- Create: `tests/unit/test_tool_read_file.py`

**Pydantic models:**
```python
class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str = Field(min_length=1, max_length=500)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> "ReadFileInput":
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end must be >= line_start")
            if self.line_end - self.line_start > 2000:
                raise ValueError("line range cannot exceed 2000 lines")
        return self

class ReadFileOutput(BaseModel):
    relpath: str
    line_start: int
    line_end: int
    content: str
```

**Handler signature:** `async def read_file_handler(inp: ReadFileInput, ctx: ToolContext) -> ReadFileOutput`.

**Implementation outline:**
1. `resolved = validate_repo_path(inp.relpath, ctx.repo_root)` — raises `PathOutsideRepo` / `SymlinkRefused`.
2. `if not resolved.is_file(): raise ToolDispatchFailed("not a regular file")`.
3. `if resolved.stat().st_size > 5_000_000: raise ToolDispatchFailed("file too large to read")` — defensive cap (5MB) above the walker's 512KB to allow occasional large reads.
4. Read text (`encoding="utf-8", errors="replace"`).
5. Slice by line range; default `line_start=1`, `line_end=min(len(lines), line_start+200)` if both omitted.
6. Return `ReadFileOutput`.

**Error mapping:**
- `PathOutsideRepo`/`SymlinkRefused` -> `ToolError(kind="path_rejected")`.
- `FileNotFoundError` -> `ToolError(kind="dispatch_failed", message="file not found")`.
- `UnicodeDecodeError` -> handled by `errors="replace"`; no error.
- `ToolDispatchFailed` -> `ToolError(kind="dispatch_failed")`.

- [x] **Step 5.5.1: Failing tests** in `tests/unit/test_tool_read_file.py` (uses real fs with `tests/fixtures/repos/tiny_python/`):
  - `test_happy_path_full_file` — `relpath="src/main.py"`; result content matches file bytes.
  - `test_happy_path_line_range` — `line_start=10, line_end=20` -> exactly 11 lines.
  - `test_invalid_path_traversal` — `relpath="../etc/passwd"` -> `path_rejected`.
  - `test_invalid_symlink` — symlink fixture file -> `path_rejected`.
  - `test_invalid_line_range_inverted` — `line_start=20, line_end=10` -> `schema_invalid`.
  - `test_invalid_line_range_too_wide` — `line_start=1, line_end=5000` -> `schema_invalid` (cap is 2000).
  - `test_file_not_found` — `relpath="does_not_exist.py"` -> `dispatch_failed`.
  - `test_oversize_file_truncated` — file > `max_result_tokens` -> result content ends with truncation marker.

- [x] **Step 5.5.2: Implement** `senex/tools/read_file.py`.

- [x] **Step 5.5.3: Run** `pytest tests/unit/test_tool_read_file.py -v` -> green. Expected: `8 passed`.

- [x] **Step 5.5.4: Commit** `feat(M5): read_file tool with path-safety enforcement`.

### Task 5.6: grep tool

**Files:**
- Create: `senex/tools/grep.py`
- Create: `tests/unit/test_tool_grep.py`

**Pydantic models:**
```python
class GrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=256)
    glob: str | None = Field(default=None, max_length=200)
    max_matches: int = Field(default=20, ge=1, le=20)

    @field_validator("pattern")
    @classmethod
    def validate_pattern_complexity(cls, v: str) -> str:
        validate_regex_pattern(v)  # raises RegexTooComplex / RegexTimeoutExceeded
        return v

    @field_validator("glob")
    @classmethod
    def validate_glob(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not re.fullmatch(r"^[A-Za-z0-9_./\\\-\*\?\[\]\{\}]+$", v):
            raise ValueError("glob contains invalid characters")
        return v

class GrepMatch(BaseModel):
    file: str
    line: int
    text: str

class GrepOutput(BaseModel):
    matches: list[GrepMatch]
    truncated: bool
```

**Handler signature:** `async def grep_handler(inp: GrepInput, ctx: ToolContext) -> GrepOutput`.

**Implementation outline (uses `regex` library, NOT stdlib `re`):**
1. `compiled = regex.compile(inp.pattern, flags=regex.MULTILINE)` — already validated, but compile here.
2. Walk `ctx.repo_root` filtered by `inp.glob` (use `pathlib.Path.rglob` if glob set; otherwise default include extensions from M2 walker).
3. For each candidate file: skip symlinks (mirror walker), skip files > 1MB, read text with `errors="replace"`.
4. Run `compiled.search(line, timeout=0.05)` per line — `regex.TimeoutError` skips that file with a logged warning.
5. Collect matches up to `inp.max_matches`; set `truncated=True` if more files remain.
6. Return `GrepOutput`.

**Error mapping:**
- pydantic validators (`RegexTooComplex`, glob invalid) -> `schema_invalid`.
- `regex.TimeoutError` during scan -> individual file skipped, logged; not a `ToolError`.
- `OSError` walking -> `dispatch_failed`.

- [ ] **Step 5.6.1: Failing tests** in `tests/unit/test_tool_grep.py` (uses `tests/fixtures/repos/tiny_python/`):
  - `test_happy_path_finds_matches` — `pattern="def\\s+\\w+"` -> list of function defs.
  - `test_glob_filter_narrows_search` — `glob="**/*.py"` only matches Python files.
  - `test_max_matches_caps_results` — `max_matches=2` returns exactly 2 even when more exist; `truncated=True`.
  - `test_invalid_pattern_oversize` — `pattern="x" * 257` -> `schema_invalid`.
  - `test_invalid_pattern_redos` — `pattern="(a+)+$"` -> `schema_invalid` (RegexTooComplex/Timeout).
  - `test_invalid_glob_special_chars` — `glob="foo;rm -rf /"` -> `schema_invalid`.
  - `test_runtime_timeout_skips_file` — synthetic large file with adversarial content; per-line timeout fires; other files still scanned. Verify warning logged, result returned.
  - `test_no_matches_returns_empty_list` — `pattern="ZZZNEVERMATCHESZZZ"` -> `matches=[], truncated=False`.

- [ ] **Step 5.6.2: Implement** `senex/tools/grep.py` using `regex` library with `timeout` argument.

- [ ] **Step 5.6.3: Run** `pytest tests/unit/test_tool_grep.py -v` -> green. Expected: `8 passed`.

- [ ] **Step 5.6.4: Commit** `feat(M5): grep tool with regex-library timeouts and ReDoS guards`.

### Task 5.7: search_code (claude-context MCP) tool

**Files:**
- Create: `senex/tools/search_code.py`
- Create: `tests/unit/test_tool_search_code.py`

**Pydantic models:**
```python
class SearchCodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)

class SearchCodeHit(BaseModel):
    file: str
    score: float = Field(ge=0.0, le=1.0)
    snippet: str

class SearchCodeOutput(BaseModel):
    hits: list[SearchCodeHit]
```

**Handler signature:** `async def search_code_handler(inp: SearchCodeInput, ctx: ToolContext) -> SearchCodeOutput`.

**Implementation outline:**
1. `client = httpx.AsyncClient(base_url=os.environ.get("CLAUDE_CONTEXT_URL", "http://localhost:8765"), timeout=ctx.tool_timeout_seconds)`.
2. `resp = await client.post("/search_code", json={"query": inp.query, "limit": inp.limit})`.
3. On `ConnectError` / `ConnectTimeout` -> raise `ToolUnavailable("claude-context MCP not reachable")`.
4. `resp.raise_for_status()` -> on 5xx, raise `ToolDispatchFailed`.
5. Parse JSON -> validate against `SearchCodeOutput`.

**Error mapping:**
- `ToolUnavailable` -> `ToolError(kind="unavailable")`.
- `httpx.HTTPStatusError` (5xx) -> `ToolError(kind="dispatch_failed")`.
- `httpx.HTTPStatusError` (4xx) -> `ToolError(kind="dispatch_failed", message=...)` (likely a malformed query the model can adapt to).
- `pydantic.ValidationError` on response -> `ToolError(kind="dispatch_failed", message="malformed MCP response")`.

- [ ] **Step 5.7.1: Failing tests** in `tests/unit/test_tool_search_code.py` (mocks via `respx`):
  - `test_happy_path_returns_hits` — mocked HTTP returns 3 hits; result parses.
  - `test_invalid_query_too_long` — `query="x" * 501` -> `schema_invalid`.
  - `test_invalid_limit_zero` — `limit=0` -> `schema_invalid`.
  - `test_mcp_unreachable_returns_unavailable` — `respx` raises `ConnectError` -> `ToolError(kind="unavailable")`.
  - `test_mcp_5xx_returns_dispatch_failed` — `respx` returns 503 -> `ToolError(kind="dispatch_failed")`.
  - `test_malformed_response_returns_dispatch_failed` — `respx` returns 200 with `{"unexpected": "shape"}` -> `dispatch_failed` with message "malformed MCP response".
  - `test_empty_results_returns_empty_hits` — `respx` returns `{"hits": []}` -> `hits=[]`.

- [ ] **Step 5.7.2: Implement** `senex/tools/search_code.py`.

- [ ] **Step 5.7.3: Run** `pytest tests/unit/test_tool_search_code.py -v` -> green. Expected: `7 passed`.

- [ ] **Step 5.7.4: Commit** `feat(M5): search_code MCP tool with unavailable fallback`.

### Task 5.8: ToolLoop controller (multi-call iteration over LMStudioClient)

**Files:**
- Create: `senex/tools/loop.py`
- Create: `tests/unit/test_tool_loop.py`
- Create: `tests/integration/test_tool_loop_with_client.py`

**M5 ↔ M3 reconciliation (verbatim, included in `ToolLoop` docstring):**

> *"M3's `LMStudioClient.chat(messages, schema, tools=...)` handles ONE chat-completion round-trip. M5's `ToolLoop` is the iteration controller that wraps the client across multiple round-trips. M3 owns the per-call mechanics; M5 owns the multi-call iteration."*

**Algorithm (from Key contracts; events emitted in this exact order):**

For each outer iteration `i` in `range(max_calls + 1)`:
1. `tools_arg = registry.openai_tools(lens_tools) if i < max_calls else None`.
2. `response = await client.chat(messages, schema, tools=tools_arg)`.
3. Append assistant message to history.
4. **If `not response.tool_calls`**: return `response`. (Success exit.)
5. For each `tc` in `response.tool_calls` (in order):
   - Emit `ToolCall(call_id=tc.id, name=tc.name, arguments=tc.arguments)`.
   - `result = await registry.dispatch(tc.id, tc.name, tc.arguments_json, ctx)`.
   - Emit `ToolResult(...)` if `result` is `ToolResult`, else `ToolError(...)`.
   - Append `{"role": "tool", "tool_call_id": tc.id, "content": result.content}`.
   - `calls_made += 1`.
6. **Compaction hook (between turns; does NOT increment `calls_made`):**
   - `compacted = await self.compactor(messages)`.
   - If `compacted is not None`: emit `CompactionTriggered` (already emitted by compactor) and replace `messages = compacted`.
7. **If `calls_made >= max_calls`:**
   - Emit `ToolBudgetExhausted(calls_made=calls_made, path=...)`.
   - Append system message: `"Tool budget exhausted. Emit your final structured response now. Do not call any more tools."`
   - `final = await client.chat(messages, schema, tools=None)`.
   - Return `final` regardless of whether content matches schema (the no-tools call is the model's last chance).

**Bound:** `max_calls + 1` outer iterations total (one extra iteration reserved for the budget-exhaustion final turn).

- [ ] **Step 5.8.1: Failing unit tests** in `tests/unit/test_tool_loop.py` (mocks `LMStudioClient.chat` via `AsyncMock`):
  - `test_terminates_when_no_tool_calls` — first `chat()` returns content without tool_calls -> loop returns immediately, 1 chat call total.
  - `test_dispatches_tool_calls_and_appends_results` — first `chat()` returns 2 tool_calls; second `chat()` returns content. Verify: 2 `ToolCall` events, 2 `ToolResult` events (in tool_calls order), 2 `tool` messages appended, 2 chat calls.
  - `test_event_order_per_turn` — capture `bus` events; assert order: `ToolCall(a)` -> `ToolResult(a)` -> `ToolCall(b)` -> `ToolResult(b)`.
  - `test_budget_exhaustion_at_max_calls` — `max_calls=2`; mock returns tool_calls 3 times, then content. Expect: 2 dispatched calls, `ToolBudgetExhausted` emitted, final `chat()` called with `tools=None`, system message appended. Total chat calls: 3 (max_calls + 1).
  - `test_budget_exhaustion_returns_final_response_regardless` — even if final no-tools call returns malformed content, `run()` returns it (the loop bound is enforced; schema validation is the caller's problem).
  - `test_tool_failure_does_not_break_loop` — registry returns `ToolError`; loop appends it as `tool` message, increments `calls_made`, continues.
  - `test_compaction_hook_does_not_count_against_budget` — `compactor` returns rewritten messages on iteration 2; `calls_made` unaffected. Verify the rewritten messages are passed to the next `chat()`.
  - `test_compaction_hook_no_op_returns_none` — `compactor` returns `None`; `messages` unchanged; loop proceeds.
  - `test_unknown_tool_returns_dispatch_error` — model emits `tool_calls=[{name: "shell"}]` but `shell` not registered; `ToolError(kind="unknown_tool")` in event stream + `tool` message; loop continues.

- [ ] **Step 5.8.2: Implement `ToolLoop`** in `senex/tools/loop.py`. Constructor signature exactly per Key contracts. Algorithm exactly per the bullet list above. Include the M5↔M3 reconciliation note verbatim in the `run()` docstring.

- [ ] **Step 5.8.3: Run** unit tests `pytest tests/unit/test_tool_loop.py -v` -> green. Expected: `9 passed`.

- [ ] **Step 5.8.4: Failing integration test** in `tests/integration/test_tool_loop_with_client.py`:
  - `test_real_client_with_real_loop_respects_budget` — uses a **real** `LMStudioClient` with `respx`-mocked LMS HTTP responses, a **real** `ToolRegistry` with `read_file` registered, a **real** `ToolLoop`. Mock LMS responds with: turn 1 -> `read_file("a.py")`, turn 2 -> `read_file("b.py")`, turn 3 -> `read_file("c.py")`, turn 4 (no tools) -> final content. With `max_calls=2`, assert: exactly 3 LMS HTTP calls (2 with tools + 1 final no-tools), `ToolBudgetExhausted` emitted, final response returned.
  - `test_real_client_terminates_early_on_no_tool_calls` — mock LMS responds with content immediately. Assert: 1 LMS HTTP call total; no `ToolBudgetExhausted`.

- [ ] **Step 5.8.5: Run** `pytest tests/integration/test_tool_loop_with_client.py -v` -> green. Expected: `2 passed`.

- [ ] **Step 5.8.6: Commit** `feat(M5): bounded ToolLoop with budget enforcement and compaction hook`.

### Task 5.9: Lens -> tools wiring (intersection rule)

**Files:**
- Modify: `senex/lens.py` (add `Lens.openai_tools_for`)
- Create: `tests/unit/test_lens_tool_intersection.py`

**Per spec §6.1:** config can SUBSET but cannot EXTEND lens-declared tools. Names in config that are not in the lens cause Exit 2 with a warning logged for EVERY violation before raising (so the user sees all problems at once).

- [ ] **Step 5.9.1: Failing tests** in `tests/unit/test_lens_tool_intersection.py`:
  - `test_config_none_returns_lens_tools_unchanged` — `lens.tools=[A,B,C]`, `config=None` -> result `[A,B,C]`.
  - `test_config_subset_returns_intersection_in_lens_order` — `lens=[A,B,C]`, `config=[B,A]` -> result `[A,B]` (lens order).
  - `test_config_with_extension_raises_with_warnings` — `lens=[A,B,C]`, `config=[A,X,Y]` -> raises `ConfigError`. Verify `caplog` captured TWO warnings (for X and Y) before the raise.
  - `test_config_empty_list_returns_empty` — `lens=[A,B,C]`, `config=[]` -> result `[]` (empty subset is valid; tools disabled entirely).
  - `test_intersection_preserves_lens_order_not_config_order` — `lens=[A,B,C]`, `config=[C,A,B]` -> result `[A,B,C]`.

- [ ] **Step 5.9.2: Implement `Lens.openai_tools_for(registry, config_subset)`** per Key contracts. Verify ALL extensions are warning-logged before raising.

- [ ] **Step 5.9.3: Run** `pytest tests/unit/test_lens_tool_intersection.py -v` -> green. Expected: `5 passed`.

- [ ] **Step 5.9.4: Commit** `feat(M5): lens-driven tool pack resolution with subset-only enforcement`.

### Task 5.10: tool_pack_hash for resume bucket

> **Producer/consumer cross-ref (R6):** This task computes `tool_pack_hash`. It is consumed by `senex.checkpoint.Checkpoint` (M1 Task 1.7), written into `findings.json` (M7 Task 7.3), and included in the `RunStart` event payload (M1 Task 1.3).

**Files:**
- Create: `senex/tools/pack_hash.py`
- Create: `tests/unit/test_tool_pack_hash.py`
- Modify: `senex/checkpoint.py` — add `tool_pack_hash: str` field
- Modify: `senex/events.py` — `RunStart.tool_pack_hash` field
- Modify: rendered findings.json metadata writer (M7 placeholder; this milestone touches the dataclass)

**`compute_tool_pack_hash(enabled_tools, registry)`:**
```python
def compute_tool_pack_hash(enabled_tools: list[str], registry: ToolRegistry) -> str:
    payload = [
        (name, registry.input_schema(name))
        for name in sorted(enabled_tools)
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
```

- [ ] **Step 5.10.1: Failing tests** in `tests/unit/test_tool_pack_hash.py`:
  - `test_hash_stable_across_calls` — same `(enabled_tools, registry)` -> identical hash twice.
  - `test_hash_independent_of_input_order` — `[A,B,C]` and `[C,B,A]` produce identical hash (sorted internally).
  - `test_hash_changes_when_tool_added` — `[A,B]` vs `[A,B,C]` -> different hashes.
  - `test_hash_changes_when_tool_removed` — `[A,B,C]` vs `[A,B]` -> different.
  - `test_hash_changes_when_input_schema_changes` — register tool `A` with schema v1, hash; re-register with schema v2 (different `max_length` cap); hash differs.
  - `test_hash_is_64_hex_chars` — sha256 -> 64 hex chars matching `^[0-9a-f]{64}$`.

- [ ] **Step 5.10.2: Implement** `senex/tools/pack_hash.py`. Add `registry.input_schema(name) -> dict` method to `ToolRegistry` (returns the OpenAI-format JSON schema for the named tool's input).

- [ ] **Step 5.10.3: Wire `tool_pack_hash` into `Checkpoint`, `RunStart` event, and findings.json metadata.** Add field to each pydantic model. Update `Checkpoint.is_compatible(other_hashes)` (M1) to include `tool_pack_hash` in the comparison.

- [ ] **Step 5.10.4: Run** `pytest tests/unit/test_tool_pack_hash.py -v` -> green. Expected: `6 passed`. Also re-run `pytest tests/unit/test_checkpoint.py -v` to confirm no regression in M1 checkpoint tests after schema field addition.

- [ ] **Step 5.10.5: Commit** `feat(M5): tool_pack_hash for resume discipline`.

## Acceptance criteria

- `pytest tests/unit/test_tools_safety.py tests/unit/test_tool_registry.py tests/unit/test_tool_loop.py tests/unit/test_tool_*.py tests/unit/test_lens_tool_intersection.py tests/unit/test_tool_pack_hash.py tests/integration/test_tool_loop_with_client.py -v` is 100% green (10 unit files + 1 integration file).
- `mypy senex/tools/ senex/lens.py senex/checkpoint.py --strict` reports no errors.
- Each of 6 tools dispatches successfully via `ToolRegistry.dispatch()` with mocked subprocess/HTTP — verified by 6 unit test files (test counts: 7+6+6+8+8+7 = 42 per-tool tests).
- Tool loop unit test count: 9; integration test count: 2.
- `ToolLoop` integration test asserts: with `max_calls=2`, exactly 3 LMS HTTP calls happen (2 with tools + 1 final no-tools); `ToolBudgetExhausted` emitted; final response returned.
- Tool loop test asserts compaction callback is invoked between turns and does NOT count against `max_calls` (synthetic compactor returns rewritten messages).
- `Lens.openai_tools_for(registry, ["read_file", "grep", "shell"])` raises `ConfigError` AND logs separate warnings for each extension before raising (intersection-only enforcement; ALL violations surfaced).
- `compute_tool_pack_hash([...6 tools...])` is stable across runs; subsetting to 3 tools produces a different hash; changing a registered tool's input schema also produces a different hash.
- `validate_regex_pattern("(a+)+$")` raises `RegexTooComplex` or `RegexTimeoutExceeded`; `validate_regex_pattern("safe.*pattern")` passes.
- `validate_repo_path("../etc/passwd", repo_root)` raises `PathOutsideRepo` before any file read; symlinks raise `SymlinkRefused`.
- `M3 ↔ M5 reconciliation`: integration test in `tests/integration/test_tool_loop_with_client.py` uses a real `LMStudioClient` with `respx`-mocked HTTP plus a real `ToolLoop`; the loop docstring includes the verbatim "M3 owns the per-call mechanics; M5 owns the multi-call iteration" note.
- `tool_pack_hash` appears in `Checkpoint`, `RunStart` event, and findings.json metadata fields; `Checkpoint.is_compatible()` rejects resume across different pack hashes.
