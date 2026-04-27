# Milestone 8: Phases + Auditor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M8 wires every prior milestone together into the orchestrator. Five phases (Preflight, Discovery, FileAudit, Crosscut, Aggregate) each implement the `Phase` protocol; `run_audit()` is the async coroutine that walks them in order, respecting checkpoints and the command bus. After M8, you have a fully functional headless audit — no TUI, no CLI yet, but `await run_audit(...)` produces a complete audit directory.

**Architectural intent:** Phases are *resumable units of work*. Each has `read_state` / `do_work` / `write_state`, all of which are checkpoint-aware. The auditor is dumb on purpose: it iterates phases, checks the command bus between them, and trusts each phase to handle its own errors. FileAuditPhase is "the big one" because it owns the per-file recovery matrix from spec §8.2.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration, M4 Lifecycle, M5 Tool Framework, M6 Compaction, M7 Renderer + Aggregator
- **Required modules from prior work:**
  - `senex/checkpoint.py` — `Checkpoint.create_or_resume()`, `set_phase()`, `mark_done()` (M1)
  - `senex/walker.py` — `Walker.discover()` for DiscoveryPhase (M2)
  - `senex/graph_awareness.py` — `GraphContextProvider.fetch()` for FileAuditPhase (M2)
  - `senex/prompts/_anchor_loader.py` — `build_user_prompt()` for FileAuditPhase (M2)
  - `senex/lmstudio_client.py` — `client.chat()` for FileAuditPhase + CrosscutPhase (M3)
  - `senex/lmstudio_lifecycle.py` — `Lifecycle.acquire()` / `release()` / `acquire_for_resume()` (M4)
  - `senex/tools/registry.py` + `loop.py` — for FileAuditPhase (M5)
  - `senex/compaction.py` — `Compactor` for FileAuditPhase (M6)
  - `senex/renderer.py`, `findings_partial.py`, `findings_aggregator.py`, `cross_cutting.py`, `handoff.py` (M7)
  - `senex/events.py` + `EventBus` + `CommandBus` (M1)
- **Required tools/state:**
  - `tests/recorded/` LMS replay infrastructure from M3 — every M8 end-to-end test runs offline against recorded fixtures.
  - `tests/fixtures/repos/tiny_python/` from M2.

## Deliverable

This milestone creates the following files:

- `senex/phases/__init__.py`
- `senex/phases/base.py` — `Phase` protocol
- `senex/phases/preflight.py` — `PreflightPhase` with all spec §8.1 checks
- `senex/phases/discovery.py` — `DiscoveryPhase` (wraps Walker)
- `senex/phases/file_audit.py` — `FileAuditPhase` (the big one; per-file loop with full recovery matrix)
- `senex/phases/crosscut.py` — `CrosscutPhase` (wraps `CrossCutter`)
- `senex/phases/aggregate.py` — `AggregatePhase` (Aggregator + HandoffWriter + render_combined)
- `senex/auditor.py` — `async run_audit(repo, config, lens, bus, command_bus, resume=False)`
- `tests/unit/test_preflight.py`
- `tests/unit/test_phases_*.py` (per-phase tests)
- `tests/recorded/test_run_audit_e2e.py` — full end-to-end against recorded LMS

## Downstream consumers

- **M9** TUI's "Start audit" button launches `run_audit()` as a Textual worker task.
- **M10** CLI `senex audit` subcommand is a thin wrapper that constructs the bus + lifecycle + lens + config and calls `run_audit()`.
- **M10** CLI `senex doctor` reuses `PreflightPhase` checks.
- **M10** CLI `senex aggregate` re-runs `AggregatePhase` against an existing audit dir.
- **M10** Live gates 13a/13c exercise full `run_audit()` flow + resume.

## Spec sections referenced

- §4 Data flow — overall audit pipeline
- §5.5.2.7 Resume scenarios — used by FileAuditPhase + auditor's resume path
- §5.6.1 Bus semantics — event publishing rules
- §5.6.2 Command bus — Pause/Resume/Skip/Rerun/Quit handling between phases and per-file
- §8.1 Pre-flight — full list of checks (config parses, repo exists, output dir writable, LMS reachable, model loaded/loadable, schema+thinking probe, gitnexus availability, addendum safety, ranges valid, lifecycle backend, runlock_dir writable) with documented exit codes
- §8.2 Per-file Recovery — the full per-file error matrix (CompactionLoopExceeded, SchemaInvalid, LMSError, retry policy, ERROR.md / SKIPPED.md fallbacks)
- §8.3 Run-Level — run-level errors (preflight failure, lifecycle handshake fail)
- §8.4 Observability — events that must be emitted at phase boundaries
- §8.5 Reproducibility & Resume Compatibility — hash discipline (config_hash, prompt_hash, model_fingerprint, tool_pack_hash, lens_version, compaction_prompt_hash)

## Key contracts

- **`Phase` protocol**:
  ```python
  class Phase(Protocol):
      name: str
      def read_state(self, audit_dir: Path) -> Any: ...
      async def do_work(self, state, lens, config, bus, command_bus) -> Any: ...
      def write_state(self, audit_dir: Path, new_state: Any) -> None: ...
  ```
- **`PreflightPhase.do_work()`** — runs each spec §8.1 check; raises `PreflightFailure(exit_code, message)` on first hard fail; collects warnings.
- **`DiscoveryPhase.do_work()`** — calls `Walker.discover()`; emits `DiscoveryComplete`.
- **`FileAuditPhase.do_work()`** — the per-file loop with full §8.2 recovery (see Task 8.4.2 pseudocode).
- **`CrosscutPhase.do_work()`** — reads `findings.partial.jsonl`, calls `CrossCutter.run()`, emits `CrosscutStart`/`CrosscutComplete`.
- **`AggregatePhase.do_work()`** — calls `Aggregator.run()` + `HandoffWriter.write()` + `Renderer.render_combined()`.
- **`async run_audit(repo, config, lens, bus, command_bus, resume=False)`** — coroutine that iterates phases, checks command bus, manages checkpoint phase status, emits `RunStart`/`RunComplete`.

## Watch-outs

- **FileAuditPhase recovery matrix is non-trivial.** Spec §8.2 has multiple branches: `CompactionLoopExceeded` → ERROR.md and continue; `SchemaInvalid` or `LMSError` → retry once with stricter prompt, second failure → ERROR.md and continue; `TokenBudgetExceeded` → SKIPPED.md and continue. Each branch has its own event sequence. Test all of them.
- **Command bus is checked at iteration boundaries.** Per-file boundary (between files in FileAudit) and between phases. Mid-LLM-call interrupts are not safe; pause = "finish current file then suspend."
- **Resume logic is in the auditor, not in `Checkpoint`.** `Checkpoint.create_or_resume()` returns the existing checkpoint if compatible (hashes match) or a fresh one. The auditor uses `checkpoint.phase_status[phase.name] == "complete"` to skip already-completed phases.
- **Lifecycle handshake on resume** — the auditor's resume branch calls `lifecycle.acquire_for_resume()` (M4 Task 4.3), not `lifecycle.acquire()`. Mixing these breaks the `loaded_by_us` invariant.
- **`PreflightFailure` exit codes are part of the public contract.** Spec §8.1 documents which check produces which exit code; tests must lock these in.
- **Each phase emits its own `*_Started` / `*_Complete` events.** The auditor only emits `RunStart`/`RunComplete`. Don't double-emit.
- **`run_audit` is not the CLI.** It's a coroutine that takes a `repo_path` and pre-built `config`/`lens`/`bus`/`command_bus`. M10 builds those from CLI args; M8 just orchestrates.

## Patterns to follow

- **§5.5.2.1 lifecycle phase block:** the auditor wraps the phase loop in `lifecycle.acquire(...)` ... `lifecycle.release(...)` so model load/unload bracket the entire run, not individual phases.
- **End-to-end testing with recorded LMS:** tests in `tests/recorded/test_run_audit_e2e.py` use the `recorded_lms` fixture (M3 Task 3.9) to run `await run_audit(...)` against `tests/fixtures/repos/tiny_python/` offline.
- **Phase TDD:** for each of the 5 phases, write a failing test that asserts the phase's outputs (file artifacts + emitted events) before implementing.

## Tasks

### Task 8.1: Phase protocol + base

**Files:**
- Create: `senex/phases/__init__.py`
- Create: `senex/phases/base.py`

- [ ] **Step 8.1.1: Implement `Phase` protocol**:
  ```python
  class Phase(Protocol):
      name: str
      def read_state(self, audit_dir: Path) -> Any: ...
      async def do_work(self, state, lens, config, bus, command_bus) -> Any: ...
      def write_state(self, audit_dir: Path, new_state: Any) -> None: ...
  ```

- [ ] **Step 8.1.2: Commit** `feat(M8): Phase protocol`.

### Task 8.2-8.6: Five phase implementations

Each phase is a separate task with TDD. Pattern shown for one (preflight); rest follow:

#### Task 8.2: PreflightPhase

**Files:**
- Create: `senex/phases/preflight.py`
- Create: `tests/unit/test_preflight.py`

- [ ] **Step 8.2.1: Failing tests** for each preflight check (per spec §8.1 — config parses, repo exists, output dir writable, LMS reachable, model loaded/loadable, schema+thinking probe, gitnexus availability, addendum safety, ranges valid, lifecycle backend, runlock_dir writable). Each should produce the documented exit code.

- [ ] **Step 8.2.2: Implement `PreflightPhase.do_work()`** — runs each check; raises `PreflightFailure(exit_code, message)` on first hard fail; collects warnings.

- [ ] **Step 8.2.3: Each check is a small function**: `check_config(...)`, `check_lms(...)`, `check_addendum_safety(...)` — testable in isolation.

- [ ] **Step 8.2.4: Commit** `feat(M8): preflight phase with all spec §8.1 checks`.

#### Task 8.3: DiscoveryPhase

- [ ] Wraps the walker + emits `DiscoveryComplete`. Test + commit.

#### Task 8.4: FileAuditPhase (the big one)

- [ ] **Step 8.4.1: Failing test** end-to-end: given a tiny fixture repo + recorded LMS responses, FileAuditPhase produces N reports + N findings.partial entries + N checkpoint updates.

- [ ] **Step 8.4.2: Implement** the per-file loop:
  ```python
  for file in walker_output:
      if file in checkpoint.completed: continue
      emit FileStart
      awareness = graph_provider.fetch(file)
      emit FileContextBuilt
      messages = build_messages(file, awareness, source, ...)
      try:
          if count_tokens(messages) > 0.9 * ctx_window: skip; continue
          response = await client.chat(task='file_audit', messages, schema, tools=lens.tools)
      except CompactionLoopExceeded: write ERROR.md; continue
      except (SchemaInvalid, LMSError): retry once with stricter prompt; if fail again write ERROR.md; continue
      await renderer.render(response, file_metadata) → atomic write to <file>.md.tmp → rename
      await partial_writer.append(response, file_metadata)
      await checkpoint.mark_done(file)
      emit FileComplete
  ```

- [ ] **Step 8.4.3: Test** all recovery paths from spec §8.2.

- [ ] **Step 8.4.4: Commit** `feat(M8): per-file audit phase with full recovery`.

#### Task 8.5: CrosscutPhase
- [ ] Reads `findings.partial.jsonl`, calls cross_cutter, emits events. Test + commit.

#### Task 8.6: AggregatePhase
- [ ] Reads partial + crosscut output, runs aggregator + handoff writer + finalizes combined.md. Test + commit.

### Task 8.7: Auditor coroutine (run_audit)

**Files:**
- Create: `senex/auditor.py`

- [ ] **Step 8.7.1: Failing test** end-to-end (still using recorded LMS): `await run_audit(repo_path, config, lens, bus)` produces a complete audit dir with all artifacts + sets RunComplete event.

- [ ] **Step 8.7.2: Implement `run_audit()`**:
  ```python
  async def run_audit(repo, config, lens, bus, command_bus, resume=False):
      audit_dir = compute_audit_dir(repo, config)
      checkpoint = Checkpoint.create_or_resume(audit_dir, ...)
      phases = [PreflightPhase(), DiscoveryPhase(), FileAuditPhase(), CrosscutPhase(), AggregatePhase()]
      for phase in phases:
          if checkpoint.phase_status[phase.name] == "complete": continue
          checkpoint.set_phase(phase.name, "in_progress")
          state = phase.read_state(audit_dir)
          new_state = await phase.do_work(state, lens, config, bus, command_bus)
          phase.write_state(audit_dir, new_state)
          checkpoint.set_phase(phase.name, "complete")
      emit RunComplete
  ```

- [ ] **Step 8.7.3: Wire `command_bus`** — between phases (and between files in FileAudit), check for Pause/Quit/Skip commands.

- [ ] **Step 8.7.4: Tests + commit** `feat(M8): run_audit coroutine — phases + checkpoint + command bus`.

## Acceptance criteria

- `pytest tests/unit/test_preflight.py tests/unit/test_phases_*.py tests/recorded/test_run_audit_e2e.py -v` is 100% green.
- End-to-end test: `await run_audit(tests/fixtures/repos/tiny_python, config, lens, bus, command_bus)` against recorded LMS produces an audit dir containing `combined.md`, `findings.json`, `claude-handoff.md`, per-file `<file>.md` for each fixture file, and an `events.jsonl` ending with `RunComplete`.
- Resume test: kill the run mid-FileAudit (raise in `do_work` after 1 file completes); restart with `resume=True`; verify the second run skips the completed file and processes the remaining ones.
- Per-file recovery test: a `CompactionLoopExceeded` for one file produces `<file>.ERROR.md` AND the run continues to the next file (no run abort).
- Per-file recovery test: `TokenBudgetExceeded` produces `<file>.SKIPPED.md` with reason="too_large".
- All 11 spec §8.1 preflight checks have unit tests asserting the documented exit code on failure.
- Command bus test: posting `Pause` between two files causes the auditor to suspend at the next iteration boundary; `Resume` continues; `Quit` shuts down cleanly.
- The `events.jsonl` from a successful run is monotonically `seq`-ordered and ends with `RunComplete`.
