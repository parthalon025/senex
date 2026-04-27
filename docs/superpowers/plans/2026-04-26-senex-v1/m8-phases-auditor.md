# Milestone 8: Phases + Auditor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M8 wires every prior milestone (M1-M7) together into the orchestrator. Five phases (Preflight, Discovery, FileAudit, Crosscut, Aggregate) each implement the `Phase` protocol; `run_audit()` is the async coroutine that walks them in order, respecting checkpoints and the command bus. After M8, you have a fully functional headless audit — no TUI, no CLI yet, but `await run_audit(...)` produces a complete audit directory.

**Architectural intent (spec §ARCH-3):** Phases are *resumable units of work*. Each is an object satisfying the `Phase` protocol with `read_state` / `do_work` / `write_state`, all checkpoint-aware. The auditor (`run_audit`) is intentionally dumb: it iterates phases, drives the checkpoint state machine (§ARCH-15), checks the command bus between them, and trusts each phase to handle its own per-file errors. `FileAuditPhase` is "the big one" because it owns the per-file recovery matrix from spec §8.2.

**Import boundary (spec §3.2 — STRICT):** `senex/phases/*` and `senex/auditor.py` MUST NOT import from `senex/tui/*`. The auditor publishes events on the bus; the TUI subscribes. This file defines the producer side only. Test enforced via `tests/unit/test_import_boundaries.py` (already present from M1).

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration, M4 Lifecycle, M5 Tool Framework, M6 Compaction, M7 Renderer + Aggregator
- **Required modules from prior work (named imports — keep this list authoritative):**
  - `senex/config.py` — `SenexConfig`, `Lens` (M1)
  - `senex/checkpoint.py` — `Checkpoint.create_or_resume()`, `set_phase()`, `mark_done()`, `phase_status` dict, `completed: set[str]` (M1)
  - `senex/events.py` — `EventBus`, `CommandBus`, all event dataclasses: `RunStart`, `RunComplete`, `DiscoveryStart`, `DiscoveryComplete`, `FileStart`, `FileContextBuilt`, `FileComplete`, `FileError`, `CrosscutStart`, `CrosscutComplete`, `AggregateStart`, `AggregateComplete`, `PreflightWarning` (M1)
  - `senex/walker.py` — `Walker.discover()` for `DiscoveryPhase` (M2)
  - `senex/graph_awareness.py` — `GraphContextProvider.fetch()` for `FileAuditPhase` (M2)
  - `senex/prompts/_anchor_loader.py` — `build_user_prompt()`, `inject_strict_addendum()` for `FileAuditPhase` (M2)
  - `senex/lmstudio_client.py` — `LMStudioClient`, `client.count_tokens()`, `client.context_window`; exceptions `LMSConnectionLost`, `LMSError`, `LMSResponseSchemaInvalid`, `LMSResponseInvalidJSON`, `FingerprintChanged` (M3)
  - `senex/lmstudio_lifecycle.py` — `Lifecycle.acquire()`, `release()`, `acquire_for_resume()` (M4); ALL invoked via `Lifecycle.acquire_or_resume()` async context manager (Task 8.7.2)
  - `senex/tools/registry.py` + `senex/tools/loop.py` — `ToolLoop.run()`; raises `ToolDispatchFailed` (M5)
  - `senex/compaction.py` — `Compactor`; raises `CompactionLoopExceeded` (M6)
  - `senex/renderer.py` — `Renderer.render_file()`, `Renderer.render_combined()`, `Renderer.write_file_atomic()` (M7)
  - `senex/findings_partial.py` — `PartialWriter.append()`, `make_finding_records()` (M7)
  - `senex/findings_aggregator.py` — `Aggregator.run()` (M7)
  - `senex/cross_cutting.py` — `CrossCutter.run()` (M7)
  - `senex/handoff.py` — `HandoffWriter.write()` (M7)
- **Required test infrastructure:**
  - `tests/recorded/` LMS replay infrastructure from M3 — every M8 end-to-end test runs offline against recorded fixtures (no live LMS).
  - `tests/fixtures/repos/tiny_python/` from M2.
  - `tests/fixtures/recorded_responses/preflight/*` — prerecorded LMS handshake fixtures for `check_lms_reachable` / `check_schema_with_thinking` / `check_streaming` (create as part of Task 8.2.1).

## Deliverable

This milestone creates the following files:

- `senex/phases/__init__.py` — re-exports `Phase`, all 5 phase classes, all named exceptions
- `senex/phases/base.py` — `Phase` protocol + named exceptions (`PreflightFailure`, `PhaseAborted`, `ResumeIncompatible`, `RenderFatal`, `AggregateFailed`)
- `senex/phases/preflight.py` — `PreflightPhase` + 14 individual check functions + `CheckResult` enum
- `senex/phases/discovery.py` — `DiscoveryPhase` (wraps `Walker`)
- `senex/phases/file_audit.py` — `FileAuditPhase` (per-file loop with full §8.2 recovery matrix)
- `senex/phases/crosscut.py` — `CrosscutPhase` (wraps `CrossCutter`)
- `senex/phases/aggregate.py` — `AggregatePhase` (`Aggregator` + `HandoffWriter` + `Renderer.render_combined`)
- `senex/auditor.py` — `async def run_audit(repo, config, lens, bus, command_bus, resume=False) -> int`
- `tests/unit/test_phase_protocol.py` — `runtime_checkable` compliance for all 5 phases
- `tests/unit/test_preflight_checks.py` — one test per check function (14 tests)
- `tests/unit/test_phases_discovery.py`, `test_phases_file_audit.py`, `test_phases_crosscut.py`, `test_phases_aggregate.py`
- `tests/unit/test_auditor_resume.py` — checkpoint state-machine + hash mismatch refusal
- `tests/unit/test_auditor_lifecycle.py` — exception in any phase still releases lifecycle
- `tests/recorded/test_run_audit_e2e.py` — full end-to-end against recorded LMS

## Downstream consumers

- **M9** TUI's "Start audit" button launches `run_audit()` as a Textual worker task. (TUI imports auditor; auditor MUST NOT import TUI.)
- **M10** CLI `senex audit` is a thin wrapper that constructs the bus + lifecycle + lens + config and calls `run_audit()`.
- **M10** CLI `senex doctor` reuses `PreflightPhase` and the individual `check_*` functions.
- **M10** CLI `senex aggregate` re-runs `AggregatePhase` against an existing audit dir (recovers from `AggregateFailed`).
- **M10** Live gates 13a/13c exercise full `run_audit()` flow + resume.

## Spec sections referenced

- §3 Architecture — async-heavy boundary, import rules (`tui/*` forbidden here)
- §4 Data flow phases 1-5 — overall audit pipeline
- §5.5.2.1 Lifecycle phase block — context-manager bracketing
- §5.5.2.7 Resume scenarios
- §5.6.1 Bus semantics — event publishing rules, monotonic `seq`
- §5.6.2 Command bus — Pause/Resume/Skip/Rerun/Quit handling at iteration boundaries
- §6 Testing — recorded LMS, fixture repos, per-branch coverage
- §8.1 Pre-flight — full checklist with documented exit codes (rows mapped to functions in Task 8.2)
- §8.2 Per-file Recovery — error matrix
- §8.3 Run-Level — preflight failure, lifecycle handshake fail, fingerprint drift
- §8.4 Observability — events at phase boundaries
- §8.5 Reproducibility & Resume Compatibility — `config_hash`, `prompt_hash`, `model_fingerprint`, `tool_pack_hash`, `lens_version`, `compaction_prompt_hash`
- §11 Git — commit message conventions
- §ARCH-3 Phases as objects — protocol contract
- §ARCH-13 Renderer crash NOT run-killing — write `<file>.ERROR.md` and continue
- §ARCH-15 Checkpoint state machine — `pending` → `in_progress` → `complete`
- §SEC-1 Addendum safety — path resolution + `is_relative_to` + symlink refusal

## Named exceptions (Task 8.1, declared in `senex/phases/base.py`)

```python
class PreflightFailure(Exception):
    def __init__(self, exit_code: int, message: str, check_name: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message
        self.check_name = check_name

class PhaseAborted(Exception):
    """Raised when a phase aborts due to an unrecoverable condition specific to that phase."""

class ResumeIncompatible(Exception):
    """Raised when checkpoint hashes don't match current config/prompts/model. exit_code=4."""

class RenderFatal(Exception):
    """Truly unrecoverable run-killing render error (disk full, audit_dir gone). NOT for per-file render failures (those go to <file>.ERROR.md per §ARCH-13)."""

class AggregateFailed(Exception):
    """Aggregate phase failed but is resumable via `senex aggregate`. exit_code=5."""
```

Tests assert each exception's `exit_code` is part of the public contract (locked by `tests/unit/test_exit_codes.py`).

## Phase protocol (Task 8.1)

```python
from typing import Protocol, runtime_checkable, Any
from pathlib import Path

@runtime_checkable
class Phase(Protocol):
    name: str  # one of: "preflight", "discovery", "file_audit", "crosscut", "aggregate"

    async def read_state(self, audit_dir: Path) -> Any: ...
    async def do_work(
        self,
        state: Any,
        lens: "Lens",
        config: "SenexConfig",
        bus: "EventBus",
        command_bus: "CommandBus",
    ) -> Any: ...
    async def write_state(self, audit_dir: Path, state: Any) -> None: ...
```

- `read_state` and `write_state` are async to keep the I/O boundary uniform; for cheap synchronous reads (e.g., reading checkpoint dict) the implementation may simply `return value` without `await`.
- Each phase is **stateless across `do_work` calls**; state in/out flows through arguments. The auditor owns persistence between phases.
- Tests: `assert isinstance(PreflightPhase(), Phase)` for all 5 phases. (`runtime_checkable` makes this work without ABC inheritance.)

## Key contracts

- **`PreflightPhase.do_work()`** — runs each spec §8.1 check; raises `PreflightFailure(exit_code, message, check_name)` on first hard FAIL; collects WARN results and emits one `PreflightWarning` event per warning.
- **`DiscoveryPhase.do_work()`** — calls `Walker.discover()`; emits exactly one `DiscoveryStart` and one `DiscoveryComplete`. State out: `{"files": list[Path], "skipped": list[tuple[Path, str]]}`.
- **`FileAuditPhase.do_work()`** — the per-file loop with full §8.2 recovery (see Task 8.4.2).
- **`CrosscutPhase.do_work()`** — reads `findings.partial.jsonl`, calls `CrossCutter.run()`, writes `themes.json`, emits `CrosscutStart`/`CrosscutComplete`. On crosscut failure, returns `None` (combined report shows the gap) — does NOT raise.
- **`AggregatePhase.do_work()`** — calls `Aggregator.run()` + `HandoffWriter.write()` + `Renderer.render_combined()`; writes all atomically. Raises `AggregateFailed` on failure (recoverable via `senex aggregate`).
- **`async run_audit(repo, config, lens, bus, command_bus, resume=False) -> int`** — coroutine that iterates phases via the checkpoint state machine, brackets the run with `Lifecycle.acquire_or_resume()`, emits `RunStart`/`RunComplete`. Returns 0 on full success, 1 on partial success (some files skipped/errored but run finished), nonzero exit codes on hard failure (see Task 8.7).

## Watch-outs

- **`FileAuditPhase` recovery matrix is non-trivial.** Spec §8.2 has multiple branches; each must be tested with fixture LMS responses + crafted file inputs (see Task 8.4.3).
- **Command bus is checked at iteration boundaries only.** Per-file boundary in `FileAuditPhase` (canonical) and between phases in `run_audit`. Mid-LLM-call interrupts are NOT safe; pause means "finish current file then suspend." See Task 8.4.2 for exact poll point.
- **Resume logic lives in the auditor, not in `Checkpoint`.** `Checkpoint.create_or_resume()` returns the existing checkpoint if hashes match (per §8.5) or a fresh one. The auditor uses `checkpoint.phase_status[phase.name] == "complete"` to skip already-completed phases. On hash mismatch, it raises `ResumeIncompatible` unless `--allow-mixed-resume`.
- **Lifecycle handshake on resume** — `Lifecycle.acquire_or_resume()` (the async CM) internally dispatches to `acquire()` (fresh) or `acquire_for_resume()` (resume) per M4 Task 4.3. Mixing these breaks the `loaded_by_us` invariant.
- **Lifecycle MUST be released on any exception.** The `async with Lifecycle.acquire_or_resume(...)` context manager guarantees release. Tested in `test_auditor_lifecycle.py`: inject exception in any phase, assert `Lifecycle.release` was called exactly once.
- **`PreflightFailure` exit codes are part of the public contract.** Spec §8.1 documents which check produces which exit code; `tests/unit/test_exit_codes.py` locks these.
- **Each phase emits its own `*_Start` / `*_Complete` events.** The auditor only emits `RunStart`/`RunComplete`. Don't double-emit.
- **`run_audit` is not the CLI.** It's a coroutine that takes a `repo` path and pre-built `config`/`lens`/`bus`/`command_bus`. M10 builds those from CLI args.
- **Renderer crash is NOT run-killing (§ARCH-13).** Per-file render failure writes `<file>.ERROR.md` with traceback and continues. Only `RenderFatal` (raised by renderer for disk-full / audit_dir gone) propagates.
- **No `tui/*` imports.** Enforced by `test_import_boundaries.py`. If you find yourself wanting a TUI symbol in the auditor, you're modeling the seam wrong — emit an event instead.

## Patterns to follow

- **§5.5.2.1 lifecycle phase block:** the auditor wraps the phase loop in `async with Lifecycle.acquire_or_resume(...) as model_info:` so model load/unload bracket the entire run, not individual phases.
- **End-to-end testing with recorded LMS:** tests in `tests/recorded/test_run_audit_e2e.py` use the `recorded_lms` fixture (M3 Task 3.9) to run `await run_audit(...)` against `tests/fixtures/repos/tiny_python/` offline.
- **Phase TDD:** for each of the 5 phases, write a failing test that asserts the phase's outputs (file artifacts + emitted events) before implementing.
- **One small function per spec row:** every §8.1 row gets its own pure function so it can be tested in isolation. `PreflightPhase.do_work` is just a list-runner.

## Tasks

### Task 8.1: Phase protocol + base + named exceptions

**Files:**
- Create: `senex/phases/__init__.py`
- Create: `senex/phases/base.py`

- [ ] **Step 8.1.1: Failing test** in `tests/unit/test_phase_protocol.py` — `assert isinstance(stub_phase, Phase)` using `runtime_checkable`. Exists test for each named exception class (importable, has documented attributes).

- [ ] **Step 8.1.2: Implement `Phase` protocol** with `@runtime_checkable` decorator. Implement named exceptions: `PreflightFailure(exit_code, message, check_name)`, `PhaseAborted`, `ResumeIncompatible`, `RenderFatal`, `AggregateFailed`. Re-export from `senex/phases/__init__.py`.

- [ ] **Step 8.1.3: Verify import boundary** — add an explicit assertion in `tests/unit/test_import_boundaries.py` that `senex.phases.*` and `senex.auditor` modules' `__file__` ASTs contain no `from senex.tui` or `import senex.tui`. (M1 has the helper; just add the new module names.)

- [ ] **Step 8.1.4: Commit** `feat(M8): Phase protocol + named exceptions`.

### Task 8.2: PreflightPhase + 14 individual check functions

**Files:**
- Create: `senex/phases/preflight.py`
- Create: `tests/unit/test_preflight_checks.py`

**Each spec §8.1 row gets ITS OWN small function. Signatures:**

```python
from enum import Enum
from dataclasses import dataclass

class CheckStatus(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    message: str = ""
    exit_code: int | None = None  # required when status == FAIL

# Synchronous checks (no I/O over network)
def check_config_parses(path: Path) -> CheckResult: ...                 # exit_code=10
def check_repo_path(path: Path) -> CheckResult: ...                     # exit_code=11
def check_output_dir_writable(path: Path, min_bytes: int) -> CheckResult: ...  # exit_code=12
def check_gitnexus_index(repo: Path) -> CheckResult: ...                # WARN only, no exit
def check_addendum_safety(addendum_path: Path, repo_root: Path) -> CheckResult: ...  # exit_code=13 — SEC-1: resolve(), is_relative_to(repo_root), refuse if path.is_symlink()
def check_language_anchors(lens: Lens, anchors_dir: Path) -> CheckResult: ...  # exit_code=14
def check_sampling_ranges(config: SenexConfig) -> CheckResult: ...      # exit_code=15
def check_lifecycle_backend(config: SenexConfig) -> CheckResult: ...    # exit_code=16
def check_runlock_dir_writable(config: SenexConfig) -> CheckResult: ... # exit_code=17

# Async checks (network I/O to LMS)
async def check_lms_reachable(client: LMStudioClient) -> CheckResult: ...               # exit_code=20
async def check_model_loaded_or_loadable(client: LMStudioClient, lifecycle: Lifecycle, config: SenexConfig) -> CheckResult: ...  # exit_code=21
async def check_schema_with_thinking(client: LMStudioClient, config: SenexConfig) -> CheckResult: ...  # exit_code=22
async def check_streaming(client: LMStudioClient, config: SenexConfig) -> CheckResult: ...             # exit_code=23
```

(Exit codes above are illustrative — final values come from spec §8.1; tests lock whatever the spec says.)

**`PreflightPhase` orchestrates:**

```python
class PreflightPhase:
    name = "preflight"

    def __init__(self, client: LMStudioClient, lifecycle: Lifecycle) -> None:
        self._client = client
        self._lifecycle = lifecycle

    async def read_state(self, audit_dir: Path) -> None:
        return None

    async def do_work(self, state, lens, config, bus, command_bus):
        sync_checks = [
            ("config_parses",         lambda: check_config_parses(config.config_path)),
            ("repo_path",             lambda: check_repo_path(config.repo_path)),
            ("output_dir_writable",   lambda: check_output_dir_writable(config.output_dir, config.min_disk_bytes)),
            ("gitnexus_index",        lambda: check_gitnexus_index(config.repo_path)),
            ("addendum_safety",       lambda: check_addendum_safety(config.addendum_path, config.repo_path)),
            ("language_anchors",      lambda: check_language_anchors(lens, config.anchors_dir)),
            ("sampling_ranges",       lambda: check_sampling_ranges(config)),
            ("lifecycle_backend",     lambda: check_lifecycle_backend(config)),
            ("runlock_dir_writable",  lambda: check_runlock_dir_writable(config)),
        ]
        async_checks = [
            ("lms_reachable",         check_lms_reachable(self._client)),
            ("model_loaded",          check_model_loaded_or_loadable(self._client, self._lifecycle, config)),
            ("schema_with_thinking",  check_schema_with_thinking(self._client, config)),
            ("streaming",             check_streaming(self._client, config)),
        ]
        warnings: list[str] = []
        for name, fn in sync_checks:
            result = fn()
            if result.status is CheckStatus.FAIL:
                raise PreflightFailure(result.exit_code, result.message, name)
            if result.status is CheckStatus.WARN:
                warnings.append(f"{name}: {result.message}")
                await bus.publish(PreflightWarning(check=name, message=result.message))
        for name, coro in async_checks:
            result = await coro
            if result.status is CheckStatus.FAIL:
                raise PreflightFailure(result.exit_code, result.message, name)
            if result.status is CheckStatus.WARN:
                warnings.append(f"{name}: {result.message}")
                await bus.publish(PreflightWarning(check=name, message=result.message))
        return {"warnings": warnings}

    async def write_state(self, audit_dir: Path, state) -> None:
        # Persist warnings to audit_dir/preflight.json for doctor reuse.
        ...
```

- [ ] **Step 8.2.1: Failing tests for each check function (14 total)** — each test mocks the dependency (filesystem, `LMStudioClient`, `Lifecycle`) and asserts: PASS path returns `CheckStatus.PASS`; FAIL path returns the **exact documented exit code** from spec §8.1. `check_addendum_safety` MUST have explicit tests for: (a) symlink rejection, (b) path traversal `../../etc/passwd` rejection via `is_relative_to`, (c) absolute path outside repo rejection.

- [ ] **Step 8.2.2: Implement each `check_*` function in isolation.** No cross-talk between checks. Pure functions where possible (sync checks); async only for network I/O.

- [ ] **Step 8.2.3: Implement `PreflightPhase`** orchestrating the 14 checks per the snippet above. Test that first FAIL short-circuits (subsequent checks not run); WARN results are all collected and emitted as `PreflightWarning` events.

- [ ] **Step 8.2.4: Test `PreflightFailure` exit code locking** — `tests/unit/test_exit_codes.py` parameterizes over each check name → expected exit code per spec §8.1. This is a regression guard.

- [ ] **Step 8.2.5: Commit** `feat(M8): preflight phase with all spec §8.1 checks`.

### Task 8.3: DiscoveryPhase

**Files:**
- Create: `senex/phases/discovery.py`
- Create: `tests/unit/test_phases_discovery.py`

- [ ] **Step 8.3.1: Failing test** — given fixture repo `tests/fixtures/repos/tiny_python/`, `DiscoveryPhase.do_work` returns deterministic ordered file list, emits exactly one `DiscoveryStart` and one `DiscoveryComplete`, and returns the same files across two runs (determinism check).

- [ ] **Step 8.3.2: Implement `DiscoveryPhase`** — wraps `Walker.discover()`. State out: `{"files": list[Path], "skipped": list[tuple[Path, str]]}`. `write_state` persists the file list to `audit_dir/discovery.json` for resume.

- [ ] **Step 8.3.3: Commit** `feat(M8): discovery phase`.

### Task 8.4: FileAuditPhase (the big one)

**Files:**
- Create: `senex/phases/file_audit.py`
- Create: `tests/unit/test_phases_file_audit.py`

**Per-file iteration body — full pseudocode (this is the contract; the implementation is a near-1:1 translation):**

```python
async def do_work(self, state, lens, config, bus, command_bus):
    files: list[Path] = state["files"]
    audit_dir: Path = self._audit_dir
    checkpoint = self._checkpoint
    graph_provider = self._graph_provider
    client = self._client
    tool_loop = self._tool_loop
    renderer = self._renderer
    partial_writer = self._partial_writer

    for idx, file in enumerate(files):
        # ---- Resume: skip already-completed files ----
        if str(file) in checkpoint.completed:
            continue

        # ---- Command bus poll point #1 (CANONICAL: per-file boundary) ----
        command = command_bus.poll_nowait()
        if command is not None:
            if command.type == "Quit":
                raise KeyboardInterrupt
            if command.type == "Pause":
                await pause_until_resume(command_bus, bus)
            if command.type == "Skip" and command.target == str(file):
                await write_skipped_artifact(audit_dir, file, reason="user_skip")
                await checkpoint.mark_done(file)
                continue
            # Rerun is handled higher up (M10 CLI); ignored here.

        await bus.publish(FileStart(path=str(file), idx=idx, total=len(files)))

        # ---- Read source ----
        try:
            source = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError) as exc:
            await bus.publish(FileError(path=str(file), phase="read",
                                        error_kind="read_error", error_message=str(exc)))
            await write_skipped_artifact(audit_dir, file, reason=f"read_error: {exc}")
            await checkpoint.mark_done(file)
            continue

        # ---- Graph awareness (graceful fallback inside) ----
        awareness = await graph_provider.fetch(file)
        await bus.publish(FileContextBuilt(path=str(file), awareness_summary=awareness.summary()))

        # ---- Build messages ----
        messages = build_user_prompt(file, source, awareness, lens, config)

        # ---- Token budget gate ----
        if client.count_tokens(messages) > 0.9 * client.context_window:
            await write_skipped_artifact(audit_dir, file, reason="token_budget_exceeded")
            await bus.publish(FileError(path=str(file), phase="prepare",
                                        error_kind="token_budget", error_message="..."))
            await checkpoint.mark_done(file)
            continue

        # ---- LMS call with full §8.2 recovery matrix ----
        try:
            response = await tool_loop.run(messages, schema=config.schema, tools=lens.tools)
        except CompactionLoopExceeded as exc:
            await write_error_artifact(audit_dir, file, kind="compaction_loop",
                                       traceback=traceback.format_exc())
            await bus.publish(FileError(path=str(file), phase="llm",
                                        error_kind="compaction_loop", error_message=str(exc)))
            await checkpoint.mark_done(file)
            continue
        except (LMSResponseSchemaInvalid, LMSResponseInvalidJSON) as exc:
            # Retry ONCE with stricter prompt
            messages_strict = inject_strict_addendum(messages)
            try:
                response = await tool_loop.run(messages_strict, schema=config.schema, tools=lens.tools)
            except (LMSResponseSchemaInvalid, LMSResponseInvalidJSON) as exc2:
                await write_raw_response(audit_dir, file, raw_json=str(getattr(exc2, "last_response", "")))
                await write_error_artifact(audit_dir, file, kind="schema_mismatch",
                                           traceback=traceback.format_exc())
                await bus.publish(FileError(path=str(file), phase="llm",
                                            error_kind="schema_mismatch", error_message=str(exc2)))
                await checkpoint.mark_done(file)
                continue
        except LMSConnectionLost:
            raise  # Propagate to run-level — run aborts
        except FingerprintChanged:
            raise  # §8.3 — propagate; run aborts (model swapped under us)
        except (LMSError, ToolDispatchFailed) as exc:
            await write_error_artifact(audit_dir, file, kind="lms_error",
                                       traceback=traceback.format_exc())
            await bus.publish(FileError(path=str(file), phase="llm",
                                        error_kind="lms_error", error_message=str(exc)))
            await checkpoint.mark_done(file)
            continue

        # ---- Render + persist (ARCH-13: render crash is per-file, NOT run-killing) ----
        try:
            metadata = build_file_metadata(file, response, lens, config)
            markdown = renderer.render_file(response, metadata)
            renderer.write_file_atomic(audit_dir, file, markdown)
            await partial_writer.append(make_finding_records(response, file, metadata))
        except RenderFatal:
            raise  # Disk full / audit_dir gone — kill the run
        except Exception as exc:
            await write_error_artifact(audit_dir, file, kind="render_error",
                                       traceback=traceback.format_exc())
            await bus.publish(FileError(path=str(file), phase="render",
                                        error_kind="render_error", error_message=str(exc)))
            await checkpoint.mark_done(file)
            continue

        # ---- Optional thinking trace ----
        if config.thinking.save_traces:
            write_thinking_trace(audit_dir, file, response.reasoning_content)

        # ---- Mark complete + emit ----
        await checkpoint.mark_done(file)
        await bus.publish(FileComplete(
            path=str(file),
            finding_counts={severity: len(getattr(response.findings, severity, []))
                            for severity in ("critical", "high", "medium", "low", "info")},
        ))

    return {"completed": list(checkpoint.completed)}
```

**Command bus poll points (canonical list):**
- **Per-file boundary in `FileAuditPhase`** — line marked "Command bus poll point #1" above, immediately after the resume-skip check and before `FileStart` emission. This is the canonical interrupt point.
- **Between phases in `run_audit`** — see Task 8.7. Acceptable but lower-resolution.

- [ ] **Step 8.4.1: Failing test** end-to-end happy path: given `tests/fixtures/repos/tiny_python/` (5 files) + recorded LMS responses, `FileAuditPhase` produces 5 reports, 5 partial finding entries, 5 checkpoint updates, and emits exactly 5 `FileStart` + 5 `FileComplete` events.

- [ ] **Step 8.4.2: Implement** the per-file loop per the pseudocode above.

- [ ] **Step 8.4.3: Test EVERY recovery branch** (one test per branch — these are §8.2 contract tests):
  - `read_error` (file with bad UTF-8 → `<file>.SKIPPED.md` with reason, run continues)
  - `token_budget_exceeded` (artificially low ctx_window → `<file>.SKIPPED.md` with reason="too_large")
  - `CompactionLoopExceeded` (recorded fixture → `<file>.ERROR.md`, run continues)
  - `LMSResponseSchemaInvalid` first try, success second try (retry with stricter prompt works)
  - `LMSResponseSchemaInvalid` both tries (raw response saved + `<file>.ERROR.md` kind="schema_mismatch", run continues)
  - `LMSError` / `ToolDispatchFailed` (`<file>.ERROR.md` kind="lms_error", run continues)
  - `LMSConnectionLost` → propagates out of `do_work` (run aborts)
  - `FingerprintChanged` → propagates (run aborts per §8.3)
  - Renderer crash for one file (`<file>.ERROR.md` kind="render_error", run continues — §ARCH-13)
  - `RenderFatal` (disk full simulated) → propagates

- [ ] **Step 8.4.4: Test command bus integration** — post `Pause` between two files; assert auditor blocks on `pause_until_resume`. Post `Resume`; assert next file processes. Post `Skip` targeting the next file; assert that file is skipped (SKIPPED.md written) and the file after that is processed normally. Post `Quit`; assert `KeyboardInterrupt` propagates.

- [ ] **Step 8.4.5: Test thinking trace** (`config.thinking.save_traces=True` → `<file>.thinking.md` written; `False` → not written).

- [ ] **Step 8.4.6: Commit** `feat(M8): per-file audit phase with full §8.2 recovery matrix`.

### Task 8.5: CrosscutPhase

**Files:**
- Create: `senex/phases/crosscut.py`
- Create: `tests/unit/test_phases_crosscut.py`

- [ ] **Step 8.5.1: Failing test** — given `findings.partial.jsonl` fixture, `CrosscutPhase` writes `themes.json`, emits `CrosscutStart`/`CrosscutComplete`. Failure path: `CrossCutter.run` raises → phase returns `None`, emits `CrosscutComplete(success=False, gap_reason=...)`, does NOT raise.

- [ ] **Step 8.5.2: Implement `CrosscutPhase`** — loads partial NDJSON via `PartialReader`, calls `CrossCutter.run()`, writes themes JSON to `audit_dir/themes.json`, emits events. Failure-tolerant per §8.2.

- [ ] **Step 8.5.3: Commit** `feat(M8): crosscut phase`.

### Task 8.6: AggregatePhase

**Files:**
- Create: `senex/phases/aggregate.py`
- Create: `tests/unit/test_phases_aggregate.py`

- [ ] **Step 8.6.1: Failing test** — given partial findings + themes fixtures, `AggregatePhase` writes `combined.md`, `findings.json`, `claude-handoff.md` atomically. Failure path: any sub-step raises → wrapped in `AggregateFailed` (with `exit_code=5`).

- [ ] **Step 8.6.2: Implement `AggregatePhase`** — `Aggregator.run()` → `HandoffWriter.write()` → `Renderer.render_combined()` → all writes via `write_atomic` (tmp + rename). On any failure, raise `AggregateFailed(exit_code=5, message=...)`.

- [ ] **Step 8.6.3: Test atomic-write semantics** — kill the process mid-aggregate; verify no partial `combined.md` exists (only `.tmp` files).

- [ ] **Step 8.6.4: Commit** `feat(M8): aggregate phase + atomic finalization`.

### Task 8.7: Auditor coroutine (run_audit)

**Files:**
- Create: `senex/auditor.py`
- Create: `tests/unit/test_auditor_resume.py`
- Create: `tests/unit/test_auditor_lifecycle.py`
- Create: `tests/recorded/test_run_audit_e2e.py`

**Signature and skeleton:**

```python
async def run_audit(
    repo: Path,
    config: SenexConfig,
    lens: Lens,
    bus: EventBus,
    command_bus: CommandBus,
    resume: bool = False,
) -> int:
    """
    Returns:
        0 — full success
        1 — partial success (run completed but some files skipped/errored)
        ResumeIncompatible.exit_code (4) — hash mismatch on resume
        AggregateFailed.exit_code (5) — aggregate phase failed (resumable via `senex aggregate`)
        PreflightFailure.exit_code — per spec §8.1 (varies by check)
    """
    audit_dir = compute_audit_dir(repo, config)  # includes run_id_short suffix
    audit_dir.mkdir(parents=True, exist_ok=True)

    # ---- Checkpoint: load or create, validate hashes on resume ----
    checkpoint = await Checkpoint.create_or_resume(
        audit_dir,
        config_hash=config.hash(),
        prompt_hash=lens.prompt_hash(),
        model_fingerprint=config.model.fingerprint,
        tool_pack_hash=lens.tool_pack_hash(),
        lens_version=lens.version,
        compaction_prompt_hash=config.compaction.prompt_hash,
    )
    if resume and checkpoint.is_incompatible() and not config.allow_mixed_resume:
        raise ResumeIncompatible(
            f"Checkpoint hash mismatch: {checkpoint.mismatch_reason()}. "
            "Pass --allow-mixed-resume to override."
        )

    # ---- Lifecycle bracket: load model on entry, release on ANY exit (incl. exception) ----
    async with Lifecycle.acquire_or_resume(config, resume=resume) as model_info:
        await bus.publish(RunStart(
            run_id=checkpoint.run_id,
            audit_dir=str(audit_dir),
            resume=resume,
            model_fingerprint=model_info.fingerprint,
        ))

        client = LMStudioClient(config, model_info)
        phases: list[Phase] = [
            PreflightPhase(client, model_info.lifecycle),
            DiscoveryPhase(repo=repo),
            FileAuditPhase(audit_dir=audit_dir, checkpoint=checkpoint, client=client, ...),
            CrosscutPhase(audit_dir=audit_dir),
            AggregatePhase(audit_dir=audit_dir),
        ]

        partial_success = False
        try:
            for phase in phases:
                # ---- Command bus poll point #2: between phases ----
                command = command_bus.poll_nowait()
                if command is not None and command.type == "Quit":
                    raise KeyboardInterrupt

                # ---- Checkpoint state machine: skip completed phases (§ARCH-15) ----
                if checkpoint.phase_status.get(phase.name) == "complete":
                    continue

                await checkpoint.set_phase(phase.name, "in_progress")
                state = await phase.read_state(audit_dir)
                new_state = await phase.do_work(state, lens, config, bus, command_bus)
                await phase.write_state(audit_dir, new_state)
                await checkpoint.set_phase(phase.name, "complete")

                # Detect partial success during FileAuditPhase
                if phase.name == "file_audit" and new_state and any_files_errored(new_state):
                    partial_success = True

            await bus.publish(RunComplete(
                run_id=checkpoint.run_id,
                exit_code=1 if partial_success else 0,
            ))
            return 1 if partial_success else 0

        except (PreflightFailure, ResumeIncompatible, AggregateFailed) as exc:
            await bus.publish(RunComplete(run_id=checkpoint.run_id, exit_code=exc.exit_code, error=str(exc)))
            return exc.exit_code
        # Lifecycle.release happens automatically via __aexit__ even on exception.
```

- [ ] **Step 8.7.1: Failing test** end-to-end (`tests/recorded/test_run_audit_e2e.py`): `await run_audit(tiny_python, config, lens, bus, command_bus)` against recorded LMS produces complete audit dir (`combined.md`, `findings.json`, `claude-handoff.md`, per-file `<file>.md`, `events.jsonl` ending with `RunComplete`).

- [ ] **Step 8.7.2: Implement `run_audit`** per the skeleton above. The `async with Lifecycle.acquire_or_resume(...)` is non-negotiable — any other pattern leaks the runlock on exception.

- [ ] **Step 8.7.3: Test resume hash validation** — `tests/unit/test_auditor_resume.py`:
  - Compatible resume (all hashes match) → checkpoint loaded, completed phases skipped.
  - `config_hash` mismatch → `ResumeIncompatible` raised unless `allow_mixed_resume=True`.
  - `model_fingerprint` mismatch → `ResumeIncompatible` raised; with `allow_mixed_resume=True`, run proceeds.
  - Each of the 6 hashes in §8.5 has a dedicated mismatch test.

- [ ] **Step 8.7.4: Test lifecycle release on exception** — `tests/unit/test_auditor_lifecycle.py`:
  - Inject exception in `PreflightPhase.do_work` → assert `Lifecycle.release` called exactly once.
  - Inject exception in `FileAuditPhase.do_work` → assert release called.
  - Inject exception in `AggregatePhase.do_work` (`AggregateFailed`) → assert release called, return code is 5.
  - `KeyboardInterrupt` from Quit command → assert release called, exception propagates.

- [ ] **Step 8.7.5: Test checkpoint state machine** — start a run, kill mid-FileAudit (raise after 1 file). Restart with `resume=True`. Assert: preflight + discovery skipped (status="complete"), file_audit resumes (1 file in `completed`), 4 remaining files processed, crosscut + aggregate run normally.

- [ ] **Step 8.7.6: Test command bus between phases** — post `Pause` between phases; assert auditor pauses. Post `Quit` between phases; assert `KeyboardInterrupt` propagates with lifecycle released.

- [ ] **Step 8.7.7: Commit** `feat(M8): run_audit coroutine — phases + checkpoint + command bus + lifecycle bracket`.

## Acceptance criteria

- `pytest tests/unit/test_phase_protocol.py tests/unit/test_preflight_checks.py tests/unit/test_phases_*.py tests/unit/test_auditor_*.py tests/recorded/test_run_audit_e2e.py -v` is 100% green.
- `pytest tests/unit/test_import_boundaries.py -v` confirms no `senex/phases/*` or `senex/auditor.py` module imports `senex.tui` (AST-checked).
- `isinstance(phase, Phase)` returns `True` for all 5 concrete phase classes (verifies `runtime_checkable` protocol compliance).
- End-to-end test: `await run_audit(tiny_python, config, lens, bus, command_bus)` against recorded LMS produces `combined.md`, `findings.json`, `claude-handoff.md`, per-file `<file>.md`, `events.jsonl` with monotonically increasing `seq`, ending with `RunComplete`.
- Resume test: kill mid-FileAudit (after 1 file) → restart with `resume=True` → second run's events.jsonl starts where first left off; final state has all files processed; checkpoint hash validation passes.
- Per-file recovery tests (one per branch): `read_error`, `token_budget_exceeded`, `CompactionLoopExceeded`, `schema_mismatch_after_retry`, `lms_error`, `render_error` each produce the right artifact (`<file>.SKIPPED.md` or `<file>.ERROR.md`) AND the run continues to the next file.
- Run-aborting failures propagate cleanly: `LMSConnectionLost` and `FingerprintChanged` raise out of `FileAuditPhase` and `Lifecycle.release` is still called.
- All 14 spec §8.1 preflight checks have unit tests asserting documented exit codes; `check_addendum_safety` has explicit symlink + path-traversal rejection tests (SEC-1).
- Resume hash mismatch test: each of the 6 hashes in §8.5 has a dedicated `ResumeIncompatible` test; `--allow-mixed-resume` overrides each.
- Command bus tests: `Pause` between files suspends; `Resume` continues; `Skip` writes SKIPPED.md and proceeds; `Quit` raises `KeyboardInterrupt` with lifecycle released; commands posted between phases also honored.
- `tests/recorded/test_run_audit_e2e.py` runs offline (no live LMS) using the `recorded_lms` fixture from M3.
- Aggregate atomicity: process killed mid-aggregate leaves no partial `combined.md` (only `.tmp` files); next run with `resume=True` recovers cleanly via re-running `AggregatePhase`.
