# Milestone 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M1 delivers Layer 0 of the senex architecture — the foundational primitives (config, events, redaction, lens, runlock, checkpoint, schemas) that every later milestone imports. These modules have **no internal senex dependencies** other than each other; they are the bedrock of the system. Get them right; everything builds on them.

**Architectural intent:** Layer 0 modules are pure data + protocol definitions. They do not perform I/O against LM Studio, do not subprocess to GitNexus, and do not orchestrate phases. They define the *shapes* (pydantic models, JSON schemas, dataclasses) and *primitives* (event bus, redactor regexes, file-based locks) the rest of the codebase consumes.

## Prerequisites

- **Completed milestones:** None — this is the first milestone.
- **Required modules from prior work:** None.
- **Required tools/state:**
  - Python 3.11+ on PATH
  - `pip` and `venv` available
  - Spec file at `E:/senex/docs/superpowers/specs/2026-04-26-senex-audit-tool-design.md` (referenced for verbatim copy of schemas, system prompt, sample config)

## Deliverable

This milestone creates the following files:

- `pyproject.toml`, `requirements.txt` — package metadata + dependency pins
- `senex/__init__.py` — version + package init
- `senex/config.py` — `SenexConfig` pydantic v2 model + `load_config()` + `resolve_config()` (deep-merge precedence)
- `senex.config.toml.example` — annotated sample config
- `senex/events.py` — 35+ event pydantic models + `EventBus` + `CommandBus`
- `senex/secret_redactor.py` — `SecretRedactor` with named patterns + `redact_dict()`
- `senex/lens.py` + `senex/lens/correctness/{lens.toml,tools.toml}` — `Lens` dataclass + `Lens.load(name)`
- `senex/runlock.py` — `RunLock` interprocess refcount file lock with stale-PID prune
- `senex/checkpoint.py` + `senex/schema/checkpoint.schema.json` — `Checkpoint` state machine
- `senex/schema/{audit,crosscut,compaction,findings_index,events}.schema.json` — 5 JSON Schemas

## Downstream consumers

- **M2** needs `senex/walker.py` to import `events` (FileSkipped/SymlinkSkipped emission); `graph_awareness.py` needs `events`.
- **M3** needs `events`, `secret_redactor`, `config` (LmStudioCfg), `runlock` (fingerprint comparison).
- **M4** needs `runlock`, `events` (lifecycle event types), `config` (LifecycleCfg).
- **M5** needs `lens` (tool-pack resolution), `config` (ToolsCfg), `events` (ToolCall/Result/Error).
- **M6** needs `events` (CompactionTriggered/Complete/Error), `config` (CompactionCfg).
- **M7** needs the JSON schemas (renderer validates against `audit_response.schema.json`).
- **M8** needs `checkpoint` (phase tracking), `events` (RunStart/RunComplete), all of Layer 0.
- **M9** needs `events` (subscribers consume the event types).
- **M10** needs `config` (CLI flags merge into resolved config).

## Spec sections referenced

- §3.1 File Layout — verifies module names match spec
- §3.2 Module Boundaries — Layer 0 isolation
- §5.0 Lens Abstraction — `Lens` shape, `lens.toml` + `tools.toml` separation
- §5.4 Audit response schema — copied verbatim into `senex/schema/audit_response.schema.json`
- §5.4.1 Crosscut response schema — copied verbatim
- §5.5.1 Compaction response schema — copied verbatim
- §5.5.2.2 Runlock semantics — refcount file format, stale-PID prune
- §5.6 Events table — 35+ event types
- §5.6.1 Event bus semantics — per-subscriber backpressure (DiskWriter blocks, TUI drops Tick)
- §5.6.2 Command bus — TUI → auditor channel
- §5.10 Secret Redactor — pattern set
- §6 Configuration — TOML schema, `extra="forbid"`
- §6.1 Configuration Resolution — defaults → file → per-repo → CLI → TUI deep-merge order
- §7.3 findings.json — schema basis for `findings_index.schema.json`
- §SEC-2 / L2 — `redact_dict` keys for `config.snapshot.toml`
- ARCH-15 — checkpoint required fields
- ARCH-4 — resume hash discipline (`is_compatible(other_hashes)`)
- API-3 / M11 — `extra="forbid"` for unknown TOML keys
- SCHEMA-5 — `additionalProperties: false`, `location` `oneOf`

## Key contracts (functions/classes you will write)

- **`SenexConfig`** — root pydantic v2 model with `extra="forbid"`. Nested: `OutputCfg`, `LensCfg`, `WalkerCfg`, `LmStudioCfg` (with `SamplingCfg`, `ThinkingCfg`, `ToolsCfg`, `CompactionCfg`, `LifecycleCfg`, `TasksCfg`), `RepoCfg`, `CrosscutCfg`.
- **`load_config(path)`** — TOML → `SenexConfig`. Unknown key → ValueError with Levenshtein hint.
- **`resolve_config(base, repo_path, cli_overrides, tui_overrides)`** — deep-merge in spec §6.1 order.
- **`BaseEvent`** + 35+ subclasses with `Field(default="literal")` discriminator.
- **`EventBus.subscribe(name, capacity)`** / **`EventBus.publish(event)`** — bounded async queues, per-subscriber slow policy.
- **`CommandBus`** — separate channel; `Command` model: `type`, `target`, `ts`.
- **`SecretRedactor.redact(text)`** — applies all named patterns in priority order.
- **`SecretRedactor.redact_dict(d, keys=...)`** — for config snapshotting.
- **`Lens.load(name)`** — reads `senex/lens/<name>/{lens.toml,tools.toml}`, validates, returns `Lens` with `fingerprint = sha256(concatenated bytes)`.
- **`RunLock.acquire(fingerprint, run_id, pid, loaded_by_us)`** / **`RunLock.release(fingerprint, run_id)`** — atomic file refcount with stale-PID prune.
- **`Checkpoint.create(audit_dir, ...)`** / **`Checkpoint.mark_done(file)`** / **`Checkpoint.set_phase(name, status)`** — atomic write-rename pattern; `is_compatible(other_hashes)` for resume.

## Watch-outs

- **`extra="forbid"` is non-negotiable** (§API-3 / M11). Drift between config schema and example will fail tests; the agent who edits one must edit the other.
- **Event seq must be monotonic per run.** A single `_seq_counter` lives on the bus; do not let subclasses generate their own seq.
- **Per-subscriber backpressure policy is asymmetric:** DiskWriter blocks the publisher (write-ahead is critical for crash recovery); TUI drops Tick events; Metrics drops oldest. Get this wrong and the TUI hangs the auditor under load.
- **Runlock atomicity:** read+modify+write must hold the `portalocker` advisory lock for the entire critical section. Tmp+rename is the only safe pattern for the data file write.
- **`detect-secrets` redactor priority:** longer/more-specific patterns first (PEM block, JWT, AWS key) before generic `SECRET_KEY=...` env-style. Otherwise generic eats the specifics.
- **Schema files are authoritative.** Spec §5.4 is verbatim source; do not paraphrase. `additionalProperties: false` on every object; `location` uses `oneOf` per §SCHEMA-5.

## Patterns to follow

- **TDD throughout** — every task starts with a failing test before implementation.
- **Atomic writes** — every file the system writes (lockfile, checkpoint, partial findings) uses tmp+rename. No exceptions.
- **Pydantic v2** — `model_validate` for parsing, `model_dump` for serialization, `Field(default=...)` for discriminators, `Discriminator` from `pydantic` for tagged unions.
- **Spec-verbatim copies:** schemas in §5.4 / §5.4.1 / §5.5.1 are copied byte-for-byte; sample config in §6 is copied as `senex.config.toml.example`.

## Tasks

### Task 1.1: Project scaffolding (pyproject.toml, package init, requirements.txt)

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `senex/__init__.py`

- [ ] **Step 1.1.1: Write `pyproject.toml`** with `[project]` metadata, `name = "senex"`, `version = "0.1.0"`, `requires-python = ">=3.11"`, `[project.scripts] senex = "senex.cli:main"`, dependencies pinned per spec §5.5 (`openai>=1.50,<2.0`, `pydantic>=2.5,<3`, `textual>=0.50`, `tiktoken`, `portalocker`, `detect-secrets`, `regex`, `httpx`).

- [ ] **Step 1.1.2: Write `requirements.txt`** mirroring pyproject deps for the `.bat` invocation path (Windows scheduled task uses pip install -r).

- [ ] **Step 1.1.3: Write `senex/__init__.py`** with `__version__ = "0.1.0"` and a single `__all__` listing public re-exports (empty for now).

- [ ] **Step 1.1.4: Verify** `python -m pip install -e .` succeeds. Run `python -c "import senex; print(senex.__version__)"` → expect `0.1.0`.

- [ ] **Step 1.1.5: Commit** `feat(M1): project scaffolding (pyproject, requirements, package init)`.

### Task 1.2: Config models (pydantic v2)

**Files:**
- Create: `senex/config.py`
- Create: `tests/unit/test_config.py`
- Create: `senex.config.toml.example`

- [ ] **Step 1.2.1: Write failing test** `tests/unit/test_config.py::test_load_default_config_succeeds` — asserts `load_config(Path("senex.config.toml.example"))` returns a `SenexConfig` with `lens.default == "correctness"` and `lmstudio.model == "google/gemma-4-26b-a4b"`.

- [ ] **Step 1.2.2: Implement `SenexConfig`** in `senex/config.py` as a pydantic v2 model nested per spec §6: `OutputCfg`, `LensCfg`, `WalkerCfg`, `LmStudioCfg` (with `SamplingCfg`, `ThinkingCfg`, `ToolsCfg`, `CompactionCfg`, `LifecycleCfg`, `TasksCfg`), `RepoCfg`, `CrosscutCfg`. Set `extra="forbid"` to fail on unknown keys (spec API-3 / M11).

- [ ] **Step 1.2.3: Write `senex.config.toml.example`** verbatim from spec §6 sample (with `pensiv` repo entry pre-populated; addendum field optional; per-repo `lmstudio` overrides commented out).

- [ ] **Step 1.2.4: Implement `load_config(path)`** — parses TOML via `tomllib`, validates against `SenexConfig`, returns model. On unknown key, raises with field name + closest valid name (Levenshtein hint).

- [ ] **Step 1.2.5: Implement `resolve_config(base, repo_path, cli_overrides, tui_overrides)`** — deep-merge per §6.1: defaults → file → per-repo → CLI → TUI. Uses pydantic's `model_dump()` + dict merge, then `model_validate()` for the result.

- [ ] **Step 1.2.6: Add tests** for: unknown key → ValueError with hint; per-repo override correctly shadows global; `seed = "@random"` parsed as sentinel; sampling out-of-range (e.g., `temperature = 3.0`) → ValueError.

- [ ] **Step 1.2.7: Run** `pytest tests/unit/test_config.py -v` → all green.

- [ ] **Step 1.2.8: Commit** `feat(M1): config loader with strict pydantic validation`.

### Task 1.3: Event types (pydantic models)

**Files:**
- Create: `senex/events.py`
- Create: `tests/unit/test_events.py`

- [ ] **Step 1.3.1: Write failing test** `test_event_serialization_roundtrip` — instantiates each of the 30+ events from §5.6, dumps to JSON, parses back, asserts equality.

- [ ] **Step 1.3.2: Implement `BaseEvent`** with required fields per §5.6: `v: int = 1`, `type: str`, `ts: datetime`, `seq: int`, `run_id: str`. Use pydantic's `Discriminator` on `type`.

- [ ] **Step 1.3.3: Implement all 35+ event subclasses** as a typed union (spec §5.6 table):
  - Run: `RunStart`, `DiscoveryComplete`, `RunComplete`
  - File phase: `FileStart`, `FileContextBuilt`, `FileLLMCall`, `ThinkingStarted/Tick/Complete`, `OutputStarted/Tick/Complete`, `FileComplete`, `FileError`
  - Tool: `ToolCall`, `ToolResult`, `ToolError`, `ToolBudgetExhausted`
  - Compaction: `CompactionTriggered/Complete/Error`
  - Lifecycle: `ModelLoadRequested/Started/Complete/Failed`, `ModelUnloadStarted/Complete/Skipped/Failed`, `ModelFingerprintChanged`, `RunLockAcquired/Released`
  - Walker: `SymlinkSkipped`, `SuspiciousEmptyFinding`
  - Crosscut: `CrosscutStart/Complete`
  - Each emits its `type` literal automatically via `Field(default="...")`.

- [ ] **Step 1.3.4: Implement `EventBus`** class:
  - `__init__(default_capacity=1024)` — per-subscriber bounded queue store.
  - `subscribe(name, capacity=None) -> asyncio.Queue` — returns subscriber's queue.
  - `publish(event)` — fans out; per-subscriber slow policy via `_dispatch_to_subscriber()` (DiskWriter blocks the publisher; TUI drops Tick events; Metrics drops oldest Tick).
  - `_seq_counter` monotonic per run.

- [ ] **Step 1.3.5: Implement `CommandBus`** — separate channel for TUI → auditor: `Command` model with `type: "Pause"|"Resume"|"Skip"|"Rerun"|"Quit"`, `target: str|None`, `ts`. `auditor` polls at well-defined points (per-file iteration boundary + between phases).

- [ ] **Step 1.3.6: Tests** for: per-subscriber backpressure (TUI drops ticks, DiskWriter blocks), seq monotonicity, type discriminator round-trip on unknown type → ValidationError.

- [ ] **Step 1.3.7: Run** `pytest tests/unit/test_events.py -v` → green.

- [ ] **Step 1.3.8: Commit** `feat(M1): event taxonomy + bounded async event bus`.

### Task 1.4: Secret redactor

**Files:**
- Create: `senex/secret_redactor.py`
- Create: `tests/unit/test_secret_redactor.py`

- [ ] **Step 1.4.1: Failing tests** for each pattern:
  - AWS access key: `AKIA...........` → `[REDACTED:aws_access_key]`
  - GitHub PAT: `ghp_<40 chars>` → `[REDACTED:github_pat]`
  - OpenAI/Anthropic key: `sk-...`/`sk-ant-...` → `[REDACTED:llm_api_key]`
  - JWT: three base64 segments separated by `.` → `[REDACTED:jwt]`
  - PEM block: `-----BEGIN ...-----...-----END ...-----` → `[REDACTED:pem]`
  - Generic env-style: `SECRET_KEY=anything-with-secret-or-token-or-password` → `[REDACTED:env_secret]`

- [ ] **Step 1.4.2: Implement `SecretRedactor`** with method `redact(text: str) -> str`. Compile patterns once at init. Apply in priority order. Each match wrapped with `[REDACTED:<name>]`.

- [ ] **Step 1.4.3: Implement `redact_dict(d, keys=("api_key", "*_token", "*_secret", "password*"))`** for `config.snapshot.toml` snapshotting (spec §SEC-2 / L2).

- [ ] **Step 1.4.4: Run** tests → green.

- [ ] **Step 1.4.5: Commit** `feat(M1): secret redactor with named pattern set`.

### Task 1.5: Lens loader

**Files:**
- Create: `senex/lens.py`
- Create: `senex/lens/correctness/lens.toml`
- Create: `senex/lens/correctness/tools.toml`
- Create: `tests/unit/test_lens.py`

- [ ] **Step 1.5.1: Write `senex/lens/correctness/lens.toml`**:
  ```toml
  schema_version = 1
  name = "correctness"
  version = "v1"
  description = "Senior-developer correctness review lens"
  system_prompt = "system_senior_dev.md"
  response_schema = "audit_response.schema.json"
  crosscut_prompt = "cross_cutting.md"
  crosscut_schema = "crosscut_response.schema.json"
  ```

- [ ] **Step 1.5.2: Write `senex/lens/correctness/tools.toml`** with `enabled_tools = ["gitnexus_query", "gitnexus_context", "gitnexus_impact", "read_file", "grep", "search_code"]`.

- [ ] **Step 1.5.3: Failing test** `test_load_correctness_lens` — `Lens.load("correctness")` returns object with `.name == "correctness"`, `.tools == [...6 tools...]`, prompt+schema paths resolved.

- [ ] **Step 1.5.4: Implement `Lens`** dataclass: `name`, `version`, `system_prompt_path`, `response_schema_path`, `crosscut_prompt_path`, `crosscut_schema_path`, `tools: list[str]`. `load(name)` reads `senex/lens/<name>/lens.toml` + `tools.toml`, validates, returns. Hash all loaded files into `lens.fingerprint` (sha256 of concatenated bytes).

- [ ] **Step 1.5.5: Run** tests → green.

- [ ] **Step 1.5.6: Commit** `feat(M1): Lens loader with versioned prompts + tool packs`.

### Task 1.6: Runlock (file-based interprocess refcount)

**Files:**
- Create: `senex/runlock.py`
- Create: `tests/unit/test_runlock.py`

- [ ] **Step 1.6.1: Failing tests** covering spec §5.5.2.2 + §LIFE-tests:
  - acquire on fresh fingerprint → file created with single holder
  - acquire twice (different run_ids) → file has 2 holders, returns count=2
  - release → file has 1 holder; second release → file deleted
  - acquire with stale PID entry → stale pruned before append
  - corrupt JSON → file renamed to `.corrupt-<ts>` and recreated

- [ ] **Step 1.6.2: Implement `RunLock`** with class methods:
  - `_lock_path(fingerprint, root) -> Path` — defaults to `~/.senex/locks/<fp>.lock`
  - `acquire(fingerprint, run_id, pid, loaded_by_us) -> int` — uses `portalocker` for advisory lock during read+modify+write
  - `release(fingerprint, run_id) -> int` — returns remaining count
  - `_prune_stale(holders) -> list` — drops entries where `psutil.pid_exists(pid)` is False (use stdlib alt if psutil too heavy: `os.kill(pid, 0)` + ProcessLookupError handling; on Windows use ctypes)
  - All file writes are atomic via `tmp + rename`

- [ ] **Step 1.6.3: Run** tests → green; manually test concurrency by spawning two threads.

- [ ] **Step 1.6.4: Commit** `feat(M1): runlock interprocess refcount with stale-PID prune`.

### Task 1.7: Checkpoint state machine

**Files:**
- Create: `senex/checkpoint.py`
- Create: `senex/schema/checkpoint.schema.json`
- Create: `tests/unit/test_checkpoint.py`

- [ ] **Step 1.7.1: Write JSON Schema** at `senex/schema/checkpoint.schema.json` per spec ARCH-15: `schema_version`, `run_id`, `current_phase`, `phase_status`, `phase_artifact_hashes`, `completed_files`, `config_hash`, `prompt_hash`, `model_fingerprint`, `tool_pack_hash`, `lens_version`, `compaction_prompt_hash`.

- [ ] **Step 1.7.2: Failing tests**:
  - `Checkpoint.create(audit_dir, ...)` → file exists with all required fields
  - `Checkpoint.mark_done(audit_dir, "src/foo.py")` → atomic write; resume reads same value back
  - `Checkpoint.set_phase(audit_dir, "crosscut", "in_progress")` → phase_status updated
  - Crash mid-write simulated by raising in atomic_write → original file unchanged (uses tmp+rename pattern)

- [ ] **Step 1.7.3: Implement** with atomic-write-rename pattern; `validate()` against schema on load; `is_compatible(other_hashes)` for resume hash discipline (§ARCH-4).

- [ ] **Step 1.7.4: Commit** `feat(M1): checkpoint state machine with phase tracking`.

### Task 1.8: Schema files (5 JSON schemas)

**Files:**
- Create: `senex/schema/audit_response.schema.json`
- Create: `senex/schema/crosscut_response.schema.json`
- Create: `senex/schema/compaction_response.schema.json`
- Create: `senex/schema/findings_index.schema.json`
- Create: `senex/schema/events.schema.json`
- Create: `tests/unit/test_schemas.py`

- [ ] **Step 1.8.1: Copy `audit_response.schema.json` verbatim from spec §5.4** (with `additionalProperties: false`, `location` required with `oneOf` per §SCHEMA-5).

- [ ] **Step 1.8.2: Copy `crosscut_response.schema.json` verbatim from spec §5.4.1** (themes array with id pattern `^t-[a-f0-9]{12}$`).

- [ ] **Step 1.8.3: Copy `compaction_response.schema.json` verbatim from spec §5.5.1** (evidence_summary, key_findings_so_far, unanswered_questions).

- [ ] **Step 1.8.4: Write `findings_index.schema.json`** per spec §7.3 (run, totals, themes, findings array with finding objects).

- [ ] **Step 1.8.5: Write `events.schema.json`** that union-validates all 35+ event types (use a `oneOf` discriminated by `type`).

- [ ] **Step 1.8.6: Failing tests** that load each schema with `jsonschema.Draft202012Validator` and check_schema → no ValidationError.

- [ ] **Step 1.8.7: Run** tests → green.

- [ ] **Step 1.8.8: Commit** `feat(M1): JSON schemas for all artifacts`.

## Acceptance criteria

- `python -m pip install -e .` succeeds; `python -c "import senex; print(senex.__version__)"` prints `0.1.0`.
- `pytest tests/unit/ -v` is 100% green (8 task-level test files).
- `python -c "from senex.config import load_config; load_config('senex.config.toml.example')"` succeeds with no errors.
- `python -c "from senex.lens import Lens; l = Lens.load('correctness'); print(l.tools)"` prints the 6-tool list.
- All five JSON schemas validate against `Draft202012Validator.check_schema()` with no errors.
- Round-trip serialization works for every event subclass (instantiate → JSON → parse → equal).
- Two concurrent threads acquiring `RunLock` on the same fingerprint produce a single lockfile with 2 holders; releasing both deletes the file.
