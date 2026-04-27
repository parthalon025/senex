# Milestone 4: Lifecycle + Runlock Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M4 delivers model lifecycle management: deciding whether to load a model, attaching to an already-loaded one, holding a runlock so a second senex run won't unload it, and unloading only when *we* loaded it AND no one else holds the lock. It also delivers the `senex lifecycle` CLI subcommand for operator visibility.

**Architectural intent:** The runlock from M1 is the *mechanism*; the Lifecycle wrapper is the *policy*. The policy must work correctly when (a) we are the only senex run, (b) two runs share a model, (c) a run resumes from checkpoint and inherits a model it didn't load, (d) the model fingerprint changes mid-run. Each scenario has a unique combination of `loaded_by_us` provenance + runlock holder count + auto_unload setting; getting any one wrong corrupts the lock file or leaves models pinned in VRAM.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration
- **Required modules from prior work:**
  - `senex/runlock.py` — `RunLock.acquire()` / `release()` (M1)
  - `senex/events.py` — all 11 lifecycle event types: `ModelLoadRequested/Started/Complete/Failed`, `ModelUnloadStarted/Complete/Skipped/Failed`, `ModelFingerprintChanged`, `RunLockAcquired/Released`
  - `senex/lmstudio_client.py` — `list_loaded_models()` for backend probing (M3)
  - `senex/config.py` — `LifecycleCfg` (auto_load, auto_unload, allow_mixed)
- **Required tools/state:**
  - LM Studio running locally for `senex lifecycle status` smoke tests
  - Either `lmstudio` Python SDK installed OR `lms` CLI on PATH (backend selection auto-detects)

## Deliverable

This milestone creates the following files:

- `senex/lmstudio_lifecycle.py` — `LifecycleBackend` protocol, `LMStudioSDKBackend`, `LMSCLIBackend`, `Lifecycle` high-level API
- `tests/unit/test_lifecycle.py` — backend selection + acquire/release scenarios + resume + threat-surface validation
- Wired-in CLI subcommands: `senex lifecycle status [--json]`, `senex lifecycle clear-locks [--force]` (the CLI shell lives in M10; here we add the handler functions)
- Wired-in `senex doctor` lifecycle availability check

## Downstream consumers

- **M8** Auditor calls `lifecycle.acquire(model_id, run_id, runlock)` at run start and `lifecycle.release(...)` at run end. The `loaded_by_us` boolean is persisted on the checkpoint for resume.
- **M8** Resume path calls `lifecycle.acquire_for_resume(...)` instead of regular `acquire()`.
- **M10** CLI subcommands `senex lifecycle status` and `senex lifecycle clear-locks` are exposed in the argparse layer.
- **M10** Doctor incorporates the lifecycle backend availability check.

## Spec sections referenced

- §5.5.2 Model Lifecycle Management — overall lifecycle behavior
- §5.5.2.1 Acquire/Release semantics — the canonical algorithm (referenced by Task 4.2)
- §5.5.2.2 Runlock format — already implemented in M1; M4 consumes it
- §5.5.2.3 Fingerprint computation — already implemented in M3; M4 stores in checkpoint
- §5.5.2.7 Resume scenarios — 4-row table (loaded_by_us / current state / fingerprint match / outcome)
- §LIFE-tests — every acquire/release scenario must have a unit test
- §SEC-4 — model_id regex validation pre-subprocess

## Key contracts

- **`LifecycleBackend`** protocol — `is_loaded(model_id)`, `load(model_id, timeout)`, `unload(model_id)`, `list_loaded()`.
- **`LMStudioSDKBackend`** — `import lmstudio` → `lmstudio.list_loaded_models()`, `lmstudio.llm(model_id)`, `model.unload()`.
- **`LMSCLIBackend`** — `subprocess.run(["lms", "ps", "--json"])`, `["lms", "load", model_id]`, `["lms", "unload", model_id]`.
- **`LifecycleBackend.select() -> LifecycleBackend`** — prefers SDK, falls back to CLI, raises if neither.
- **`Lifecycle.acquire(model_id, run_id, runlock) -> tuple[ModelInfo, bool]`** — returns (info, loaded_by_us).
- **`Lifecycle.release(model_id, run_id, runlock, auto_unload, loaded_by_us)`** — refcount-aware unload.
- **`Lifecycle.acquire_for_resume(model_id, run_id, runlock, checkpoint_fingerprint, allow_mixed) -> ModelInfo`** — resume-specific path that always sets `loaded_by_us=False`.

## Watch-outs

- **Resume must NEVER set `loaded_by_us=True`.** A resumed run did not perform the load; even if the fingerprint matches, the model belongs to whoever loaded it originally (which may be the user via LM Studio UI). `loaded_by_us=False` enforced unconditionally on the resume path.
- **Refcount semantics:** `release()` returns the count *after* this run's holder is removed. Unload only when `count == 0 AND loaded_by_us AND auto_unload`. Any other combination → emit `ModelUnloadSkipped(reason=...)`.
- **Fingerprint mismatch on resume** is a hard error unless `allow_mixed=true`. Don't silently continue with a different model; the run's findings would be inconsistent.
- **`model_id` is subprocess input.** Validate `^[A-Za-z0-9_./-]+$` pre-subprocess in *both* SDK and CLI backends. SDK paths can also be exploited via the `subprocess` underneath the SDK in some adapter implementations — defense in depth.
- **CLI backend uses list-form args.** Never `f"lms load {model_id}"`. Always `["lms", "load", model_id]`.
- **`is_loaded()` is racy.** Two senex runs can both observe "not loaded" and both call `load()`. The runlock protects the unload side; the load side relies on LM Studio's idempotent load (subsequent loads return immediately if already loaded). Both runs end up holding the lock; only the second's `loaded_by_us` is logically False *unless* we cannot tell, in which case treat both as `loaded_by_us=True` and rely on refcount to gate unload.

## Patterns to follow

- §5.5.2.1 acquire/release pseudocode is the canonical algorithm. Implement it exactly:
  ```
  if not is_loaded(model_id):
      if auto_load: load → runlock.acquire(loaded_by_us=True) → return (info, True)
      else: raise ModelNotLoaded
  else:
      info = lookup → runlock.acquire(loaded_by_us=False) → return (info, False)
  ```
  ```
  count = runlock.release(fp, run_id)
  if count == 0 and we_loaded and auto_unload:
      unload → emit ModelUnloadComplete
  else:
      emit ModelUnloadSkipped(reason=...)
  ```
- **Test the §5.5.2.7 resume table row-by-row** — 4 scenarios, 4 tests.
- **All 11 lifecycle events emitted from `Lifecycle`**, never from the backends. The backends are mechanism-only.

## Tasks

### Task 4.1: Lifecycle backend selection

**Files:**
- Create: `senex/lmstudio_lifecycle.py`
- Create: `tests/unit/test_lifecycle.py`

- [ ] **Step 4.1.1: Failing test** — `LifecycleBackend.select()` prefers Python SDK if `import lmstudio` succeeds, else falls back to `lms` CLI, else raises if neither.

- [ ] **Step 4.1.2: Implement `LifecycleBackend` protocol** + `LMStudioSDKBackend` and `LMSCLIBackend` impls:
  - `is_loaded(model_id) -> bool`
  - `load(model_id, timeout: int) -> ModelInfo` (returns fingerprint)
  - `unload(model_id) -> None`
  - `list_loaded() -> list[ModelInfo]`

- [ ] **Step 4.1.3: SDK backend** uses `lmstudio.list_loaded_models()`, `lmstudio.llm(model_id)`, `model.unload()`.

- [ ] **Step 4.1.4: CLI backend** uses `subprocess.run(["lms", "ps", "--json"], ...)`, `["lms", "load", model_id]`, `["lms", "unload", model_id]` — list form, validates model_id regex.

- [ ] **Step 4.1.5: Tests** for each backend (mock subprocess + SDK).

- [ ] **Step 4.1.6: Commit** `feat(M4): lifecycle backend selection (SDK + CLI)`.

### Task 4.2: Lifecycle high-level API

- [ ] **Step 4.2.1: Implement `Lifecycle`** wrapping a backend:
  - `acquire(model_id, run_id, runlock) -> tuple[ModelInfo, bool]` returns (info, loaded_by_us)
  - `release(model_id, run_id, runlock, auto_unload, loaded_by_us) -> None`
  - Emits all 11 lifecycle events

- [ ] **Step 4.2.2: Acquire logic** per spec §5.5.2.1:
  ```
  if not is_loaded(model_id):
      if auto_load:
          fp = load(...); runlock.acquire(fp, run_id, loaded_by_us=True); return (info, True)
      else:
          raise ModelNotLoaded
  else:
      info = lookup; runlock.acquire(info.fingerprint, run_id, loaded_by_us=False); return (info, False)
  ```

- [ ] **Step 4.2.3: Release logic** per spec §5.5.2.1:
  ```
  count = runlock.release(fp, run_id)
  if count == 0 and we_loaded and auto_unload:
      unload(model_id); emit ModelUnloadComplete
  else:
      emit ModelUnloadSkipped(reason=...)
  ```

- [ ] **Step 4.2.4: Tests** for each scenario: fresh load+unload, attached, concurrent holders, auto_unload=false, resume (`loaded_by_us=False` enforced).

- [ ] **Step 4.2.5: Commit** `feat(M4): Lifecycle API with acquire/release semantics`.

### Task 4.3: Resume integration (§5.5.2.7)

- [ ] **Step 4.3.1: Implement `acquire_for_resume(model_id, run_id, runlock, checkpoint_fingerprint, allow_mixed) -> ModelInfo`**:
  - Re-probe; recompute fingerprint
  - Compare against checkpoint; if mismatch and not allow_mixed → raise `FingerprintMismatch`
  - Always acquire with `loaded_by_us=False` (resume never owns unload)

- [ ] **Step 4.3.2: Tests** for the 4 scenarios in spec §5.5.2.7 table.

- [ ] **Step 4.3.3: Commit** `feat(M4): resume lifecycle handshake`.

### Task 4.4: Lifecycle CLI subcommand

- [ ] **Step 4.4.1: Implement `senex lifecycle status [--json]`** — list current loaded models + runlock holders + `loaded_by_us` provenance for each.

- [ ] **Step 4.4.2: Implement `senex lifecycle clear-locks [--force]`** — prune dead-PID entries; with `--force`, also remove live entries (warn).

- [ ] **Step 4.4.3: Tests** for both subcommands; smoke test against an actual lock file.

- [ ] **Step 4.4.4: Commit** `feat(M4): lifecycle CLI subcommands`.

### Task 4.5: Doctor lifecycle check integration

- [ ] **Step 4.5.1: Add to `senex doctor`**: probe lifecycle backend availability; warn if `auto_load=true` AND no backend available.

- [ ] **Step 4.5.2: Commit** `feat(M4): doctor lifecycle availability check`.

### Task 4.6: Threat-surface validation (regex on model_id)

- [ ] **Step 4.6.1: Failing test** — `_load_model("$(rm -rf /)")` raises `InvalidModelId` before any subprocess call.

- [ ] **Step 4.6.2: Add `^[A-Za-z0-9_./-]+$` validation** to all backends pre-subprocess.

- [ ] **Step 4.6.3: Commit** `feat(M4): model_id validation pre-subprocess`.

## Acceptance criteria

- `pytest tests/unit/test_lifecycle.py -v` is 100% green.
- All 4 §5.5.2.7 resume scenarios have passing tests with the documented outcomes.
- A test asserts `_load_model("$(rm -rf /)")` raises `InvalidModelId` *before* any subprocess invocation (mock + assert subprocess never called).
- `senex lifecycle status --json` against a running LM Studio with one loaded model returns valid JSON with `loaded_by_us` provenance.
- `senex lifecycle clear-locks` removes only stale-PID entries by default; `--force` also removes live entries with a warning.
- `senex doctor` warns when `auto_load=true` and no lifecycle backend is available.
- A test asserts that a `release()` with `count > 0` emits `ModelUnloadSkipped(reason="other_holders")` and does NOT call `backend.unload()`.
- A test asserts that `acquire_for_resume()` always sets `loaded_by_us=False` even when the fingerprint matches.
