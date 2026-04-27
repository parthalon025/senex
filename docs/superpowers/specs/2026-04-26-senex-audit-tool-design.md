# senex — Local-LLM Code Audit Tool

**Date:** 2026-04-26
**Status:** Design (brainstormed, awaiting user review)
**Owner:** Justin McFarland

## 1. Summary

`senex` is a standalone Python tool that uses a local LM Studio model to audit a target repository file by file, producing per-file Markdown reports and a combined run report. It runs on demand or nightly, presents progress through an integrated Textual TUI, and uses GitNexus as background awareness so the model understands each file's role and consumers without that context being part of the analysis output. Reports hand off cleanly to Claude Code for review or revision; an LM-Studio-driven fix mode is explicit v2 scope.

## 2. Goals & Non-Goals

### Goals
- Audit any selected repository one file at a time using a senior-engineer rubric.
- Run on demand (`senex audit <repo>`) and nightly (Windows Scheduled Task running `run_senex.bat`).
- Produce a structured per-file Markdown report plus a `findings.json` machine index, plus a combined run report and a Claude Code handoff artifact.
- Be resumable after any crash with no more than one in-progress file lost.
- Be observable through an integrated TUI by default; runnable headless for unattended jobs.
- Use GitNexus for awareness only: file role, callers, processes — never echoed in the report.
- Be calibrated to a thinking MoE model (`google/gemma-4-26b-a4b`) running in LM Studio with a 262144 context window.

### Non-Goals (v1)
- Auto-fix mode (`senex fix <audit-dir>` is v2).
- Multi-lens output (security, performance, maintainability are deferred via the `--lens` flag mechanism).
- Concurrent file audits (sequential only — local GPU serializes).
- Cloud LLM backends (the design is OpenAI-compatible, but only LM Studio is tested in v1).
- Auto re-indexing GitNexus.
- Detach-and-reattach across terminal sessions.

## 3. Architecture

Single-process integrated Textual app. The auditor is a pure async coroutine that emits events through an asyncio-queue event bus. Subscribers consume events; subscribers do not drive the auditor. Three subscribers run in every audit:

- `DiskWriterSubscriber` — write-ahead persistence of reports, `findings.json`, `checkpoint.json`, `events.jsonl`, `audit.log`. Always on. Provides crash safety.
- `MetricsCollectorSubscriber` — running totals for the final summary screen.
- `TuiSubscriber` *or* `HeadlessSubscriber` — mutually exclusive, selected by `--no-tui`.

The TUI runs `run_audit()` as an asyncio worker task. Widget render exceptions are caught at the Textual boundary and do not propagate into the auditor task. If the process dies entirely, `senex audit --resume` reads `checkpoint.json` and resumes from the next undone file. Per-file work is durable: each file completion writes report → `findings.json` patch → `checkpoint.json` → `events.jsonl` → bus emit, in that order.

### 3.1 File Layout

```
E:\senex\
├── senex/                              # Python package
│   ├── __init__.py
│   ├── cli.py                          # entrypoints: audit | view | doctor | aggregate
│   ├── walker.py                       # repo file discovery
│   ├── gitnexus_context.py             # graph context via `npx gitnexus context|query`
│   ├── lmstudio_client.py              # OpenAI-compatible client; streaming; thinking-aware
│   ├── auditor.py                      # async coroutine; orchestrates one full run
│   ├── renderer.py                     # structured findings -> markdown report + findings.json
│   ├── cross_cutting.py                # post-pass: per-file findings -> repo-wide themes
│   ├── handoff.py                      # writes claude-handoff.md
│   ├── checkpoint.py                   # resume state
│   ├── config.py                       # TOML loader; per-repo override merging; validation
│   ├── events.py                       # event bus + event type definitions (pydantic)
│   ├── subscribers/
│   │   ├── disk_writer.py
│   │   ├── tui_subscriber.py
│   │   ├── headless_subscriber.py
│   │   └── metrics.py
│   ├── schema/
│   │   ├── audit_response.json         # JSON Schema for LM Studio structured output
│   │   ├── findings_index.json         # canonical findings.json schema
│   │   └── events.json                 # events.jsonl schema
│   ├── prompts/
│   │   ├── system_senior_dev.md        # base senior-dev correctness lens (versioned, hashed)
│   │   ├── per_file_user.md            # per-file user prompt template
│   │   ├── cross_cutting.md            # cross-cutting themes prompt
│   │   ├── lang_python.md              # per-language anchors
│   │   ├── lang_typescript.md
│   │   ├── lang_rust.md
│   │   ├── lang_go.md
│   │   └── lang_csharp.md
│   └── tui/
│       ├── app.py                      # launcher screen + monitor screen
│       └── widgets/                    # progress, findings panel, gates, error banner, status
├── scripts/
│   ├── run_senex.bat                   # Windows scheduler entrypoint
│   └── setup.ps1                       # first-time setup
├── tests/
│   ├── unit/
│   ├── golden/
│   ├── recorded/
│   ├── tui/
│   └── fixtures/
├── senex.config.toml.example
├── pyproject.toml                      # CLI entrypoint, deps
├── requirements.txt                    # pinned for the .bat
├── CLAUDE.md                           # guidance for Claude Code in this repo
└── README.md
```

### 3.2 Module Boundaries

Each module owns exactly one responsibility:

| Module | Owns |
|---|---|
| `walker` | repo file discovery (gitignore + extensions + per-repo overrides + size cap) |
| `gitnexus_context` | building a graph-context block per file (CLI calls only; no MCP) |
| `lmstudio_client` | OpenAI-compatible HTTP client; streaming; thinking content extraction; structured-output negotiation |
| `auditor` | end-to-end run orchestration; emits events; catches exceptions and converts to event-stream errors |
| `renderer` | converting one structured response to one markdown report + a findings.json delta |
| `cross_cutting` | second-pass repo-wide theme synthesis from compressed per-file findings |
| `handoff` | writing the Claude Code handoff artifact |
| `checkpoint` | resume state on disk; idempotent under partial failures |
| `config` | loading, validating, merging TOML config layers (defaults → file → per-repo → TUI overrides) |
| `events` | event bus implementation; event type definitions; pub/sub semantics |
| `tui/app` | Textual application; launcher screen; monitor screen; runs auditor as worker task |

Forbidden imports:
- `tui/*` MUST NOT import from `auditor.py` directly. Both attach to the same event bus.
- `auditor.py` MUST NOT import from `tui/*`.
- `subscribers/*` MUST NOT mutate the bus or call back into `auditor.py`.

## 4. Data Flow

```
User runs: senex audit /path/to/repo  (or scheduler kicks run_senex.bat)
        │
        ▼
TUI Launcher Screen (skipped if --no-tui)
  - reads senex.config.toml
  - user confirms repo, lens, model, sampling overrides
  - writes config.snapshot.toml to <audit-dir>
        │
        ▼
TUI switches to Monitor screen; auditor coroutine starts
        │
        ▼
Phase 1 — Pre-flight (see §8.1)
  - validate config + paths + LMS reachability + GitNexus index + structured-output support
  - exit non-zero on hard failures
        │
        ▼
Phase 2 — Discovery
  - walker enumerates files honoring .gitignore, extension list, per-repo overrides
  - excludes node_modules, .venv, dist, etc.; size cap 10K lines
  - emits DiscoveryComplete{file_count, skipped[]}
        │
        ▼
Phase 3 — Per-file audit loop  (sequential, resumable, idempotent)
  for each file not in checkpoint.completed:
    a) gitnexus_context.fetch(file) -> {cluster, callers_d1, processes}
    b) renderer.build_prompt(file, source, graph_ctx, lang_anchor, addendum)
    c) lmstudio_client.chat(task='file_audit', messages, schema)
       - streams; emits ThinkingToken/OutputToken events
       - returns ChatResponse{content_json, reasoning_content, latencies}
    d) validate JSON against audit_response schema
       - on failure: retry once with stricter prompt
    e) renderer.render(response) -> <file>.md
    f) renderer.append_index(response) -> findings.json
    g) (if save_traces) reasoning_content -> <file>.thinking.md
    h) checkpoint.mark_done(file)
    i) emit FileComplete
        │
        ▼
Phase 4 — Cross-cutting pass
  - load all per-file structured findings
  - compress to (file, category, priority, title) tuples
  - one LMS call (task='cross_cutting'); structured output
  - failures here do not fail the run; combined report notes the gap
        │
        ▼
Phase 5 — Aggregation
  - combined.md (run header, rollup, themes, top findings, file index, skipped, errors)
  - claude-handoff.md (structured prompt for downstream Claude review)
  - finalize findings.json (sorted, deduped)
  - emits RunComplete
        │
        ▼
TUI shows final summary screen; auditor task exits
```

### 4.1 Output Directory

```
E:\senex-audits\<repo-name>\<YYYY-MM-DD>\
├── config.snapshot.toml            # exact resolved settings used
├── checkpoint.json                 # resume state
├── audit.log                       # human-readable
├── events.jsonl                    # machine-readable, schema-validated, one event per line
├── findings.json                   # canonical structured index of all findings
├── combined.md                     # run-level report
├── claude-handoff.md               # structured prompt for Claude Code
├── <relpath>/<file>.md             # per-file report
├── <relpath>/<file>.thinking.md    # captured reasoning trace (if save_traces=true)
├── <relpath>/<file>.SKIPPED.md     # for skipped files (unreadable, too large, etc.)
└── <relpath>/<file>.ERROR.md       # for files that failed audit after retries
```

## 5. Components in Detail

### 5.1 System Prompt (`prompts/system_senior_dev.md`)

Versioned (`v1` initially), SHA256-hashed, snapshot into each audit dir for reproducibility. The prompt establishes a principal-engineer persona with explicit calibration discipline, anti-LLM-reviewer-failure-modes, and output discipline. Full text:

```
ROLE
You are a principal software engineer with 20+ years of production
experience across distributed systems, language design, security, and
operations. You are reviewing ONE source file as if it were a pull
request you must approve, reject, or send back with changes.

CONTEXT YOU HAVE BEEN GIVEN
- The full source of one file (with line numbers).
- A graph-derived context block: the file's cluster, public symbols,
  d=1 callers across the repo, and top processes the file participates
  in. Treat this as ground truth about the file's role and consumers.
- The repository's language and target runtime.

YOUR JOB
Identify defects that would block a senior reviewer from approving
this file. Recognize and call out genuinely good patterns that should
be preserved. Do not nitpick.

WHAT IS A DEFECT
A defect is something that:
  (a) causes incorrect behavior, data loss, or security exposure now or
      under foreseeable inputs, OR
  (b) makes the code unmaintainable in a concrete way (specific
      complexity smell, broken invariant, misleading name causing real
      misreading, dead code that will rot), OR
  (c) violates a contract visible in the graph context (e.g. a function
      called by 12 sites that handles its empty-input case wrongly).

A defect is NOT:
  - Style or formatting (linters handle this).
  - Personal preference disguised as principle.
  - "Could be cleaner if..." absent a concrete defect.
  - Speculative future requirements.
  - "Should consider <library>" or "should use <pattern>" without a
    specific defect the change would fix.

PRIORITY RUBRIC
- high     : the code is incorrect, leaks resources, has a security
             defect, or breaks a contract its callers depend on.
- medium   : an error path is wrong, an edge case is unhandled, a name
             actively misleads, an invariant is unchecked in
             pipeline-critical code, a return value is silently
             dropped, or a loop is unbounded.
- low      : naming clarity, function/file too long for its
             responsibility, missing log context, complexity smell,
             dead code.
- healthy  : a pattern in this file that should be preserved.
             Emit when present; do not invent.

WHAT TO LOOK FOR
Correctness:
  - Off-by-one, wrong comparison, swapped args, incorrect default.
  - Error paths that swallow exceptions, return None silently, use
    bare except, or contextlib.suppress(Exception).
  - Unbounded loops over external input.
  - Unawaited coroutines, dropped asyncio.create_task handles, locks
    held across await, sync I/O in async paths.
  - Race conditions on shared mutable state.
  - Resource leaks (files/sockets/connections not closed on error
    paths; missing context managers).
  - Time/timezone bugs.
  - Ignored non-None return values, especially Result-shaped.

Security (correctness with blast radius):
  - Timing-unsafe equality on secrets/tokens.
  - Path traversal.
  - Command injection.
  - SQL injection.
  - Hardcoded credentials, API keys, tokens.
  - SSRF.
  - Insecure deserialization on untrusted input (legacy binary
    serialization formats, yaml.load, etc.).

Maintainability:
  - Names that don't say what the function/var DOES or RETURNS:
    `handle`, `process`, `manage`, `do_thing`, `data`, `info`.
  - Functions over ~50 lines doing more than one job.
  - Files over ~600 lines spanning two responsibilities.
  - Magic numbers without name or comment.
  - Duplicated logic the graph context shows already exists.
  - Code whose existence is unjustified (deletion is the best fix).

Testability:
  - Pure logic intertwined with I/O.
  - Hidden global state preventing isolation.
  - Edge cases the file's structure makes hard to reach.

WHAT TO NOT FLAG
  - Style/format. No "use f-strings" / "use Pathlib".
  - Architectural recommendations (microservices, event sourcing, DI
    containers, pub/sub) unless the file's specific role demands one
    and you can name the defect it would fix.
  - New dependencies unless stdlib genuinely cannot solve the problem
    and the omission causes a defect today.
  - Renaming things to other vague names.
  - "Should consider..." — either it's a defect or it isn't.

OUTPUT DISCIPLINE
  - Emit only the JSON object matching the response schema. No prose
    before or after. No markdown code fences around the JSON.
  - Each finding describes exactly ONE issue. Do not bundle.
  - Each finding cites a specific line or symbol.
  - The `fix` field must be specific enough that another engineer can
    implement it directly.
  - The `why` field states the root cause or concrete consequence.
  - Do not repeat large source spans verbatim.
  - Do not apologize, hedge, or pad.

UNCERTAINTY HANDLING
  - If you cannot verify a behavior from the file source + graph
    context, set `confidence: low` or omit the finding.
  - If you suspect an issue but cannot pin it to a specific line or
    symbol, omit it.
  - It is correct to return an empty findings array for trivial files.

REPO CONVENTIONS
  - Do not recommend signature changes or removals that would break
    the listed d=1 callers without flagging the breakage as part of
    the fix.
  - Match the language and idioms visible in the file.

THINKING MODELS
  You may reason at length before answering. Your reasoning is
  captured separately and is not part of your final output. Your
  final output must be ONLY the JSON object matching the response
  schema.
```

### 5.2 Per-Repo Addendum

Each repo entry in `senex.config.toml` may declare `system_prompt_addendum = "<path>"`. Loaded at audit start, concatenated after the base system prompt. This is where pensiv's `Senior Engineering Discipline` rules get injected. The addendum file is hashed and included in the audit's config snapshot.

### 5.3 Per-Language Anchors

A single short paragraph per language naming idioms, common pitfalls, and stdlib preferences. Selected by file extension; concatenated after the addendum (or after the base prompt if no addendum). Initial set: Python, TypeScript, Rust, Go, C#. Files with extensions outside the set fall back to the base prompt.

### 5.4 Structured Output Schema (`schema/audit_response.json`)

JSON Schema enforced via LM Studio's `response_format=json_schema`. With the thinking model, schema is enforced on the *final* output only; reasoning is captured separately via `reasoning_content`. If `response_format=json_schema` is incompatible with the loaded model under thinking, the client falls back to `response_format=json_object` plus post-hoc validation against the same schema using Pydantic.

```jsonc
{
  "type": "object",
  "required": ["overall_assessment", "findings", "recommendations"],
  "properties": {
    "overall_assessment": { "type": "string", "minLength": 50, "maxLength": 1500 },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["category", "priority", "title", "issue", "why", "fix", "confidence"],
        "properties": {
          "category": { "type": "string" },
          "priority": { "enum": ["high", "medium", "low", "healthy"] },
          "title": { "type": "string", "maxLength": 120 },
          "issue": { "type": "string" },
          "why": { "type": "string" },
          "fix": { "type": "string" },
          "confidence": { "enum": ["high", "medium", "low"] },
          "location": {
            "type": "object",
            "properties": {
              "line_start": { "type": "integer" },
              "line_end": { "type": "integer" },
              "symbol": { "type": "string" }
            }
          }
        }
      }
    },
    "recommendations": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["title", "rationale"],
        "properties": {
          "title": { "type": "string" },
          "rationale": { "type": "string" },
          "code_snippet": { "type": "string" }
        }
      }
    },
    "best_practices_table": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["feature", "original", "recommended"],
        "properties": {
          "feature": { "type": "string" },
          "original": { "type": "string" },
          "recommended": { "type": "string" }
        }
      }
    }
  }
}
```

### 5.5 LM Studio Inference Controls

Full configurability over sampling, model selection, connection behavior, and thinking-model parameters.

```toml
[lmstudio]
base_url        = "http://localhost:1234/v1"
api_key         = "lm-studio"
connect_timeout = 10
read_timeout    = 600
http_retries    = 3
backoff_seconds = [5, 15, 45]

# "auto" = use whatever's loaded, "<id>" = pin
model = "google/gemma-4-26b-a4b"

# Optional saved sampler bundle in LM Studio UI
preset = ""

[lmstudio.sampling]
# Tuned for thinking-MoE model (gemma-4-26b-a4b)
temperature       = 0.6        # NOT 0.2 — low temp degrades reasoning
top_p             = 0.95
top_k             = 40
min_p             = 0.0
repeat_penalty    = 1.0        # NOT 1.1 — penalty interferes with reasoning chains
frequency_penalty = 0.0
presence_penalty  = 0.0
seed              = null       # determinism hurts thinking quality
max_tokens        = 8192       # FINAL OUTPUT cap (thinking is separate)
stop              = []

[lmstudio.thinking]
enabled              = true
effort               = "high"     # "low" | "medium" | "high" — passed as reasoning_effort if supported
max_thinking_tokens  = 32768
save_traces          = true       # write <file>.thinking.md
include_in_report    = false

[lmstudio.tasks.file_audit]
temperature = 0.6
max_tokens  = 8192

[lmstudio.tasks.cross_cutting]
temperature = 0.7
max_tokens  = 4096
```

Per-repo overrides may shadow any field. `config.snapshot.toml` captures the fully resolved set used by the run. The TUI launcher exposes model, temperature, max_tokens, and seed (with a "randomize" button) as live overrides.

The `lmstudio_client.chat()` API:
- Streams responses; emits `ThinkingStarted`, `ThinkingToken`, `ThinkingComplete`, `OutputStarted`, `OutputToken`, `OutputComplete` events.
- Extracts `reasoning_content` (DeepSeek/Qwen convention) AND strips `<think>...</think>` interleaved tags from `content` if present.
- On `response_format=json_schema` failure under thinking, falls back to `json_object` and validates post-hoc with Pydantic.

### 5.6 Event Bus & Subscribers

Event types (pydantic models, serialized to `events.jsonl` newline-delimited):

| Event | Fields |
|---|---|
| `RunStart` | repo, audit_dir, model, lens, config_hash, prompt_hash, started_at |
| `DiscoveryComplete` | file_count, skipped: [(path, reason)] |
| `FileStart` | path, idx, total |
| `FileContextBuilt` | path, graph_context_tokens |
| `FileLLMCall` | path, prompt_tokens |
| `ThinkingStarted` | path |
| `ThinkingToken` | path, token_count_so_far |
| `ThinkingComplete` | path, total_thinking_tokens, latency_ms |
| `OutputStarted` | path |
| `OutputToken` | path, token_count_so_far |
| `OutputComplete` | path, total_output_tokens, latency_ms |
| `FileComplete` | path, finding_counts: {high, medium, low, healthy} |
| `FileError` | path, phase, error_kind, error_message |
| `CrosscutStart` | |
| `CrosscutComplete` | theme_count |
| `RunComplete` | duration_seconds, totals, exit_status |

Three always-on subscribers:
- `DiskWriterSubscriber`: write-ahead persistence.
- `MetricsCollectorSubscriber`: running totals.
- `TuiSubscriber` OR `HeadlessSubscriber`: mutually exclusive.

### 5.7 TUI

Two screens, one Textual app.

**Launcher (initial):**
- Repo picker (config entries + manual path)
- Lens selector (correctness only wired in v1)
- Model dropdown (populated from `GET /v1/models`)
- Preset dropdown (populated if API exposes presets)
- Sampling fields: temperature, max_tokens, seed (with "randomize" button)
- Include-tests toggle
- Resume detector (offer Resume vs Fresh if today's audit dir exists)
- "Reset to config defaults" button
- "Start audit" button

**Monitor (live):**
- Header: repo, audit-dir, elapsed, model
- Progress bar: `12/87 files (14%) — ETA 23m`
- Current-file panel:
  - filename
  - phase (context | thinking | writing | rendering)
  - thinking token counter
  - tokens in/out
- Recent findings rolling window (deque maxlen=30): priority badge, file, title
- Counter strip: HIGH N | MEDIUM N | LOW N | HEALTHY N
- Error banner (sticky if errors present)
- LM Studio status: model, last-call latency, queue depth
- Footer keybindings: `p` pause, `q` quit, `r` re-run current file, `s` skip, `t` toggle threshold

Pause/cancel/re-run/skip toggle flags on the auditor coroutine via the event bus's reverse channel (commands).

### 5.8 GitNexus Context Builder

Per file, build a graph context block (medium tier) by calling the GitNexus CLI:

```
npx gitnexus context --repo <name> --file <relpath> --json
npx gitnexus query --repo <name> --goal "What does <relpath> do?" --limit 3 --json
```

Combined into a structured awareness block that includes:
- Cluster name and 1-line description
- Public symbols defined in the file
- For each public symbol: count of d=1 callers + a few example callsites
- Top 1–3 processes (execution flows) the file participates in (names + 1-line summaries)

Total budget: ~500–1500 tokens per file. If GitNexus is unavailable or the index is missing, the context block is a single sentence noting absence; the audit proceeds with file source only.

## 6. Configuration

`senex.config.toml` lives at `~\.senex\senex.config.toml` by default; `--config <path>` overrides.

```toml
# Top-level defaults
[output]
root = "E:/senex-audits"
filename_template = "{repo}/{date}"     # under root

[lens]
default          = "correctness"
min_confidence   = "all"                # "all" | "medium" | "high"
include_tests    = false

[walker]
max_lines             = 10000
extensions            = [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".cs", ".cpp", ".c", ".h", ".swift", ".rb", ".php", ".sh", ".ps1"]
default_excludes      = ["node_modules", ".venv", "venv", "dist", "build", "__pycache__", "*.min.*", ".git", "vendor"]
respect_gitignore     = true

[lmstudio]
# (see §5.5)

[lmstudio.sampling]
[lmstudio.thinking]
[lmstudio.tasks.file_audit]
[lmstudio.tasks.cross_cutting]

# Repo entries (nightly iterates these; ad-hoc may target any repo by path)
[[repos]]
name                     = "pensiv"
path                     = "E:/pensiv"
system_prompt_addendum   = "E:/pensiv/.claude/CLAUDE.md"
include_extensions       = []
exclude_globs            = ["src/pensiv/_archive/**"]
include_tests            = false

# Per-repo lmstudio overrides (optional; merge over [lmstudio.*])
[repos.lmstudio.tasks.file_audit]
# temperature = 0.5
```

Config resolution order (later wins):
1. Built-in defaults.
2. `senex.config.toml`.
3. Per-repo entry (when audit target matches a repo entry's path).
4. CLI flags.
5. TUI launcher overrides.

The fully resolved config is captured in `<audit-dir>/config.snapshot.toml` for reproducibility.

## 7. Output Format

### 7.1 Per-File Report

Mirrors the empirical sample format the user has validated. Generated by `renderer.py` from the structured response — never directly produced by the LLM.

```
# Audit: <relpath/to/file.py>

**Date:** 2026-04-26  **Model:** google/gemma-4-26b-a4b  **Lens:** correctness
**Tokens in/out:** 8423 / 1276  **Latency:** 47s (thinking 32s, output 15s)
**GitNexus context:** cluster=`retrieval/factory` | callers d=1: 14 | processes: build_retrieval_pipeline, search_pipeline_init

<overall_assessment paragraph>

---

## Detailed Audit Findings

### A. <category-1> (High Priority)
- **<title>**
  - **Issue:** <issue>
  - **Why:** <why>
  - **Fix:** <fix>
  - **Confidence:** high | medium | low
  - **Location:** L42–L58, `validate_user`

### B. <category-2> (Medium Priority)
... (same shape)

### C. <category-3> (Low Priority)
... (same shape)

### Healthy
- **<title>** — <issue>

---

## Recommendations

#### Recommendation 1: <title>
<rationale>

```python
<code_snippet>
```

---

### Summary of Best Practices Applied
| Feature | Original Code | Recommended |
|---|---|---|
| <feature> | <original> | <recommended> |
```

If `min_confidence` is set to `"medium"` or `"high"`, lower-confidence findings are suppressed from the markdown but still recorded in `findings.json` with a `suppressed: true` flag.

### 7.2 Combined Report (`combined.md`)

```
# senex Audit: <repo> 2026-04-26

**Files audited:** 87  **Skipped:** 4  **Errored:** 1
**Duration:** 2h 14m  **Model:** google/gemma-4-26b-a4b
**Prompt hash:** sha256:<...>  **Config hash:** sha256:<...>

## Priority Rollup
- HIGH: 8
- MEDIUM: 34
- LOW: 56
- HEALTHY: 17

## Cross-Cutting Themes
1. **Repeated silent-exception swallowing in store/** — 4 files affected.
   <theme description>
2. ...

## Top Findings (sorted by priority)
| File | Priority | Title | Link |
|---|---|---|---|
| store/sqlite.py:142 | HIGH | Connection leak on rollback | [report](store/sqlite.md) |
| ... |

## Files With No Findings
- <path>
- ...

## Skipped Files
- <path>: too large
- <path>: encoding error

## Errored Files
- <path>: schema validation failure after retries (see <path>.RAW.json)

## Run Metadata
- LM Studio model fingerprint
- GitNexus index hash (per repo)
- Per-file durations
- Token totals (input / output / thinking)
```

### 7.3 `findings.json`

Canonical structured index — schema enforced at write time.

```json
{
  "schema_version": 1,
  "run": {
    "repo": "pensiv",
    "audit_dir": "E:/senex-audits/pensiv/2026-04-26",
    "model": "google/gemma-4-26b-a4b",
    "started_at": "2026-04-26T22:14:03Z",
    "duration_seconds": 8044,
    "config_hash": "sha256:...",
    "prompt_hash": "sha256:..."
  },
  "totals": {"high": 8, "medium": 34, "low": 56, "healthy": 17, "files": 87},
  "themes": [{"title": "...", "description": "...", "affected_files": ["..."]}],
  "findings": [
    {
      "id": "f-0001",
      "file": "store/sqlite.py",
      "category": "Robustness & Error Handling",
      "priority": "high",
      "title": "Connection leak on rollback",
      "issue": "...",
      "why": "...",
      "fix": "...",
      "confidence": "high",
      "location": {"line_start": 142, "line_end": 156, "symbol": "rollback_transaction"},
      "report_path": "store/sqlite.md",
      "suppressed": false
    }
  ]
}
```

### 7.4 `claude-handoff.md`

Structured prompt the user can paste into a Claude Code session to drive review/revision.

```
You are reviewing senex audit findings for <repo> from 2026-04-26.

Audit dir: E:/senex-audits/<repo>/2026-04-26
Findings index: E:/senex-audits/<repo>/2026-04-26/findings.json

For each finding in findings.json, decide:
  - APPLY    — implement the fix as specified
  - MODIFY   — implement an adjusted version (specify what changes)
  - DISMISS  — explain why this is not actionable (false positive, intentional, etc.)
  - DEFER    — record as known issue but don't fix in this pass

Process by priority: HIGH → MEDIUM → LOW.
Run gitnexus_impact before any code changes.
Use senex's per-file <file>.md for context.

Top findings:
1. [HIGH] store/sqlite.py:142 — Connection leak on rollback
   Fix: <fix>
   Confidence: high
2. ...
```

## 8. Error Handling & Resilience

### 8.1 Pre-flight (before file 1)

| Check | On failure |
|---|---|
| `senex.config.toml` parses; required fields present | Exit 2 |
| Repo path exists, is a directory, has `.git/` | Exit 2 |
| Output directory writable; ≥ 500MB free | Exit 2 |
| LM Studio reachable at `/v1/models` | Exit 3 |
| Selected model loaded | Exit 3 |
| `response_format=json_schema` works WITH thinking (probe) | Warn + fall back to `json_object` |
| Streaming works | Warn (TUI experience degraded) |
| `npx gitnexus list` includes repo OR repo lacks `.gitnexus/` | Warn, continue without graph context |
| GitNexus index < 24h old (`.gitnexus/meta.json` mtime) | Warn, suggest `npx gitnexus analyze` |
| `system_prompt_addendum` file exists if configured | Exit 2 |
| Required language anchor files exist for repo's languages | Warn |
| Sampling values within valid ranges (`temperature ∈ [0, 2]`, `top_p ∈ [0, 1]`, `top_k ∈ [0, 200]`, `min_p ∈ [0, 1]`, `repeat_penalty ∈ [0, 2]`, `max_tokens > 0`, `max_thinking_tokens > 0`) | Exit 2, name the offending field |

Exit codes: 0 success, 1 partial success (some files errored), 2 config/setup error, 3 external dependency error, 130 interrupted.

### 8.2 Per-file Recovery

| Failure | Recovery |
|---|---|
| File read error | Skip; write `<path>.SKIPPED.md`; emit FileError; continue. |
| GitNexus context fetch fails | Continue with empty graph context; per-file report notes absence. |
| LMS HTTP 5xx or timeout | 3 retries with backoff (5s, 15s, 45s). Then write `<path>.ERROR.md`; continue. |
| LMS HTTP 4xx | No retry; write `<path>.ERROR.md`; continue. |
| LMS connection lost | Pause loop; re-probe `/v1/models` every 10s; resume when reachable; sticky TUI banner. |
| Response valid JSON but schema mismatch | Retry once with stricter prompt; on second failure store raw response in `<path>.RAW.json` and write `<path>.ERROR.md`; continue. |
| Response invalid JSON | Same as schema mismatch. |
| `<think>` tags leaked into JSON | Strip; if stripping leaves invalid JSON, retry once with stricter prompt. |
| Thinking exceeded `max_thinking_tokens` | Treat as schema-mismatch path. |
| Renderer crash | Run-killing — code defect; crash with traceback; resume re-audits cleanly. |
| Disk full | Run-killing; crash early. |

Write-ahead invariant per file: report.md → `findings.json` patch → `checkpoint.json` update → `events.jsonl` append → bus emit. Crash mid-sequence → next resume re-audits this file (idempotent).

### 8.3 Run-Level

| Scenario | Behavior |
|---|---|
| Ctrl-C / SIGINT | Finish in-flight LMS call (≤10s), persist, exit 130. Resume works. |
| TUI crash | Auditor task isolated; render exceptions caught at Textual boundary; audit continues. User restarts via `senex view <audit-dir>`. |
| Process death | `senex audit --resume`: scans checkpoint, resumes, re-runs cross-cut and aggregation on merged set. |
| LM Studio model swap mid-run | Detected on next call; warning event; continue with new model; both models logged. |
| GitNexus index becomes stale mid-run | Tolerated. |
| Cross-cutting pass failure | Don't fail run; combined report notes gap. |
| Aggregation failure | Crash; recoverable via `senex aggregate <audit-dir>`. |

### 8.4 Observability

Three streams:
- `audit.log` — human-readable; INFO/WARN/ERROR prefixed; tailable.
- `events.jsonl` — machine-readable; schema-validated; one event per line; source of truth for replay.
- TUI sticky banner — surfaces last error and current LMS health.

`senex doctor` validates the full pre-flight set on demand without running an audit.

## 9. Testing Strategy

| Layer | Tool | Covers |
|---|---|---|
| Unit | pytest | walker, gitnexus_context, renderer, schema validator, config loader, checkpoint, event bus |
| Golden file | pytest + diff | renderer: structured-input → markdown; combined-report formatter |
| Recorded LMS | pytest + JSON fixtures | auditor coroutine end-to-end with replayed responses; thinking content captured; tag stripping |
| Live LMS smoke | pytest `@live` marker | one tiny audit on a 3-file fixture repo with real LM Studio (manual / pre-release only) |
| TUI smoke | Textual `Pilot` | launcher → monitor screen renders against stub auditor |

Required tests:
1. Walker correctness (gitignore + extensions + size cap + per-repo overrides).
2. Checkpoint resume (kill mid-run, restart, no re-audit, cross-cut/aggregation re-run).
3. Schema validation (valid / missing required / wrong enum / extra / malformed JSON).
4. Renderer round-trip (every schema field appears in markdown).
5. Event bus invariants (write-ahead order enforced).
6. Pre-flight checks (each produces correct exit code).
7. Per-language anchor selection.
8. Per-repo addendum loading.
9. Inference controls merging (defaults → file → repo → TUI overrides).
10. `senex doctor` (pass on clean setup; fail correctly on each pre-flight failure).
11. Stream parser (events emit in correct phase order; thinking content captured).
12. Tag stripping (`<think>...</think>` removal).
13. Post-hoc schema validation (`json_object` fallback path).

Coverage targets: 85% on `auditor.py`, `renderer.py`, `walker.py`, `checkpoint.py`, `events.py`. 60% elsewhere.

`@live` runs manually before release tags; CI runs everything else.

Explicitly NOT tested: LLM finding *quality*. That's evaluated by reading actual reports — tests verify pipeline transport.

## 10. CLI Surface

```
senex audit <repo-path>             # ad-hoc audit
senex audit --nightly               # iterates [[repos]] from config
senex audit --resume <repo-path>    # explicit resume
senex audit --no-tui ...            # headless mode
senex view [<audit-dir>]            # replay TUI over completed run (auto-detects latest if omitted)
senex doctor                        # pre-flight diagnostic
senex aggregate <audit-dir>         # re-run aggregation phase only
senex --version
senex --help

# Common flags
--config <path>          # override default config location
--model <id>             # override [lmstudio.model]
--lens <name>            # v1: only "correctness" wired
--min-confidence <level> # all | medium | high
--include-tests          # override [lens.include_tests]
```

`scripts/run_senex.bat` is the Windows Scheduled Task entrypoint:

```bat
@echo off
title senex -- Nightly Audit
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
python -m senex audit --nightly
```

## 11. Security & Privacy

- Source code is sent to a *local* LM Studio instance — never leaves the machine.
- API key field in config is only used to satisfy the OpenAI client requirement; LM Studio ignores it.
- `findings.json` and reports may contain code excerpts. Output dir defaults to a centralized location outside any audited repo, and is gitignored at the user level.
- `senex doctor` does not print secrets.
- No telemetry.

## 12. Future Work (v2+)

- `senex fix <audit-dir>` — LM-Studio-driven patch generation per finding.
- Multi-lens audits (security, performance, maintainability) via separate prompts and per-lens reports.
- Concurrent file audits (when remote inference becomes available).
- `senex daemon start <repo>` — detached background mode with `senex monitor` reattaching live.
- Diff-mode audit: only audit files changed since the last run.
- Pre-commit hook: audit only the staged files.
- Web viewer over `findings.json` for browsing.
- Cross-run diffing in the combined report.

## 13. Glossary

- **Audit run** — one execution of `senex audit`, producing one audit-dir.
- **Audit dir** — `E:\senex-audits\<repo>\<YYYY-MM-DD>\`; output of one run.
- **Per-file report** — `<relpath>/<file>.md`; one Markdown report per audited file.
- **Combined report** — `combined.md`; run-level summary including cross-cutting themes.
- **Findings index** — `findings.json`; canonical structured record of all findings.
- **Claude handoff** — `claude-handoff.md`; prompt artifact for downstream Claude Code review.
- **Lens** — the audit dimension (v1: correctness only).
- **Graph context** — the GitNexus-derived awareness block (cluster + callers + processes).
- **Addendum** — per-repo file appended to the system prompt.
- **Language anchor** — short per-language paragraph appended after the addendum.
- **Thinking trace** — the model's reasoning content captured to `<file>.thinking.md` when `save_traces=true`.
