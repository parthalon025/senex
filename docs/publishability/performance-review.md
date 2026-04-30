# senex Performance Review (read-only)

**Scope:** Static review of the four hot paths called out by the user — Walker, per-file audit, Renderer, Aggregator — against the constraints of a single-machine, single-GPU (RTX 5080 16GB), Qwen3-8B-AWQ on SGLang setup.

**Method:** Pure code review. No profiler runs. No edits. Findings keyed by impact/effort with `file:line` anchors.

**Headline:** Throughput is GPU-bound during the `_audit_one` LLM call (correctly serialised). The biggest correctness/efficiency wins outside the GPU are: (1) the Walker calls `Path.resolve(strict=True) + .stat()` per file (2 syscalls each, one is the canonical-path resolver), (2) `count_tokens` re-tokenises the full message history on every turn (8x per file at the budget cap), (3) `MemoryBuffer.update` re-reads `findings.partial.jsonl` from scratch every file with no streaming offset, (4) every `Checkpoint.mark_done` is a load + dict-copy + atomic-write per file, and (5) skills regex matching recompiles patterns on every call. None of these are catastrophic; together they probably cost 10–25% of wall-clock on a large repo.

The SGLang config is reasonable for single-user 8B-AWQ on 16GB but is missing two flags that materially help senex's workload (long fixed-prefix prompts repeated across files): `--enable-cache-report` is a diagnostic, but the real wins are `--schedule-conserveness 0.3` is unnecessary for a single user and the **prefix cache is on by default** in SGLang — but senex's prompt construction order defeats it (memory block sandwiched in the middle, see §5).

---

## 1. Hot-path identification

### 1.1 File walk — `senex/walker.py:140-211`

| # | Finding | Impact | Effort | Anchor |
|---|---------|--------|--------|--------|
| W1 | **`candidate.resolve(strict=True)` per file is a syscall (canonicalisation, follows symlinks).** For 50k files this is ~50k stat-equivalent operations on top of `os.walk`'s own enumeration. The symlink-escape guard requires it (§SEC-3, comment at line 155–158), but it could be deferred until *after* the gitignore + extension filters, which are pure-string. Today the order is `resolve → gitignore → extension → size`; flipping it to `gitignore → extension → resolve+size` would skip the heaviest syscall on every file rejected by extension or gitignore (typically the majority on a big monorepo). | **Medium** (likely 30–60% walker speedup on extension-heavy repos) | Low (rearrange the four filter blocks; tests are deterministic on the kept-set) | `walker.py:159-181` |
| W2 | **`resolved.stat().st_size` is a second syscall per kept file.** On Windows NTFS this is non-trivial. `os.walk` already has `DirEntry`-style metadata on POSIX via `os.scandir`, but senex uses `os.walk` (no DirEntry exposure). Switching to `os.scandir` recursion would let the size check use the cached entry stat. | **Low** (only fires for kept files, which are <10% of total on most repos) | Medium (custom recursion replacing `os.walk`) | `walker.py:179` |
| W3 | **`pathspec.match_file` is O(N\_patterns × N\_files).** `pathspec` does compile patterns into regex internally, but it iterates the full pattern list for every file. For typical `.gitignore` (10–50 lines) this is fine; senex layers `gitignore` on top of an 8-element default-excludes set, so this is not a near-term concern. No O(n²) discovered. | **Low** | — | `walker.py:122-125, 169` |
| W4 | **Sort-then-walk-then-sort.** `dirnames` is sorted in-place inside the `os.walk` callback (line 144), then the kept list is sorted again at line 200. Two sorts is fine — but the in-loop sort of `dirnames` is `O(d log d)` per directory and runs whether or not the user cares about determinism mid-run. Acceptable cost. | **Low** | — | `walker.py:144, 200` |
| W5 | **Case-collision detection is correct but uses `str(resolved).encode()` for the SHA hash.** On a case-insensitive filesystem hit, this is one SHA256 per collision — negligible. | **Low** | — | `walker.py:183-191` |

No O(n²) patterns. No quadratic growth in `seen_lower` (it's a dict). The walker is dominated by syscall count, not algorithmic order.

### 1.2 Per-file audit — `senex/phases/file_audit.py::_audit_one` + `lmstudio_client.py` + `tools/loop.py`

| # | Finding | Impact | Effort | Anchor |
|---|---------|--------|--------|--------|
| A1 | **`count_tokens` re-tokenises the FULL message history every turn.** `LMStudioClient.count_tokens` (`lmstudio_client.py:461`) iterates every message and calls `enc.encode(joined)` on the concatenated content. With `max_calls_per_file = 8`, the same 50–80% of the prompt (system, anchors, skills, graph, source) is re-encoded up to 8 times per file as history grows. Tiktoken at 50–100k tokens/s on Python means 5–20ms per call × 8 × N\_files. Not catastrophic, but real. **Fix shape:** memoise per-message token counts on a content hash and sum them. | **Medium** (1–3% of wall-clock per file at 8 turns; visible at scale) | Medium (per-message LRU keyed by `(model_id, content_hash)`) | `lmstudio_client.py:461-485` |
| A2 | **`count_tokens` is also called by the budget gate at the top of `_audit_one`** (`file_audit.py:404-405`) — that's *one extra full encode* before the LLM call, but the body of the system+user prompts is already known to fit budget for 99% of files. **Fix shape:** cache the budget-gate result and reuse it as the seed for A1. | **Low** | Low | `file_audit.py:404-408` |
| A3 | **System prompt is read from disk every file.** `lens.system_prompt_path.read_text(...)` on `file_audit.py:352`. The same file-bytes are read once per audit file. With 50k files that's 50k disk reads of an unchanging asset. **Fix shape:** snapshot the prompt body at phase init, or `lru_cache` `_load_system_prompt(path)`. | **Medium** (500ms+ saved on a 50k-file audit, more on cold cache) | Low (one-line `lru_cache` decorator) | `file_audit.py:352` |
| A4 | **Schema is loaded once per phase (`_load_audit_schema` at line 215).** Good — already memoised via the local variable. No issue. | — | — | `file_audit.py:215, 124-129` |
| A5 | **Anchor selected per file.** `select_anchor(file)` is called inside the hot loop (`file_audit.py:351`). Need to verify whether `senex.prompts._anchor_loader.select_anchor` does I/O on every call — if it reads anchor markdown each time, this compounds. (Not read in this review; flagging for inspection.) | **Unknown** (depends on impl) | Low (cache if missing) | `file_audit.py:351` |
| A6 | **Tool registry dispatch overhead is small but per-call:** `registry.openai_tools(lens_tools)` is called every loop iteration (`tools/loop.py:308-312`). It rebuilds the OpenAI schema list by iterating `enabled_names`, looking up `_tools[name]`, and calling `model_json_schema()` — pydantic's JSON-schema generator. **Pydantic's `model_json_schema()` is not cached** and rebuilds the schema graph each call. With 8 turns × 6 tools = 48 schema rebuilds per file. **Fix shape:** cache `openai_tools(tuple(enabled_names))` on the registry, or pre-compute once per audit run since the tool pack is hashed and immutable. | **Medium** (10–30ms saved per file, more on slower machines) | Low (`functools.lru_cache` on a method that converts `list` to `tuple` first) | `tools/registry.py:144-165`, `tools/loop.py:308-312` |
| A7 | **`_to_chat_messages(messages)` rebuilds `ChatMessage` objects every turn.** `tools/loop.py:313, 141-169` — converts the dict-shaped history back to typed `ChatMessage` objects on every iteration of the outer loop, including all prior messages. Pydantic v2 validation is fast but compounds with A1. **Fix shape:** keep history as `ChatMessage` from the start; only the dict shape is needed for serialization to the wire (which `_build_base_body` already does at `lmstudio_client.py:1080`). | **Low–Medium** | Medium (touches several call sites) | `tools/loop.py:141, 313` |
| A8 | **Compactor `should_trigger` re-runs `count_tokens` on every loop iteration via `Compactor.maybe_compact`.** `compaction.py:325-329` — same encode-the-world cost as A1, just on the compactor side. With history growing across tool calls, this fires every turn. | **Medium** (compounds with A1) | Medium (share token-count cache with client) | `compaction.py:325-329, 354` |
| A9 | **`bus.publish` walks all subscribers + all local subs synchronously.** `events.py:485-494`. For a high-frequency event (`OutputTick` at ~256 tokens / 500ms), this is unavoidable. The drop-oldest policy on Metrics + coalescing of Tick types prevents queue blow-up. The publish path does `dict.items()` enumeration and `for` over both subs and local-subs on every event. With ~6 subscribers and 100s of ticks per file, this is fine. | **Low** | — | `events.py:485-494` |
| A10 | **`messages.append(_assistant_message_dict(response))` then immediately `chat_messages = _to_chat_messages(messages)` next iteration** — round-trips through dict shape unnecessarily. Same as A7. | Low | Medium | `tools/loop.py:323, 313` |
| A11 | **Graph awareness uses subprocess (`asyncio.create_subprocess_exec` for `npx gitnexus`) per file** — each file spawns a Node.js process. Node startup is 100–300ms cold. The `_cache: dict[str, GraphContext]` (line 111) caches by relpath, so re-visiting the same file is free, but the cache is per-instance and a fresh provider is created per run. **Mitigated by `prefetch_all` (line 206)**, which gathers concurrently — but `_audit_one` calls `await self._graph_provider.fetch(relpath)` (one at a time, `file_audit.py:333`). **The auditor never calls `prefetch_all`.** That's a missed batch opportunity. | **High** (50k files × 200ms Node-start = 2.8 hours of pure subprocess overhead, unless prefetch is wired) | Medium (call `prefetch_all` after Discovery, before file_audit loop) | `graph_awareness.py:206-213`; missed at `file_audit.py:333` |
| A12 | **Skills regex re-compiled per file.** `select_skills` (`skills.py:40-57`) calls `re.search(trigger["pattern"], file_path)` and `re.search(..., source)` for every skill × every trigger, on every file. Python's `re` module caches up to ~512 compiled patterns globally, but with N\_skills × 2 triggers per skill, this can churn the cache on long runs. **Fix shape:** pre-compile triggers at config-load time inside `_load_skills_cfg`. | **Low–Medium** (single-digit ms per file × 50k files = ~10 minutes saved) | Low (compile in the lru\_cache helper) | `skills.py:40-57` |
| A13 | **Atomic checkpoint write per file.** Every completed file calls `Checkpoint.mark_done` (`file_audit.py:279`) which does: `Checkpoint.load` (read JSON + jsonschema validate the schema again — line 95-97) → dict-copy → list-extend → `_atomic_write_json` (write tmp → fsync → rename). For 50k files this is 50k × (read + parse + validate + write + fsync). **Validation re-reads the schema file from disk every call** (`checkpoint.py:44-47`). | **Medium–High** (jsonschema validation + fsync per file is 5–20ms each on a SATA SSD; ~10–20 min on a 50k-file run) | Medium (cache the validator at module scope, switch to "in-memory state, snapshot every N files OR on graceful exit") | `checkpoint.py:44-47, 100-108` |
| A14 | **`Checkpoint.set_phase` does the same load+validate+write dance.** Per phase, not per file — fine. | Low | — | `checkpoint.py:110-119` |

### 1.3 Renderer — `senex/renderer.py`

| # | Finding | Impact | Effort | Anchor |
|---|---------|--------|--------|--------|
| R1 | **`lines: list[str]` + `"\n".join(lines)` is the right shape.** Repeated `lines.append` is amortised O(1). No O(n²) string-concat patterns. | — | — | `renderer.py:127-236` |
| R2 | **One atomic write per file** (`renderer.py:397-410` → `atomic_io.write_text_atomic`). `tmp + os.fsync + os.replace`. **`os.fsync` is the slow part on Windows NTFS** — typically 5–15ms per call. With 50k files: ~5–10 minutes of pure fsync time. The combined `findings.partial.jsonl` line append (`findings_partial.py:79`) ALSO fsyncs per finding. So a file with 5 findings does 1 markdown fsync + 5 partial-write fsyncs + 1 checkpoint fsync = 7 fsyncs per file. | **High** (significant on slow disks; 7 fsyncs × 50k files × 10ms = 1 hour on a slow drive) | Hard (fsync is there for a reason — crash safety. Consider grouped fsync per N files, with explicit "checkpoint window" semantics). | `atomic_io.py:65, 68`; `findings_partial.py:79`; `checkpoint.py:39-41` |
| R3 | **`_secret_redactor.redact(body)` runs on the full markdown post-render.** Single-pass regex over 5–50KB of text is fine. No issue. | — | — | `renderer.py:122` |
| R4 | **Render is pure (no I/O) — good.** Crash semantics are clean (ARCH-13). | — | — | `renderer.py:99-122` |
| R5 | **`_sort_findings` is O(n log n)** per file's findings; n is small (1-20). Fine. | — | — | `renderer.py:77-87` |
| R6 | **Combined renderer is O(N\_findings)** with one big sort at the end (`renderer.py:334-341`). For 50k files × ~5 findings = 250k findings, the sort is ~25ms. Fine. | — | — | `renderer.py:334-341` |

### 1.4 Aggregator — `senex/findings_aggregator.py`

| # | Finding | Impact | Effort | Anchor |
|---|---------|--------|--------|--------|
| AG1 | **Loads the entire `findings.partial.jsonl` into memory** (`findings_aggregator.py:133`) — `text = partial.read_text(...)`. For 250k findings × ~500 bytes each = 125MB. On a 64GB box this is fine; flagging for very large repos. | **Low** | Low (stream lines if needed) | `findings_aggregator.py:133` |
| AG2 | **Three full passes over `sorted_findings` to count priorities** (`findings_aggregator.py:104-108`). Could be a single pass with a Counter. Negligible. | **Low** | Low | `findings_aggregator.py:104-108` |
| AG3 | **`AggregatePhase._collect_skipped` and `_collect_errored` use `rglob`** (`aggregate.py:167, 183`) — recursive traversal of the audit directory, plus `read_text` on each match. For an audit dir with 50k files, this is two full directory scans. The audit dir has known structure (one MD per source file, one ERROR/SKIPPED sidecar per failure); a directly-derived list from the file_audit phase state would skip the rglob entirely. | **Medium** (1–3 minutes on large audits; rglob over 50k entries is slow on Windows) | Low (pass `errored: list[str]` and `skipped: list[str]` from `FileAuditPhase` state through to `AggregatePhase`) | `aggregate.py:165-195` |

---

## 2. Async opportunities (CPU-bound ops blocking the event loop)

| # | Finding | Impact | Effort | Anchor |
|---|---------|--------|--------|--------|
| AS1 | **`source = file.read_text(encoding="utf-8")` runs sync inside the async `_audit_one`.** For 5–500KB source files, this is 1–10ms; not enough to be a problem on a single-file-at-a-time audit, but it's blocking the loop. `senex.tools.read_file` correctly uses `asyncio.to_thread` (`tools/read_file.py:116`) — the audit loop's source-read does not. | **Low** (no concurrent files in this phase) | Low (`asyncio.to_thread(file.read_text, ...)`) | `file_audit.py:312` |
| AS2 | **`lens.system_prompt_path.read_text(...)` and `_load_audit_schema` are sync** in async paths. Same as AS1. Read once and cache fixes both. | Low | Low | `file_audit.py:215, 352` |
| AS3 | **`_number_lines(source)` runs sync; for 500KB Python files it's a non-trivial CPU spin.** Probably 5–20ms. Same caveat — file-audit is serial, so loop-blocking isn't a concurrency hazard. | Low | Low (move into the to-thread block alongside read) | `file_audit.py:118-121, 382` |
| AS4 | **`_renderer.render_file` + `write_file_atomic`** runs sync inline in `_audit_one` (`file_audit.py:582-585`). Render itself is pure-CPU on small data; write does a sync fsync. **Could be off-loaded to a writer task** so the next file's prompt-prep can overlap with the previous file's disk write. The LLM call dominates wall-clock so this overlap is small (~10–50ms per file), but cumulative. | **Low–Medium** | Medium (introduce a single-consumer writer task) | `file_audit.py:582-585` |
| AS5 | **`MemoryBuffer.update(self._audit_dir)` reads `findings.partial.jsonl` from disk every file** (`memory.py:51`) — **fully sync, in async path, in the hot loop.** Reads the entire JSONL each time. For a run with 50k files that have produced N findings so far, this is O(N) per file = O(N²) total. **At 250k findings end-of-run, this is the biggest hidden cost in the audit loop.** | **High** (quadratic in finding count; could be hours on a large audit) | Medium (track byte offset and only read the new tail; or maintain in-memory state and only refresh on resume) | `memory.py:45-70`, called from `file_audit.py:391` |
| AS6 | **Subprocess for gitnexus is correctly async** (`graph_awareness.py:232-258`). Good. The fact that it's not parallelised is the issue (A11), not the async-ness. | — | — | `graph_awareness.py:225` |

---

## 3. Memory growth

### 3.1 `MemoryBuffer` (memory.py)

- **Bound NOT enforced on the in-memory state.** `_seen_ids: set[str]` and `_lines: list[str]` grow unbounded across the entire run. `max_tokens × 4 char approximation` is **only applied at injection time** (`memory.py:76-83`) — the truncated block is built fresh each call but `_lines` itself keeps every accepted finding ever seen.
- **For a run with 50k files producing 250k findings**, `_lines` could hold 250k strings × ~120 bytes = ~30MB; `_seen_ids` 250k × ~30 bytes = 7.5MB. Not catastrophic on 64GB but sloppy.
- **Worse:** `update()` re-reads the entire `findings.partial.jsonl` each call (AS5) and walks the whole file to filter against `_seen_ids`. That's the real cost.

| # | Finding | Impact | Effort | Anchor |
|---|---------|--------|--------|--------|
| M1 | `_lines` grows unbounded (max_tokens budget only trims the rendered injection, not the buffer). | Medium (memory) / High (CPU via AS5) | Medium (track tail-offset on the JSONL; trim `_lines` to the last N findings ranked by priority+recency) | `memory.py:38, 45-70` |
| M2 | `_seen_ids` likewise grows unbounded but is only used for dedup; bound only matters for very long runs. | Low | Low | `memory.py:37` |

### 3.2 `EventBus` queue capacities

- **DEFAULT_CAPACITY = 1024** (`events.py:15`). Per subscriber.
- **Subscribers documented:** `DiskWriter` (block policy), `Tui` (block on non-coalesce, drop on Tick), `Metrics` (drop-oldest on Tick, block on phase events). Local-subs fire synchronously on publish (no queue).
- **Local subs are unbounded** (`events.py:447-479`) — they're a dict of callbacks, not queues. The auditor wires `DiskWriter` as a local sub (`auditor.py:308-310`). Local-sub calls run in the publisher's coroutine, so a slow local-sub blocks `bus.publish`. DiskWriter writes to `events.jsonl` synchronously per event — could backpressure.
- **No queue overflow risk identified** with current subscribers and bounded-cap policy. Acceptable.

### 3.3 Tool loop history

- `messages: list[dict[str, Any]]` grows by one assistant + one tool-result message per turn. Bounded by `max_calls_per_file = 8`, then compactor trims. **Bounded.**
- Per-file resets implicit via fresh `ToolLoop`/`Compactor` per file. Good.

---

## 4. Caching audit (existing + missing)

### 4.1 Existing caches (correct)

- `_load_skills_cfg` — `lru_cache(maxsize=8)` keyed on path. Good.
- `_load_skill_text` — `lru_cache(maxsize=32)` keyed on `(skills_dir, filename)`. Good.
- `LMStudioClient._encoder_cache` — instance dict, cached per `model_id`. Good (`lmstudio_client.py:283, 504-512`).
- `LMStudioClient._caps_cached` — capability probe cached per session. Good.
- `LMStudioClient._fingerprint_cached` + `_fingerprint_cached_at` — TTL cache. Good.
- `LMStudioClient._schema_mode` — schema-fallback decision cached after first success. Good.
- `tools/registry._ENCODER_CACHE` — module-scope `cl100k_base` cache. Good.
- `GitNexusCLIProvider._cache` — per-relpath cache. Good.

### 4.2 Missing / weak caches

| # | Missing cache | Impact | Effort | Anchor |
|---|--------------|--------|--------|--------|
| C1 | **System-prompt body** (lens system\_prompt + anchor + skills concat). Read every file. | Medium | Low | `file_audit.py:352` |
| C2 | **Audit response schema dict** is loaded once per phase via `_load_audit_schema` — fine, but `lens.response_schema_path.read_text` is the underlying I/O. Already memoised through the local. OK. | — | — | `file_audit.py:124-129, 215` |
| C3 | **`Checkpoint` jsonschema validator** — re-reads schema file from disk every `load`/`mark_done`/`set_phase` call. Should be module-scope. | Medium | Low | `checkpoint.py:44-47` |
| C4 | **`registry.openai_tools(enabled)`** — rebuilds OpenAI tool schema list every loop iteration via pydantic `model_json_schema()`. | Medium | Low | `tools/registry.py:144` |
| C5 | **Skills regex compilation** — `re.search` re-compiles per call (relies on Python's tiny module-level re cache). | Low–Medium | Low | `skills.py:45, 47` |
| C6 | **`_load_audit_schema`** is per-phase but the schema path comes from `lens.response_schema_path` — fine because phase is one-shot. | — | — | `file_audit.py:124-129` |
| C7 | **`prompts.compaction.md`** is read once per file inside the Compactor (`compaction.py:467-470`) — already cached on the instance, but each file gets a fresh compactor. Net: read once per file. Should be module-cached. | Low–Medium | Low | `compaction.py:467-470` |
| C8 | **`_audit_response.schema.json`** — read once per phase. Fine. | — | — | `lens.response_schema_path` (not directly observed) |
| C9 | **`tools/registry.input_schema(name)`** — also calls `model_json_schema()` per invocation; consumed by `compute_tool_pack_hash`. Hash is computed once per audit run, so this is fine. | — | — | `tools/registry.py:134-142` |

---

## 5. GPU efficiency / SGLang config

**Reviewed:** `infra/sglang/docker-compose.yml` and `infra/sglang/.env`.

Current config:
```
--model-path Qwen/Qwen3-8B-AWQ
--reasoning-parser qwen3-thinking
--tool-call-parser qwen
--mem-fraction-static 0.85
--context-length 32768
```

**The user's prompt mentioned `--cuda-graph-max-bs 8` and `--chunked-prefill-size 2048` — those are NOT in `infra/sglang/docker-compose.yml`.** Either the user is running a different launch script or these are SGLang defaults; flagging the discrepancy.

Per-file LLM call shape (senex):
- **System prompt** = lens system + anchor + concatenated skills = ~5–15K tokens, mostly stable across files.
- **User prompt** = file relpath + language + graph context block + numbered source = highly variable; source can be 10–50K tokens.
- **Memory injection** = ~800-token system message; **only sometimes appended after the user prompt**.
- **Tool messages** = appended each turn during dispatch loop.

| # | Finding | Impact | Effort |
|---|---------|--------|--------|
| G1 | **`--mem-fraction-static 0.85` on a 16GB card with an AWQ-quantised 8B (~5GB weights) leaves 13.6GB for KV cache + activations.** That's healthy for 32K context, single user. **Could push to 0.88–0.90** for ~5% more KV-cache headroom which translates to longer-prefix reuse. **Low impact.** | Low | Low |
| G2 | **`--context-length 32768` is the model's native limit.** Senex's `token_budget_pct = 0.9` × 32768 = ~29.5K usable. Reasonable. Increasing context would require enabling Qwen3's YaRN scaling and KV-cache budget grows linearly. **Don't.** | — | — |
| G3 | **Prefix cache is enabled by default in SGLang (`--disable-radix-cache` is the off switch).** Good. **BUT — senex's prompt order partially defeats it:** `[system | user | optional memory_block]` means the memory block (which changes every file) sits at the *end* of the prefix. That's actually fine for prefix cache. **However, the system prompt itself includes `select_anchor(file)` + `select_skills(file)` results,** which are file-dependent (`file_audit.py:351-376`). So the "system" prompt varies per file by a sub-prefix. **Fix shape:** make the system prompt order `[stable_lens_system | per_file_skills | per_file_anchor]` so the longest stable prefix lives at position 0. The current order at line 354 (`f"{system_prompt}\\n\\n---\\n\\n{anchor}"`) and line 372-376 (skills appended after anchor) is already prefix-friendly *within* a fixed lens, *as long as* the same skill-set fires for sequential files. Skills selection is path-pattern-driven, so consecutive files in the same directory often share skills — the prefix cache should hit. | Medium (verify with `--enable-cache-report`) | — (already correct shape) |
| G4 | **Missing flag: `--enable-cache-report`** (or check `/v1/models/usage` if exposed). Without it, the operator can't *see* prefix-cache hit rate. Pure observability. | Low (diagnostic) | Low |
| G5 | **`--reasoning-parser qwen3-thinking` + `--tool-call-parser qwen`** — correct for Qwen3-8B-AWQ. ✓ |
| G6 | **No `--chunked-prefill-size`** in the compose file. SGLang's default chunked-prefill is 8192 in recent versions. **For senex's 10–30K-token prompts, smaller chunk size (`2048`–`4096`) reduces TTFT but slightly hurts throughput.** Single-user throughput is preferred, so leave default. | Low | — |
| G7 | **No `--cuda-graph-max-bs`** in the compose file. SGLang defaults to capturing CUDA graphs for batch sizes 1, 2, 4, 8, 16, 32. For single-user senex, batch is always 1; **`--cuda-graph-max-bs 1`** would shrink graph-capture memory by ~200–500MB and shave ~1s off server startup. Trade-off: future multi-stream usage requires re-launch. **Worth setting to 1 for single-user senex.** | Low–Medium | Low (one-line env change) |
| G8 | **No `--schedule-conserveness`** in the compose file. Default 1.0. Fine for single user. | — | — |
| G9 | **`startup_timeout_seconds: 180`** in `senex/config.py:134` — fine for cold start of an 8B AWQ on a fast SSD; could be tight if HF cache is cold (download). | Low | — |
| G10 | **No KV-cache offloading flags** (`--kv-cache-dtype fp8` would halve KV cache memory at minor quality cost). With 13.6GB free and 32K context @ 8B, this isn't urgent. **Optional future tuning.** | Low | Medium |

**Net:** SGLang config is reasonable. The biggest wins are observational (G4) and prompt-shape (G3 — verify cache hit rate). KV-cache reuse for the long fixed-prefix ReAct loop **should work today** without new flags, *provided* the system prompt's stable section is genuinely byte-identical across consecutive files. Worth measuring.

---

## 6. Parallelism

| # | Finding | Impact | Effort |
|---|---------|--------|--------|
| P1 | **File-audit phase is serial across files.** Correct given a single-GPU single-stream backend — one in-flight `chat()` saturates the GPU. **Don't parallelise.** | — | — |
| P2 | **Renderer could run in parallel with the next file's prep.** Render + atomic-write per file is ~10–50ms; LLM call is multi-second. Overlap value: marginal. **Not worth it.** | Low | Medium |
| P3 | **Graph-awareness `prefetch_all` is the real parallelism win.** Subprocess spawn dominates per-call cost; running them concurrently across files (bounded by, say, 8 workers) could prefetch the entire run's graph context during Discovery. See A11. | **High** | Medium |
| P4 | **Aggregator + Crosscut are post-audit and inherently serial on this workload size.** Not a target. | — | — |
| P5 | **DiskWriter local-sub fires inside `bus.publish`** (synchronous in the publish loop). Slow disk could backpressure event throughput. Consider a queue-bounded subscriber for events.jsonl with explicit drop-newest-on-overflow for the non-coalesce-safe types — *but* events.jsonl is meant to be lossless for replay. **Don't change without explicit lossy-tier design.** | Low | — |

---

## 7. Caching audit artifacts (resume / re-audit unchanged files)

### 7.1 What exists

- **`Checkpoint.completed_files`** records `{path, completed_at}` per file (`checkpoint.py:104-107`).
- **Resume path skips already-completed files** at `file_audit.py:225-227`: `if relpath in already_done: continue`.
- **Compatibility hash bundle** (config/prompt/fingerprint/tool\_pack/lens version) is checked at run start (`auditor.py:267-282`). On hash mismatch, resume aborts unless `--allow-mixed-resume`.

### 7.2 What's missing

| # | Finding | Impact | Effort | Anchor |
|---|---------|--------|--------|--------|
| RC1 | **NO content-hash check.** A file completed at run T1, modified at T2, then resumed: senex skips it because the relpath is in `completed_files`. Resume only protects against the file being unchanged across runs *implicitly* — the user is expected to re-run from scratch when source changes. **For a CI-integrated audit, this is wrong.** | Medium (correctness, not perf) | Medium (add `{path, sha256, completed_at}` and re-audit on hash mismatch) | `checkpoint.py:104-107`, `file_audit.py:225-227` |
| RC2 | **NO cross-run cache.** Two distinct runs against the same repo, same hash bundle, same source file — both audit it from scratch. Caching audit results by `(file_sha256, prompt_hash, model_fingerprint, config_hash, lens_version)` would let unchanged files be copied forward. **Major win for incremental audits.** | High (incremental audits could be 10–100x faster) | High (new `~/.cache/senex/results/<hash>.md` layer + cache-hit path in file_audit) | architectural |
| RC3 | **`completed_at` field is set but unused.** Dead weight. | Low | Low (drop or wire to a reaper) | `checkpoint.py:106` |

---

## 8. Profiling hooks / observability

### 8.1 What exists

- **`MetricsCollectorSubscriber`** (`subscribers/metrics.py`) tracks:
  - `files_done` (incremented on `FileComplete`)
  - `findings_by_priority` (rolled up from `FileComplete.finding_counts`)
  - `tool_calls` (incremented on `ToolCall` event)
  - `compactions` (on `CompactionComplete`)
  - `total_thinking_seconds`, `total_output_seconds` (on `*Complete` events)
- **NEVER updates on Tick events** (correct — would be too noisy).
- Exposes a `metrics` property returning a frozen `Metrics` snapshot for the TUI status strip.

### 8.2 What's missing for performance work

| # | Missing metric | Why | Effort |
|---|--------------|-----|--------|
| O1 | **Per-phase wall-clock** — `Discovery`, `FileAudit`, `Crosscut`, `Aggregate` durations. Available in events (`*Start` / `*Complete` ts diff) but not aggregated. | Bottleneck attribution. | Low |
| O2 | **Per-file latency histogram** — current code emits `FileStart` ts and `FileComplete` ts; need a percentile distribution (p50/p95/p99). | Find slow outliers. | Medium |
| O3 | **Token-count cache hit rate** — if A1 is fixed. | Validate the optimisation. | Low |
| O4 | **Subprocess (gitnexus) latency histogram** — tracked nowhere. | Validate A11. | Low |
| O5 | **Disk fsync count + cumulative time** — could be huge on slow drives. | Decide whether to re-architect partial-write durability. | Medium |
| O6 | **GPU utilisation / KV-cache hit rate** — not exposed by senex (would come from SGLang `/v1/models` or NVML). | Decide G3/G7 wins. | Medium (NVML pollster as an external subscriber) |
| O7 | **Memory usage of `MemoryBuffer`** — `finding_count` is published on `MemoryInjected` but the buffer's `_lines` length is invisible. | Validate M1 bound. | Low |

### 8.3 No instrumentation around critical points

- **`count_tokens` calls** are not counted or timed. Confirming A1 requires adding a counter.
- **`Checkpoint.mark_done`** disk-write time is not tracked.
- **`bus.publish` slowest subscriber** is not tracked. Could be added easily.

---

## 9. Summary table — prioritised by (impact ÷ effort)

| Rank | Finding | Impact | Effort | One-line fix |
|------|---------|--------|--------|--------------|
| 1 | **AS5** Memory.update re-reads JSONL every file (O(N²) total) | High | Medium | Track byte offset; only read tail since last call |
| 2 | **A11** No `prefetch_all` for graph awareness | High | Medium | Call `graph_provider.prefetch_all(relpaths)` after Discovery |
| 3 | **A13 / C3** Checkpoint reads + validates schema per file | Medium-High | Low | Module-scope `_VALIDATOR = _validator()` |
| 4 | **A3 / C1** System-prompt body read every file | Medium | Low | `lru_cache(maxsize=4)` on `_load_system_prompt(path)` |
| 5 | **A6 / C4** `registry.openai_tools(enabled_names)` rebuilt every loop turn | Medium | Low | `lru_cache` on tuple-keyed wrapper |
| 6 | **W1** Walker resolves+stats before extension filter | Medium | Low | Reorder filters: gitignore → extension → resolve+stat |
| 7 | **A1+A8** `count_tokens` re-tokenises history per turn (and per compact-check) | Medium | Medium | Memoise per-message token count by content hash |
| 8 | **R2** 7 fsyncs per file (markdown + checkpoint + N×partial) | High | Hard | Group fsync per N files OR explicit checkpoint window |
| 9 | **AG3** Aggregator rglobs the audit dir for skipped/errored | Medium | Low | Pipe state from `FileAuditPhase` directly |
| 10 | **A12 / C5** Skills regex re-compiled per call | Low–Medium | Low | Pre-compile in `_load_skills_cfg` |
| 11 | **C7** Compaction prompt re-read per file | Low–Medium | Low | Module-scope `lru_cache` on prompt path |
| 12 | **G7** SGLang `--cuda-graph-max-bs 1` for single user | Low–Medium | Low | One-line env change |
| 13 | **M1** `MemoryBuffer._lines` unbounded | Medium (memory) | Medium | Trim to last-N or prio-bounded |
| 14 | **RC2** No cross-run audit cache | High (incremental) | High | New cache layer keyed on file_sha256 + hash bundle |
| 15 | **A5** `select_anchor(file)` may do disk I/O each call | Unknown | Low | Verify + cache if needed |
| 16 | **AS1/AS2/AS3** sync I/O in async paths | Low | Low | `asyncio.to_thread` wrappers |

---

## 10. Recommendations — quick wins (≤1 day each)

1. **Module-cache the checkpoint validator** (`checkpoint.py:44-47`). Trivial, eliminates schema-load syscall per file.
2. **`lru_cache` the system-prompt + compaction-prompt readers** (`file_audit.py:352`, `compaction.py:467-470`).
3. **Cache `registry.openai_tools(tuple(names))`** — pydantic schema generation is non-trivial under iteration.
4. **Reorder walker filters** (`walker.py:159-181`) — gitignore + extension before resolve.
5. **Pre-compile skills regex** in `_load_skills_cfg` (`skills.py`).
6. **Wire `prefetch_all`** between Discovery and FileAudit phases (`auditor.py` around line 489).
7. **Track byte offset in `MemoryBuffer`** so `update()` reads only the tail.
8. **Add per-phase wall-clock to MetricsCollectorSubscriber** so future tuning has a baseline.
9. **Add `--cuda-graph-max-bs 1` to `infra/sglang/docker-compose.yml`** for single-user senex.

## 11. Recommendations — bigger projects

1. **Cross-run audit result cache** (RC2) — biggest user-visible win for repeated audits.
2. **Group fsync per N files** (R2) — requires careful crash-recovery rethink; defer until R2 is measured to be expensive on the user's actual disk.
3. **Token-count memoisation** (A1) — touches client + compactor + budget gate; coordinate to avoid duplicate caches.
4. **Replace `os.walk` with custom `os.scandir` recursion** (W2) — only worth it if profiling confirms walker is significant on Windows.

---

## 12. Files referenced

Absolute paths:

- `E:\senex\senex\walker.py`
- `E:\senex\senex\phases\file_audit.py`
- `E:\senex\senex\phases\aggregate.py`
- `E:\senex\senex\phases\crosscut.py`
- `E:\senex\senex\renderer.py`
- `E:\senex\senex\memory.py`
- `E:\senex\senex\skills.py`
- `E:\senex\senex\graph_awareness.py`
- `E:\senex\senex\events.py`
- `E:\senex\senex\checkpoint.py`
- `E:\senex\senex\atomic_io.py`
- `E:\senex\senex\findings_partial.py`
- `E:\senex\senex\findings_aggregator.py`
- `E:\senex\senex\compaction.py`
- `E:\senex\senex\config.py`
- `E:\senex\senex\auditor.py`
- `E:\senex\senex\lmstudio_client.py`
- `E:\senex\senex\tools\loop.py`
- `E:\senex\senex\tools\registry.py`
- `E:\senex\senex\tools\read_file.py`
- `E:\senex\senex\tools\grep.py`
- `E:\senex\senex\subscribers\metrics.py`
- `E:\senex\infra\sglang\docker-compose.yml`
- `E:\senex\infra\sglang\.env`

No code modified.
