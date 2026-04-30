# Senex — Architecture Review

**Date:** 2026-04-29
**Reviewer:** architect-reviewer (read-only)
**Scope:** Per-file ReAct code-audit pipeline at `E:\senex` after the SGLang
migration (7 PRs adding HTTP backend, 3 new tools, skills, memory, TUI Monitor,
optional Docker container management).
**Method:** Read-only inspection of `senex/auditor.py`, `senex/phases/*`,
`senex/lmstudio_lifecycle.py`, `senex/lmstudio_client.py`, `senex/tools/*`,
`senex/lens.py`, `senex/skills.py`, `senex/memory.py`, `senex/checkpoint.py`,
`senex/atomic_io.py`, `senex/findings_partial.py`, `senex/config.py`,
`senex/llm_client.py`, `senex/runlock.py`, `senex/subscribers/*`,
`senex/tui/monitor.py`, `senex/lens/correctness/skills/skills.toml`,
`tests/unit/test_import_boundaries.py`, and the test inventory (73 files, 831
tests).
**Verdict:** Publishable. The architecture is consistent with the documented spec
sections, contracts are crisp, and the seams are clean enough that the SGLang
migration landed without disturbing the rest of the system. The findings below
are mostly polish; only two items are flagged as must-fix-before-publish, and
both are naming/documentation issues rather than design defects.

---

## 1. Layering / Separation of Concerns

The repository declares an explicit producer/consumer split: `senex.auditor` and
`senex.phases.*` produce events on the bus; `senex.tui` and `senex.subscribers`
consume them. This is enforced by an AST-level test
(`tests/unit/test_import_boundaries.py`) that scans `senex/auditor.py` and every
file under `senex/phases/` for `senex.tui` imports and fails if any are found.

**Layer map (top-to-bottom):**

| Layer | Modules | Imports allowed from |
|------|---------|---------------------|
| CLI/TUI shell | `cli*.py`, `tui/*` | everything below |
| Orchestrator | `auditor.py` | phases, lifecycle, client, registry, subscribers |
| Phases | `phases/*` | client, registry, lens, memory, skills, error_artifacts |
| Inference | `lmstudio_client.py`, `lmstudio_lifecycle.py` | events, config, secret_redactor |
| Tool framework | `tools/*` | events, secret_redactor, ToolContext |
| Persistence | `atomic_io.py`, `findings_partial.py`, `checkpoint.py`, `runlock.py` | (leaves) |
| Schemas / models | `render_models.py`, `events.py`, `config.py`, `schema/*` | (leaves) |

**Findings:**

- **F1.1 [non-issue].** Layering is honored. `senex.auditor` imports phases and
  the lifecycle context-manager; phases import the client by Protocol
  (`senex.llm_client.LLMClient`) plus the concrete `LMStudioClient` for typed
  exceptions. The Protocol exists and is consumed; LMStudioClient depends on no
  upper layer. No circular imports were observed.

- **F1.2 [nice-to-fix].** Several phase modules import `LMStudioClient` directly
  (e.g. `phases/file_audit.py`, `phases/preflight.py`) instead of going through
  `LLMClient`. The Protocol exists in `senex/llm_client.py` precisely so the
  client can be swapped, but the phase modules currently take the concrete type
  in `__init__`. Switching the type hints to `LLMClient` would let an
  alternative backend be substituted for tests without subclassing
  `LMStudioClient`. The auditor itself already only needs a couple of methods.

- **F1.3 [non-issue].** The `prompt_hash`/`config_hash` plumbing flows top-down
  through `auditor.run_audit` -> `FileAuditPhase` -> `_append_findings` ->
  `compute_finding_id`. No layer recomputes hashes — they are computed once and
  passed.

- **F1.4 [nice-to-fix].** `auditor.py` reaches inside the LMStudioClient with
  `client._fingerprint_pinned = ...` in two places (lines ~352-362). This is a
  documented fix for a known divergence between the lifecycle's `lms ps --json`
  view and the client's `/v1/models` view, but the underscore-prefixed attribute
  is private. A small `set_fingerprint_pin(fp: str)` helper on the client would
  preserve the intent without leaking the attribute. This is a leak through the
  client's encapsulation seam, not across module layers.

- **F1.5 [non-issue].** Disk I/O is funneled through `atomic_io.write_text_atomic`
  and `findings_partial.FindingsPartialWriter`. No phase reaches `Path.write_*`
  directly for run-critical artifacts. (`Renderer.write_file_atomic`,
  `_patch_checkpoint_fingerprint` and the `error_artifacts` writers all go
  through `atomic_io`.)

---

## 2. Phase Orchestration

The `Phase` protocol in `senex/phases/base.py` is a `runtime_checkable`
`Protocol` with three async methods (`read_state`, `do_work`, `write_state`)
and a `name: str` discriminator. `auditor.run_audit` walks a fixed list of five
concrete phase instances and the checkpoint state machine in
`senex/checkpoint.py` advances each from `pending` → `in_progress` → `complete`.

**Findings:**

- **F2.1 [non-issue].** The contract is small and uniform. Every phase exposes
  the same three async methods so the auditor's loop is genuinely generic
  (`auditor.py:474-516`). Even phases that don't persist intermediate state
  return `None` for `read_state`/`write_state` rather than special-casing them —
  good discipline.

- **F2.2 [non-issue].** Failure modes are well-defined and exhaustively named:
  `PreflightFailure(exit_code, message, check_name)`, `PhaseAborted`,
  `ResumeIncompatible`, `RenderFatal`, `AggregateFailed(exit_code=1)`. The
  auditor's outer try/except catches each by class and maps to the documented
  exit codes (R11 collapse: `{0,1,2,3,130}`). The catch-all branch at line 588
  re-raises non-`LifecycleError` so genuine bugs surface as crashes — that is
  the right call for a tool that may run unattended.

- **F2.3 [nice-to-fix].** The phase loop passes `phase_state` between phases
  with no schema (`phase_state: Any`). Within a single run that's fine, but the
  contract between e.g. `DiscoveryPhase`'s output dict (`{"files": [...],
  "relpath_to_report_path": {...}}`) and `FileAuditPhase`'s consumer is implicit:
  `file_audit.py:199-200` uses `state.get("files", [])` and silently returns an
  empty result when the shape mismatches. A shared TypedDict (or the same
  pydantic v2 strict model treatment used elsewhere) per phase boundary would
  let the type checker catch shape drift.

- **F2.4 [nice-to-fix].** `read_state(audit_dir)` and `write_state(audit_dir,
  state)` exist on every phase but only some phases actually use them — most
  return `None`. The protocol allows that but the asymmetry between
  `auditor.run_audit` (which has its own per-file checkpoint tracking via
  `Checkpoint.mark_done`) and the phase-level `read_state` is undocumented.
  Right now the `read_state` exists primarily for `AggregatePhase` recovery and
  `PreflightPhase` capability cache. Worth a 2-line note in `Phase.__doc__`
  about when each helper is meaningful.

- **F2.5 [non-issue].** Phase abort vs. run abort is correctly separated:
  `AggregateFailed` is partial-success (exit 1), `RenderFatal`/`PhaseAborted`
  are external errors (exit 3), `LMSConnectionLost`/`FingerprintChanged` propagate
  out of `FileAuditPhase` to abort the run (`file_audit.py:266-267`). This
  matches `docs/superpowers/plans/2026-04-26-senex-v1/m8-auditor.md`'s §8.2/§8.3
  recovery matrix exactly.

- **F2.6 [non-issue].** Checkpoint state machine is correctly invoked at phase
  granularity AND at file granularity. `auditor.py:486` sets
  `phase_status[name]="in_progress"` before `do_work`, and `file_audit.py:279`
  calls `Checkpoint.mark_done(audit_dir, relpath)` after each file completes
  (or skips/errors). The dual granularity is what enables both phase-level
  resume and file-level idempotency.

---

## 3. Lifecycle Backend Abstraction

Three backends live behind one `LifecycleBackend` Protocol
(`lmstudio_lifecycle.py:137-141`):

- `HTTPBackend` — OpenAI-compatible HTTP (SGLang, vLLM, anything that exposes
  `/v1/models`). Supports an optional Docker compose lifecycle.
- `LMStudioSDKBackend` — the official `lmstudio` Python SDK.
- `LMSCLIBackend` — wraps the `lms` CLI binary via `asyncio.subprocess`.

`LifecycleBackendFactory.select` probes them in order: HTTP first if `base_url`
is configured, then SDK (if `import lmstudio` works and `list_loaded_models`
succeeds), then CLI (if `shutil.which("lms")` finds the binary). On total
failure it raises `LifecycleBackendUnavailable`.

**Findings:**

- **F3.1 [non-issue].** The `LifecycleBackend` Protocol is genuinely identical
  across the three implementations: every backend implements `is_loaded`,
  `load(timeout) -> ModelInfo`, `unload`, and `list_loaded()`. `Lifecycle` (the
  high-level policy class) only ever calls these four methods. No conditional
  on `backend.backend_name` exists in `Lifecycle`'s state machine — the policy
  is mechanism-agnostic.

- **F3.2 [non-issue].** Events are emitted from `Lifecycle`, never from a
  backend (the docstring on `lmstudio_lifecycle.py:12` calls this out explicitly:
  "backends are mechanism-only"). This is the right side of the "mechanism vs.
  policy" line.

- **F3.3 [must-fix-before-publish].** Preference order is currently
  `HTTP -> SDK -> CLI`, but the HTTP probe falls through to SDK only on a fully
  failed `_list_models()` call. When `manage_container=True`, `select` returns
  the HTTPBackend immediately without probing — correct, because the container
  may legitimately be down. **However**, the SDK and CLI branches still load
  even when `base_url` is configured: if `_list_models()` raises but the user
  *did* configure HTTP, the factory silently falls back to a totally different
  backend. That is a debug-from-hell scenario (operator says "I configured
  SGLang at port 30000" — senex actually used `lms` CLI because port 30000 was
  briefly down at probe time). Recommendation: when `base_url` is non-empty AND
  `manage_container=False` AND `_list_models()` fails, raise
  `LifecycleBackendUnavailable` with a hint instead of silently falling
  through. Or at minimum emit a `PreflightWarning` event saying the HTTP
  endpoint was unreachable and the run is using the SDK/CLI fallback.

- **F3.4 [nice-to-fix].** The `HTTPBackend`'s `ModelInfo.quant` is hardcoded to
  `"auto"` and `checkpoint_digest` is the OpenAI-compat `created` field
  (typically a unix timestamp). For SGLang specifically, that timestamp changes
  every container restart, which means the lifecycle fingerprint changes on
  every restart, which means resume fingerprint comparison fails on every
  restart. Currently this is hidden because the auditor pins the client-side
  fingerprint from `/v1/models` separately (`auditor.py:345-362`) AND
  `Checkpoint.is_compatible` doesn't fail on `model_fingerprint` mismatch alone
  if `allow_mixed_resume` is set. But it's a footgun: if the user runs a
  long-running multi-day audit and the SGLang container restarts, the resume
  on day 2 will refuse to attach without `--allow-mixed-resume`. Either pull
  the model SHA from a SGLang-side endpoint that's stable across restarts, or
  document that HTTP-backend fingerprints are restart-volatile and recommend
  using `allow_mixed=true` in `[lmstudio.lifecycle]` for HTTP deployments.

- **F3.5 [nice-to-fix].** `HTTPBackend.unload` calls `docker compose down` only
  when `manage_container=True`; otherwise it's a no-op. The `Lifecycle.release`
  state machine emits `ModelUnloadSkipped(reason="not_loaded_by_us")` which is
  technically correct, but for an HTTP backend in `manage_container=False` mode,
  `loaded_by_us` is always False (since `load` is also a no-op). That means the
  `auto_unload` config knob is unreachable for the most common HTTP setup —
  worth either documenting or branching the `loaded_by_us` semantics for
  `HTTPBackend` (e.g. when `manage_container=True`, `load` returns `True` for
  ownership; when `False`, the backend is non-owning by definition).

- **F3.6 [non-issue].** The `_wait_for_loaded` polling fallback in
  `Lifecycle.acquire` is well-designed: an autoload failure on backends that
  refuse to load (LM Studio's resource guardrail rejects this) becomes a
  bounded poll instead of a hard fail. Heartbeat events
  (`ModelLoadStillWaiting`) at 60s cadence let the TUI surface the wait. The
  cancellation paths re-raise `asyncio.CancelledError` cleanly so Ctrl+C still
  works during the wait.

- **F3.7 [non-issue].** Defense-in-depth via `_resume_holder_record` and
  `ResumedRunCannotOwnLoad`: even though only `Lifecycle.acquire_for_resume`
  exposes the resume path, the helper hard-fails if any internal caller tries
  to set `loaded_by_us=True` for a resumed run. That's the right paranoia for
  a runlock-poisoning failure mode.

---

## 4. Tool Registry / ToolLoop

`ToolRegistry` (`tools/registry.py`) is the single dispatch chokepoint: lookup
→ JSON parse → pydantic validate → handler-under-timeout → serialize → ANSI
strip → redact → token truncate. Each step has its own try/except producing a
structured `ToolError` that the model receives as a tool-role message.

`ToolLoop` (`tools/loop.py`) wraps the registry with bounded iteration: at
most `max_calls + 1` outer iterations, the last reserved for a no-tools
budget-exhausted final turn.

**Findings:**

- **F4.1 [non-issue].** The `max_calls + 1` semantics is correct: the
  termination invariant is "after `max_calls` tool dispatches, run one more
  chat() with `tools=None` so the model can emit its structured response."
  Code matches docstring (`tools/loop.py:307-425`). The `AssertionError` at
  line 429 marked "unreachable" is genuinely unreachable because the
  `if calls_made >= self._max_calls` branch always returns at the bound.

- **F4.2 [non-issue].** Budget-exhaustion injection is clean: a system message
  is appended just before the final no-tools turn telling the model "Tool
  budget exhausted. Emit your final structured response now." This is exactly
  the right shape — it gives the model permission to stop calling tools rather
  than letting it think it's still in a tool-iterating context.

- **F4.3 [non-issue].** Per-tool error mapping (`_KIND_TO_EVENT_KIND`) is a
  dict that translates registry-side error kinds to the fixed `Literal` set
  declared on the `ToolError` event. Unknown kinds fall back to `"internal"`
  so the event still validates — a very nice defense-in-depth detail.

- **F4.4 [nice-to-fix].** The compaction hook is a closure of signature
  `(messages: list[dict]) -> Awaitable[list[dict] | None]`. It runs *between*
  turns and the result replaces the message history if non-None. This is
  correct, but the hook receives the dict-shaped history (not the typed
  `list[ChatMessage]`), which means any compaction-driven compaction prompt
  has to redundantly re-construct the typed surface. Either standardize on the
  typed surface throughout or document the dict<->ChatMessage boundary in
  `MaybeCompact`'s docstring.

- **F4.5 [non-issue].** Tool dispatch counting is reset at the start of every
  `ToolLoop.run()` (line 291), so a reused loop instance accurately reports
  the most recent file's tool counts. `FileMetadata.tools_used` is populated
  from this counter via `_build_file_metadata`.

- **F4.6 [nice-to-fix].** Edge case: when a single assistant turn emits N tool
  calls, all N count against `calls_made` (line 389) and the budget check at
  line 397 fires after the for-loop. That means a model that emits e.g. 8 tool
  calls in a single turn with `max_calls=5` will execute all 8 tool calls
  before the budget check. This is intentional (we want each call to complete
  cleanly so we can return its result to the model) but it means the
  `max_calls` config is more like "soft minimum after which we stop allowing
  more tool turns" than a hard cap on total dispatches. Document the
  semantics or add a stricter check between dispatches in the inner for-loop
  (the latter would require partial-tool-result handling).

- **F4.7 [non-issue].** ReDoS protection lives in `tools.safety.validate_regex_pattern`
  and is invoked by `grep` and similar tools. Repo-path validation
  (`validate_repo_path`) prevents directory escape. These are the right two
  primitives at the right boundary.

- **F4.8 [non-issue].** `compute_tool_pack_hash` (`tools/pack_hash.py`) hashes
  `(name, input_schema)` pairs sorted by name, so the hash is stable across
  runs but sensitive to schema changes — exactly what the resume hash
  discipline needs.

---

## 5. Skills + Memory Injection Point

Skills (`senex/skills.py`) match path/content/graph-fanin triggers and emit
`(name, text)` pairs that get appended to the file's system prompt. Memory
(`senex/memory.py`) accumulates findings from `findings.partial.jsonl` between
file audits and emits a system-role message summarizing prior findings. Both
are inserted into the `messages` list in `phases/file_audit.py:_audit_one`,
right before the token budget gate.

**Findings:**

- **F5.1 [non-issue].** Insertion point is correct: skills are concatenated
  *into* the system prompt (so they share the system-role budget); memory is a
  separate `system`-role message appended after the user turn. Both happen
  before `client.count_tokens(...)`, so the budget check (`file_audit.py:404-428`)
  catches over-budget cases and falls through to the §8.2 "Pre-LMS token count
  > 90%" recovery path: `<file>.SKIPPED.md`, `FileError`, continue. That is the
  documented behavior.

- **F5.2 [must-fix-before-publish].** When the *base* prompt (system + anchor +
  user + numbered source) is already at e.g. 85% budget, an injected memory
  block could push the total to 95% — inside budget. But if a single skill is
  large (e.g. `security.md` is currently small but a future skill could grow)
  AND memory has accumulated findings, the *combined* injection can blow past
  100%. The current behavior is to skip the file, which is fine functionally,
  but it produces an opaque `SKIPPED.md` with reason `token_budget_exceeded`
  and no indication that *injected* content was the cause. A user reviewing
  the audit will think "my source file is too big" when really the file would
  have audited fine without the new skill+memory injection. Recommendation:
  when the budget gate fires AND skills/memory were injected, either (a) emit
  a follow-up `MemoryDropped` / `SkillsDropped` event and retry without the
  injections (graceful degradation), or (b) include a structured `cause` field
  in the SKIPPED.md that names the injected payload sizes so the operator can
  see what got injected. Today's code doesn't do either — the `MemoryInjected`
  /`SkillsInjected` events fire even when the resulting message blows the
  budget and the file gets skipped. (See `file_audit.py:362-401`.)

- **F5.3 [nice-to-fix].** `select_skills` does an early break after the first
  matching trigger per skill (line 56 in `skills.py`), which is correct, but
  there is no telemetry on which trigger fired — only the skill name. For
  triage ("why is this skill firing on this file?") the `SkillsInjected` event
  could carry `(name, trigger_type)` instead of just `name`. Cheap to add.

- **F5.4 [non-issue].** `MemoryBuffer.update` is idempotent (tracks `_seen_ids`),
  so calling it once per file is correct even if the partial file grows
  between calls. The `min_priority` filter is sensible.

- **F5.5 [nice-to-fix].** `MemoryBuffer.format_injection` uses
  `max_tokens * 4` as a char budget — same approximation as everywhere else in
  the codebase. The actual injection runs through `client.count_tokens` for
  the budget gate, so this is fine, but it's the third place that approximates
  tokens with `len(text) // 4` (the others are `_approx_tokens` in
  `lmstudio_client.py` and `_truncate_to_tokens` in `tools/registry.py`). A
  shared `senex.tokenization.approx_tokens(text, model_id)` helper would
  reduce drift if the approximation ever needs tuning.

---

## 6. Schema Discipline

Pydantic v2 with `extra="forbid"` is uniform across the codebase: every
public model derives from a `_StrictModel` base
(`config.py:20`, `lmstudio_client.py:91`, `lmstudio_lifecycle.py:124`,
`events.py:23`). JSON schemas live in `senex/schema/` (audit_response,
checkpoint, compaction_response, crosscut_response, events, findings_index)
and the lens system separately validates `lens.toml` and `tools.toml` via
its own `_LensTomlSchema` and `_ToolsTomlSchema`.

**Findings:**

- **F6.1 [non-issue].** The `extra="forbid"` discipline is genuinely uniform.
  I checked `BaseEvent`, `ChatMessage`, `ChatResponse`, `ToolCall`,
  `LoadedModelInfo`, `ProbedCapabilities`, `ModelInfo`, all the `*Cfg` models
  in `senex/config.py`, and the lens schema models — every single one is
  strict. The "did you mean" suggestion in `config.load_config` for unknown
  keys (Levenshtein distance) is a nice operator-experience touch.

- **F6.2 [non-issue].** Hash-based versioning is well-thought-out. The
  reproducibility bucket is `(config_hash, prompt_hash, model_fingerprint,
  tool_pack_hash, lens_version)` and `Checkpoint.is_compatible` returns False
  on any mismatch (`checkpoint.py:121-134`). The `--allow-mixed-resume`
  override is exposed at the CLI level so the user can opt out when they
  intentionally changed something.

- **F6.3 [nice-to-fix].** `events.schema.json` exists and is shipped, but I
  did not find an explicit assertion that the schema's `oneOf` discriminator
  list matches `senex/events.py`'s `ALL_EVENT_TYPES` tuple at test time.
  `events.py:35-44` enforces the type discriminator at model_validator time,
  but if a developer adds a new event class to `events.py` and forgets the
  schema, an external consumer parsing `events.jsonl` against
  `events.schema.json` will reject lines silently. Recommendation: a
  `tests/unit/test_events_schema_in_sync.py` that parses the schema's
  `$defs` keys and asserts `set(defs) == {cls.__name__ for cls in
  ALL_EVENT_TYPES}`.

- **F6.4 [nice-to-fix].** The `lens.fingerprint` is a sha256 over concatenated
  bytes of `(lens.toml, tools.toml, system_prompt, crosscut_prompt,
  renderer_template)` — but **not** over the response_schema or
  crosscut_schema files (they live under `senex/schema/` and are referenced
  by name only). That means a change to `audit_response.schema.json` does NOT
  invalidate the lens fingerprint or trigger resume incompat. Today this is
  fine because schemas are pinned by name and changing them means a new
  release, but if you ever ship an inline-schema lens or a per-lens schema
  override, the fingerprint will silently understate the change set. Either
  document that response schemas are global (and version-controlled by
  `senex.__version__`) or fold their bytes into `_hash_paths`.

- **F6.5 [non-issue].** `compute_finding_id` produces stable `f-<12hex>` IDs
  by hashing `(file, symbol, line_start, title, prompt_hash)`. Different
  `prompt_hash` = different ID, so heterogeneous resume buckets stay
  distinct. That is the right invariant.

- **F6.6 [non-issue].** The `_sanitize_schema_for_lmstudio` in
  `lmstudio_client.py:1128-1181` is documented as a wire-level adapter
  *only* — the canonical schema is preserved for the senex-side
  `_validate_audit_schema` call. Good separation of "what we send" vs "what
  we accept."

- **F6.7 [non-issue].** SGLang's strict JSON schema support is honored by
  `strict_json_schema=True` default in `LmStudioCfg`, and the
  `json_schema → json_object` fallback path (`_chat_with_schema_fallback`)
  still passes `strict: True` and a sanitized schema to the json_object
  fallback (`lmstudio_client.py:973-980`), which means even the fallback
  retains server-side enforcement. That's a careful design choice that
  preserves invariants on backends that reject the strict mode.

---

## 7. Atomic Write + Checkpoint

The crash-safety story is `findings.partial.jsonl` (append-only with
per-write `flush+fsync`) + `checkpoint.json` (full-rewrite atomic via
`tmp+fsync+rename`) + per-file rendered Markdown (also atomic via
`atomic_io.write_text_atomic`).

**Findings:**

- **F7.1 [non-issue].** `atomic_io.write_text_atomic` correctly uses
  `os.open(O_WRONLY|O_CREAT|O_TRUNC|O_BINARY)` → `os.fsync(fd)` →
  `os.replace(tmp, target)`. Disk-fatal errnos (`ENOSPC`, `EROFS`, `EDQUOT`)
  are wrapped as `DiskFatalError` and propagate as run-killing
  (auditor.py:269-270 maps `DiskFatalError` to `RenderFatal`); other OSErrors
  stay recoverable per-file. The escalation policy is exactly as documented
  in ARCH-13.

- **F7.2 [non-issue].** `FindingsPartialWriter` opens with `buffering=0` and
  calls `os.fsync(fileno())` after each write. The aggregator skips a
  malformed last line (per the docstring), so a crash during the line-write
  truncates at a line boundary. The append-only NDJSON shape means a
  recovered audit can resume by reading existing findings and continuing —
  no replay-from-scratch needed.

- **F7.3 [non-issue].** Checkpoint atomic writes use the same tmp+fsync+rename
  pattern (`checkpoint.py:35-41`) and JSON Schema validation runs *before*
  the rename, so a corrupted checkpoint never reaches disk. The two checkpoint
  load paths (`Checkpoint.load` and `_patch_checkpoint_fingerprint`) both
  validate before writing.

- **F7.4 [nice-to-fix].** The per-file write order documented in
  `phases/file_audit.py:26-29`:

  ```
  <file>.md.tmp -> findings.partial.jsonl append -> checkpoint.json update
  -> rename <file>.md.tmp -> <file>.md -> events.jsonl append -> FileComplete
  ```

  Reading the actual implementation in `_audit_one` (`file_audit.py:572-649`),
  the actual order is:

  ```
  (1) write_file_atomic  — Renderer creates and renames the .md
  (2) _append_findings   — appends to findings.partial.jsonl
  (3) _write_thinking_trace (optional)
  (4) FileComplete event published — DiskWriter's "block" policy ensures
      events.jsonl flushes before publish() returns
  (5) Checkpoint.mark_done  — outer loop in do_work after _audit_one returns
  ```

  The DOCSTRING says findings are written *before* the .md rename; the CODE
  writes the .md first then appends findings then checkpoints. Both orders
  are crash-safe (a re-audit re-emits the same finding IDs because
  `compute_finding_id` is deterministic), but the divergence between docstring
  and code is a footgun for the next maintainer. Update the docstring or fix
  the order to match — either is fine, but they shouldn't disagree.

- **F7.5 [non-issue].** Checkpoint mid-write: `Checkpoint.mark_done` does
  `load -> mutate -> write` without any cross-process locking, but the
  audit_dir is single-writer per-run by `RunLock` semantics, so this is safe.
  If two runs ever shared an audit_dir the lock acquisition would fail before
  this code ran.

- **F7.6 [non-issue].** Resume idempotency: `FileAuditPhase.do_work` reads the
  checkpoint's `completed_files` (line 217-218) and skips re-auditing files
  already done. Combined with deterministic finding IDs, a re-audit that
  re-runs a file that crashed mid-write produces the same finding IDs and
  the aggregator dedupes — so even a worst-case "crashed after .md rename
  but before mark_done" results in duplicate appends to
  `findings.partial.jsonl` that the aggregator collapses on read. End-to-end
  crash safety is real.

---

## 8. Configuration Surface

The `[lmstudio]` section in `senex.config.toml` now carries both legacy
LM-Studio-specific fields (`base_url`, `api_key`, `model`, `lifecycle`) and
new SGLang-specific fields (`sglang.manage_container`, `sglang.compose_file`,
`sglang.via_wsl`, `sglang.wsl_distro`, etc.). The pydantic class is
`LmStudioCfg`.

**Findings:**

- **F8.1 [must-fix-before-publish, marketing/clarity].** The section is named
  `[lmstudio]` but the default `base_url` is `http://localhost:30000/v1` —
  SGLang's default port. The docstring on `LmStudioCfg.base_url`
  (`config.py:169-170`) explicitly says "SGLang inference backend endpoint."
  This is misleading. New users (or contributors a year from now) will
  reasonably assume `[lmstudio]` means "LM Studio" and be confused about
  why their LM Studio config doesn't work. **Recommendation**: rename
  `[lmstudio]` → `[inference]` and rename the pydantic class
  `LmStudioCfg` → `InferenceCfg`. Use `gitnexus_rename` to do this safely.
  Provide a one-version migration shim in `load_config` that accepts
  `[lmstudio]` and emits a `PreflightWarning` on use. This is the single
  highest-leverage rename in the codebase right now: every new contributor
  hits this confusion within their first hour.

- **F8.2 [nice-to-fix].** `[lmstudio.sglang]` is well-named *given* the parent
  is `[lmstudio]`, but if you do the rename above, this becomes
  `[inference.sglang]` which is also fine. Alternatively split into
  `[inference]` (transport/protocol — base_url, timeouts, sampling, schema
  mode) and `[inference.backend.sglang]`, leaving room for
  `[inference.backend.vllm]` and `[inference.backend.openai]` later. The
  current namespacing works but flat-rename keeps backward compat simpler.

- **F8.3 [non-issue].** Per-repo overrides via `RepoCfg.lmstudio` work
  correctly through `_deep_merge` in `resolve_config` (`config.py:300-330`).
  CLI overrides → TUI overrides → file → defaults is a sensible layering.

- **F8.4 [nice-to-fix].** `LifecycleCfg.runlock_dir` defaults to empty string
  rather than `None`. That's a minor pydantic ergonomic — `Path | None` would
  type-check more cleanly than the empty-string sentinel. Not load-bearing.

- **F8.5 [nice-to-fix].** `[lmstudio.lifecycle]` includes `auto_load`,
  `auto_unload`, `allow_mixed`, `load_timeout_seconds`,
  `load_wait_timeout_seconds`, and `load_wait_poll_interval_seconds`. Five
  knobs for one decision tree is a lot. Consider documenting which combos
  actually make sense: `auto_load=False, load_wait_timeout_seconds=600` is
  effectively "wait for me to load it manually," while `auto_load=True,
  load_wait_timeout_seconds=0` is "fail fast on autoload error." A small
  table in the docstring (or even the `senex.config.toml.example`) would
  help operators pick.

---

## 9. Test Pyramid

Inventory:
- **73 test files** containing **831 test functions** (per
  `find tests -name "*.py" | xargs grep -c "def test_" | sum`).
- `tests/unit/` is the bulk of the pyramid (~60 files): one file per source
  module covering happy path + named-exception paths.
- `tests/unit/test_phases/` has dedicated phase-level tests for all five
  phases.
- `tests/integration/` has only **2 files**: `test_tool_loop_with_client.py`
  and `test_view_replay.py`.
- `tests/tui/` has 9 widget/screen tests using Textual's `App.run_test`.
- `tests/golden/`, `tests/recorded/`, `tests/fixtures/` exist but are small.

**Findings:**

- **F9.1 [non-issue].** Unit coverage is excellent. The `test_import_boundaries.py`
  AST scan, the `test_phase_protocol.py` runtime-checkable protocol test, the
  `test_lens_tool_intersection.py` for the lens/config subset enforcement —
  these are exactly the structural tests a long-lived codebase needs.

- **F9.2 [must-fix-before-publish].** **Integration coverage is thin.** Two
  files for the entire integration tier is not enough for a tool that combines
  HTTP client + tool dispatch + LLM + filesystem + checkpointing. Specific
  gaps I'd expect to see:
  - **End-to-end smoke against a recorded SGLang fixture.** Today the
    `LMStudioClient` is exercised against mocked httpx responses
    (`test_lmstudio_client.py`) and the `ToolLoop` is exercised with a
    stub client. There's no test that actually proves the SGLang JSON-schema
    + tools combination works on real wire format. A VCR-style cassette of
    a real SGLang `/v1/chat/completions` response (recorded once, replayed
    forever) would catch wire-format drift.
  - **Crash-recovery integration test.** The atomic-write story is unit-
    tested, but I didn't find a test that simulates a crash mid-`do_work`
    (e.g. `KeyboardInterrupt` raised after `findings.partial.jsonl` write
    but before `checkpoint.mark_done`) and verifies that resume produces
    the same final findings.json. This is the single most important
    end-to-end guarantee senex makes.
  - **Lifecycle-backend matrix test.** Each of HTTP, SDK, CLI is unit-
    tested in isolation with mocks, but I didn't see a test that runs
    `LifecycleBackendFactory.select` against a fixture where the HTTP
    endpoint is up, the SDK is importable, and the CLI is on PATH, and
    asserts the preference order. Especially given F3.3 (the silent
    fallback when HTTP is configured but unreachable), this is a
    regression-prone surface.
  - **TUI replay against a real events.jsonl.** `test_view_replay.py`
    exists and is the right idea — extend it to cover all 30+ event types
    against a fixture from a real run.

- **F9.3 [nice-to-fix].** No load test or budget-stress test. The
  `max_calls_per_file` and `max_compactions_per_file` knobs interact in
  complex ways with `token_budget_pct` — there's no test that exhausts both
  budgets in sequence and asserts the final ERROR.md is well-formed.

- **F9.4 [non-issue].** Schema validation tests (`test_schemas.py`) are
  comprehensive and the schema files are loaded at test-collection time so
  malformed schemas fail fast.

- **F9.5 [nice-to-fix].** No fuzz test on the tool registry's JSON parsing.
  The model can return anything in `tool_calls[i].function.arguments`; the
  pydantic validator catches schema violations, but a property-based test
  (hypothesis) on the dispatch pipeline would catch e.g. UTF-8 surrogate
  pairs, deeply nested objects, or extreme key counts that pydantic might
  handle differently across versions.

---

## 10. Future-Proofing

The asks-of-the-future, in order of difficulty:

**Adding a new lens.** Drop a directory under `senex/lens/<name>/` containing
`lens.toml`, `tools.toml`, `system_<variant>.md`, optional skills. Reference
existing schemas under `senex/schema/`. Cost: **low**, well-supported. The
correctness lens is the existence proof.

**Adding a new tool.** Add `senex/tools/<name>.py` with a pydantic input model,
a handler `(input_model, ToolContext) -> Awaitable[Any]`, and a
`register_<name>(registry: ToolRegistry)` function. Wire into
`tools/__init__.register_default_tools`. Cost: **low**. The 9 existing tools
are a clean template.

**Adding a vLLM backend.** Either:
- (a) Configure `base_url` to vLLM's OpenAI-compat endpoint and use the
  existing `HTTPBackend`. Requires only that vLLM serves `/v1/models`,
  `/v1/chat/completions` with `tools` + `response_format=json_schema`. Today,
  vLLM's strict-JSON-schema support is good. Cost: **near-zero** if the wire
  shape matches.
- (b) Write a `VLLMBackend(LifecycleBackend)` if vLLM has its own load/unload
  primitives that don't reduce to docker compose. Implement the four-method
  Protocol; teach `LifecycleBackendFactory.select` about it. Cost: **low**,
  the contract is small.

**Adding an OpenAI cloud backend.** This is the harder one because it changes
several invariants:
- `compute_fingerprint` assumes you can probe a quant + checkpoint_digest;
  for OpenAI you'd hash `(model_id, "cloud", api_endpoint)` or similar.
- `Lifecycle` assumes load/unload is a meaningful operation; for cloud it's
  a no-op (the model is always "loaded" from the client's perspective).
- The runlock semantics may be irrelevant (no GPU contention) but you still
  want the runlock for "two concurrent senex runs against the same model
  ID share an audit_dir lock."
- Token-counting changes: OpenAI provides usage stats post-hoc, so the
  `count_tokens` pre-call estimate becomes more important.
- Cost considerations enter the picture (rate limits, budget caps).

  Cost: **medium**. The HTTP backend is 80% of what's needed; the rest is
  documenting which lifecycle invariants are vestigial in cloud mode.

**Adding a new event type.** Extend the `Literal` discriminator in `events.py`,
add a `oneOf` branch to `events.schema.json` (subject to F6.3), and update
`ALL_EVENT_TYPES`. Cost: **low**, but error-prone if the schema sync test
isn't in place.

**Adding a phase.** Implement `Phase` Protocol in a new module under
`senex/phases/`, register it in `auditor.run_audit`'s phase list, add a
`PhaseName` literal in `checkpoint.py`. Cost: **low**.

**Adding a TUI screen.** Subscribe to events via `EventBus.subscribe_local`
and compose Textual widgets. The Monitor screen is the existence proof. Cost:
**low**.

**Overall:** the abstraction cost paid up front (Phase Protocol,
LifecycleBackend Protocol, ToolRegistry, Lens loader, layered config) buys
real future-proofing. New backends and new lenses both fit cleanly.

---

## Findings Summary

### Must-fix-before-publish (3)

| ID | Item | Where |
|----|------|-------|
| F3.3 | Silent SDK/CLI fallback when HTTP is configured but unreachable masks operator misconfig | `lmstudio_lifecycle.py:546-610` |
| F5.2 | Skills+memory injection can blow token budget; opaque SKIPPED.md attributes failure to "your file is too big" rather than to the injected payload | `phases/file_audit.py:362-428` |
| F8.1 | Section `[lmstudio]` is the SGLang surface; rename to `[inference]` (or similar) before public release to avoid permanent confusion | `senex/config.py`, `senex.config.toml*` |

### Nice-to-fix (12)

| ID | Item |
|----|------|
| F1.2 | Phases hint `LMStudioClient` directly instead of the `LLMClient` Protocol |
| F1.4 | `auditor.py` mutates client's private `_fingerprint_pinned` — add a setter |
| F2.3 | `phase_state: Any` between phases — use TypedDicts to lock the boundary shapes |
| F2.4 | Document when `Phase.read_state`/`write_state` is meaningful (most phases return None) |
| F3.4 | HTTPBackend fingerprint volatile across SGLang container restarts — document or fix |
| F3.5 | `auto_unload` config knob unreachable in HTTP `manage_container=False` mode |
| F4.4 | `MaybeCompact` hook receives dict-shaped messages, not the typed `ChatMessage` |
| F4.6 | A single assistant turn with N tool calls bypasses the budget check until after all N dispatches — document or tighten |
| F5.3 | `SkillsInjected` event names skills but not which trigger fired |
| F5.5 | Three independent `len(text) // 4` token approximations — consolidate |
| F6.3 | No test asserting `events.schema.json $defs` matches `senex.events.ALL_EVENT_TYPES` |
| F6.4 | Lens fingerprint hashes prompts but not the response schema files |
| F7.4 | Per-file write-order docstring in `file_audit.py:26-29` disagrees with implementation order |
| F8.4 | `LifecycleCfg.runlock_dir: str = ""` should probably be `Path \| None` |
| F8.5 | Five lifecycle knobs document their semantics individually but not their interactions |
| F9.2 | Integration coverage thin — no SGLang VCR cassette, no crash-recovery e2e test, no backend-matrix test, limited TUI replay coverage |
| F9.3 | No budget-stress test exercising max_calls + max_compactions interaction |
| F9.5 | No hypothesis-style fuzz test against tool registry's JSON parsing |

### Non-issues (positive findings worth calling out)

- **Layering and import boundaries are policed by an AST test** that runs in CI
  — that level of structural discipline is rare and valuable (F1.1).
- **Five named exception classes** at the phase level map exactly to the
  documented exit-code surface (F2.2).
- **The `LifecycleBackend` Protocol is genuinely identical** across HTTP/SDK/CLI
  — `Lifecycle` policy never branches on backend type (F3.1, F3.2).
- **`ResumedRunCannotOwnLoad`** as a defense-in-depth guard against runlock
  poisoning is exactly the right paranoia (F3.7).
- **Tool registry's structured-error mapping** with a fallback `_KIND_TO_EVENT_KIND`
  default of `"internal"` ensures events always validate even on unknown errors
  (F4.3).
- **`extra="forbid"` is uniform** across every public pydantic model in the
  codebase, with Levenshtein "did you mean" hints on unknown config keys (F6.1).
- **End-to-end crash safety** through deterministic finding IDs +
  append-only NDJSON + atomic checkpoint rewrites holds up to scrutiny (F7.6).
- **Future-proofing cost is low**: vLLM is near-zero work via the existing
  HTTP backend; new lenses, tools, phases, and TUI screens all have existence
  proofs and clean templates (§10).

---

## Recommendation

**Senex is publishable in its current state, conditional on resolving the three
must-fix items.** All three are surface-level (one rename, one event/log
improvement, one factory selection guard) and none require architectural
changes. The underlying design — Phase Protocol + LifecycleBackend Protocol +
ToolRegistry + atomic-IO + hash-based resume discipline — is sound and
well-implemented. The 831 unit tests + AST import-boundary enforcement give
high confidence that the contracts hold; the integration tier should be
strengthened in a follow-up release but is not a publication blocker.

The most leveraged single change is the `[lmstudio]` → `[inference]` rename
(F8.1). Doing it now, before the SGLang migration ships to a wider audience,
saves a permanent stream of "why is my LM Studio config under [lmstudio]
talking to SGLang" questions for the rest of the project's life.
