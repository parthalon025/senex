# Senex Architecture

This document maps the core senex Python package: module connections, public
interfaces, and the layered dependency graph. It is the audit deliverable
referenced by the LMStudio→Ollama migration (`docs/MIGRATION_PLAN.md`).

## 1. Top-Level Shape

Senex is a CLI-driven, async-orchestrated code-audit pipeline. A single
`run_audit` coroutine drives five `Phase` objects; an event bus fans events
out to subscribers (disk, headless console, TUI, metrics) for parallel
consumption. The LLM backend is hidden behind the `LLMClient` Protocol.

```
              ┌──────────┐
              │   CLI    │  (cli.py + cmd_*.py)
              └────┬─────┘
                   │ composes
                   ▼
              ┌──────────┐         ┌─────────────┐
              │ auditor  │────────▶│  Lifecycle  │ (model load/unload + runlock)
              │ run_audit│         └─────────────┘
              └────┬─────┘
                   │ drives
                   ▼
   ┌──────────┬─────────┬──────────┬──────────┬────────────┐
   │preflight │discovery│file_audit│ crosscut │ aggregate  │  Phases
   └─────┬────┴────┬────┴─────┬────┴─────┬────┴──────┬─────┘
         │         │          │          │           │
         └────┬────┴──────────┴──────────┴───────────┘
              │ emits events to
              ▼
        ┌──────────┐
        │ EventBus │──▶ DiskWriter / TUI / Headless / Metrics subscribers
        └──────────┘
```

## 2. Dependency Layers (DAG, no cycles)

| Layer | Modules | Role |
|---|---|---|
| **0 Foundations** | `events.py`, `config.py`, `lmstudio_errors.py`, `atomic_io.py` | Pure data + IO primitives. No internal deps. |
| **1 Persistence + locking** | `checkpoint.py`, `runlock.py`, `secret_redactor.py`, `handoff.py` | Filesystem state, advisory locking, redaction. |
| **2 Protocols + registries** | `llm_client.py` (Protocol), `phases/base.py` (Phase Protocol), `subscribers/base.py` (Subscriber Protocol), `tools/registry.py`, `tools/exceptions.py`, `render_models.py` | Structural typing seams used for DI. |
| **3 Workers** | `walker.py`, `lens.py`, `lmstudio_client.py`, `lmstudio_lifecycle.py`, `compaction.py`, `tools/loop.py`, `tools/{read_file,grep,search_code,context,pack_hash,gitnexus_*}.py`, `tools/safety.py`, `graph_awareness.py`, `renderer.py`, `findings_aggregator.py`, `findings_partial.py`, `error_artifacts.py`, `cross_cutting.py` | Single-responsibility workers consumed by phases. |
| **4 Phases** | `phases/preflight.py`, `phases/discovery.py`, `phases/file_audit.py`, `phases/crosscut.py`, `phases/aggregate.py` | Implement the `Phase` protocol; emit events. |
| **5 Orchestrator** | `auditor.py` | `run_audit` drives all phases; brackets Lifecycle. **Forbidden from importing TUI** (enforced by AST test). |
| **6 Event consumers** | `subscribers/{disk_writer,headless_subscriber,tui_subscriber,metrics}.py` | Bounded-queue consumers attached to `EventBus`. |
| **7 CLI** | `cli.py`, `cli_audit.py`, `cli_aggregate.py`, `cli_doctor.py`, `cli_wizard.py`, `cli_config_show.py`, `cli_handlers.py`, `lifecycle_cli.py` | Thin composition root + per-command dispatch. |
| **8 TUI** | `tui/{app,launcher,monitor,runtime,view}.py`, `tui/widgets/*.py` | Optional Textual UI; consumes `EventBus`. Reaches `auditor` via `TYPE_CHECKING` import only. |
| **9 Entrypoints** | `__main__.py`, `__init__.py` | `python -m senex` → `cli.main`. |

## 3. Public Interfaces

| Module | Surface |
|---|---|
| `events.py` | `EventBus.subscribe(name, capacity)`, `CommandBus.subscribe()`, ~40 `BaseEvent` subclasses (`RunStart`, `FileStart`, `OutputTick`, `ThinkingTick`, lifecycle events, etc.) |
| `auditor.py` | `async run_audit(repo, config, lens, bus, command_bus, config_path, output_root, resume, allow_mixed_resume) -> int` (the only public entry into the pipeline) |
| `walker.py` | `Walker(repo, cfg, bus).walk() -> WalkResult` |
| `lens.py` | `Lens.load(name) -> Lens`; `lens.tools: dict[str, LensTool]` |
| `config.py` | `SenexConfig.load(path) -> SenexConfig`; dataclasses `LmStudioCfg`, `WalkerCfg`, `RepoCfg` |
| `phases/base.py` | `Phase` Protocol: `do_work(state)`, `read_state()`, `write_state(d)`; `PhaseAborted`, `ResumeIncompatible` |
| `llm_client.py` | `LLMClient` Protocol: `chat(*, task, messages, schema, tools)`, `list_loaded_models`, `probe_capabilities`, `count_tokens`, `compute_fingerprint`, `aclose` |
| `tools/registry.py` | `ToolRegistry.register(name, fn)`, `dispatch(name, args)` |
| `tools/loop.py` | `ToolLoop` — bounded multi-call iteration, budget + compaction trigger |
| `subscribers/base.py` | `Subscriber` Protocol: `consume(event)`, `shutdown()`, `name`, `queue_capacity`, `drop_policy` |
| `renderer.py` | `Renderer(output_root, redactor).render_run(...) -> Path` |
| `cli.py` | `def main(argv) -> int`; six `cmd_<name>(args)` functions |

## 4. The LLM Abstraction Seam

The codebase depends on the `LLMClient` Protocol (`senex/llm_client.py`),
not on `LMStudioClient` directly, with two exceptions worth knowing:

1. **Single instantiation site**: `senex/auditor.py:51` imports
   `LMStudioClient`, and `senex/auditor.py:331` instantiates it. The
   subsequent fingerprint-pin block (`auditor.py:331-358`) is the only
   place that touches concrete client internals.
2. **Type imports**: `compaction.py`, `cross_cutting.py`,
   `phases/file_audit.py`, and several tests import dataclass shapes
   (`ChatMessage`, `ChatResponse`, `ToolCall`, `ToolCallFunction`,
   `LoadedModelInfo`, `ProbedCapabilities`, `ToolSchema`) from
   `lmstudio_client`. These are pure data carriers and are mechanically
   movable to `ollama_client` (or a neutral `llm_types.py`).

Effect: a backend swap is a one-site instantiation change plus type-import
rerouting — provided the new client implements the same Protocol.

## 5. Phase Pipeline

`run_audit` walks five phases in order. Each phase advances a checkpoint
state and emits structured events.

| Phase | Module | Responsibilities |
|---|---|---|
| Preflight | `phases/preflight.py` | 6 checks: `lms_reachable`, `model_loaded_or_loadable`, `sampling_ranges`, `schema_with_thinking`, `streaming`, `lifecycle_backend`. Fails fast on env/config drift. |
| Discovery | `phases/discovery.py` | `Walker.walk()`, write `discovered.json`, emit `DiscoveryStart/Complete`. |
| File audit | `phases/file_audit.py` | Per-file LLM call via `ToolLoop`; emits `FileStart`, `FileComplete`, `OutputTick`, `ThinkingTick`. **Heaviest phase** — coupled to `Lens`, `LLMClient`, `ToolLoop`, `EventBus`, `Renderer`, `Findings*`. |
| Crosscut | `phases/crosscut.py` (delegates to `cross_cutting.py`) | Post-file aggregate analysis; emits `CrosscutStart/Complete`. |
| Aggregate | `phases/aggregate.py` | `findings_aggregator` merges partials; `Renderer` writes final reports. |

## 6. Event Bus Topology

`events.EventBus` is a per-subscriber bounded `asyncio.Queue` fan-out.
Subscribers declare their `drop_policy`:

| Subscriber | Drop policy | Why |
|---|---|---|
| `DiskWriterSubscriber` | `block` | Never drop; events are the audit trail. |
| `TuiSubscriber` | `drop_token_only` | High-frequency `OutputTick`/`ThinkingTick` are droppable; structural events are not. |
| `HeadlessSubscriber` | `drop_oldest` | Console output, lossy is acceptable. |
| `MetricsCollectorSubscriber` | `block` | Counts must be exact. |

`CommandBus` is the inverse channel: TUI → auditor (e.g. cancel).

## 7. Lifecycle + Runlock

`Lifecycle` (in `lmstudio_lifecycle.py`) brackets the audit with explicit
model load/unload. Two pluggable backends — `LMStudioSDKBackend` (the
`lmstudio` Python SDK) and `LMSCLIBackend` (`lms` CLI subprocess) — are
selected by `LifecycleBackendFactory.select()`. Both implement the same
`is_loaded / load / unload / list_loaded` interface.

Runlock (`runlock.py`) is a PID-based advisory lock that prevents two
senex runs from racing on the same model + repo. Lifecycle integrates
with runlock so `release` only unloads the model when no other holders
remain.

Lifecycle emits ~14 event types: `ModelLoadRequested`, `ModelLoadStarted`,
`ModelLoadComplete`, `ModelLoadFailed`, `ModelLoadWaiting`,
`ModelLoadStillWaiting`, `ModelLoadCompleteAfterWait`,
`ModelUnloadStarted`, `ModelUnloadComplete`, `ModelUnloadFailed`,
`ModelUnloadSkipped`, `RunLockAcquired`, `RunLockReleased`,
`ModelFingerprintChanged`.

## 8. Tools + ToolLoop

`tools/registry.py` maps tool names to async callables. Built-in tools
(`read_file`, `grep`, `search_code`, `context`, `pack_hash`, plus
`gitnexus_{query,context,impact}`) live alongside the registry.
`tools/safety.py` enforces path / size / timeout guards.

`tools/loop.py:ToolLoop` is the iteration controller used by phases that
need multi-turn LLM tool dispatch. Today it drives **OpenAI-style native
`tool_calls`**: call `chat()` → if `response.tool_calls`, dispatch via
registry → append tool result message → loop until `finish_reason == "stop"`
or budget exhausted. This is the single point where the migration plan
inserts the ReAct loop (see `docs/MIGRATION_PLAN.md` §6).

## 9. Config Layering

```
defaults (in dataclasses)
    ↓ overridden by
[lmstudio] / [walker] / ... blocks in TOML (config_path)
    ↓ overridden by
[[repos]].lmstudio per-repo TOML overrides (deep-merged)
    ↓ overridden by
CLI flags (--model, --auto-load, --auto-unload)
    ↓ overridden by
TUI runtime selections (model picker)
```

Deep-merge implementation: `senex/config.py:288 _deep_merge`. The full
schema is documented in `senex.config.toml.example`.

## 10. CLI Surface

| Command | Module | Purpose |
|---|---|---|
| `senex audit [--model] [--auto-load] [--auto-unload] [--resume]` | `cli_audit.py` | Run full audit. |
| `senex aggregate` | `cli_aggregate.py` | Re-render from existing partials. |
| `senex doctor [--json]` | `cli_doctor.py` | Run preflight checks standalone. |
| `senex wizard` | `cli_wizard.py` | Interactive config generator. |
| `senex config show` | `cli_config_show.py` | Dump resolved config. |
| `senex lifecycle status \| clear-locks` | `lifecycle_cli.py` | Inspect/manage model lifecycle + runlocks. |
| `senex tui` | `tui/app.py` | Launch Textual UI. |

## 11. Tests

Layered to mirror the package:

| Layer | Path | Style |
|---|---|---|
| Unit | `tests/unit/` | Pure-function + mocked-dep tests; respx for HTTP. |
| Integration | `tests/integration/` | Wire real workers together; respx for LLM HTTP. |
| Recorded | `tests/recorded/` | Replay against pinned HTTP cassettes (`OLLAMA_RECORD=1`/`LMS_RECORD=1` to regenerate). |
| TUI | `tests/tui/` | Textual `Pilot`-driven scenarios. |
| Golden | `tests/golden/` | Snapshot rendered Markdown / JSON. |
| Fixtures | `tests/fixtures/repos/`, `tests/fixtures/lms_responses/` | Seed repos + recorded responses. |

## 12. Coupling Hot Spots

- **`phases/file_audit.py` (~750 LOC)** is the fattest module. It pulls
  `Lens`, `LLMClient`, `ToolLoop`, `EventBus`, `Renderer`, `findings_*`,
  `compaction`, plus the LMS error matrix. Justified for the central
  per-file phase, but any refactor here has high blast radius — run
  `gitnexus_impact` before edits.
- **`auditor.py:331` LMS instantiation** is the single seam between the
  abstract `LLMClient` Protocol and its concrete implementation. The
  fingerprint-pin logic that follows is backend-specific and must be
  re-derived per backend.
- **Type imports from `lmstudio_client`** (in `compaction.py`,
  `cross_cutting.py`, `phases/file_audit.py`) make backend renames touch
  a wider set of files than the abstraction alone would suggest.

## 13. Findings — Audit Summary

- ✅ **DAG, no cycles.** TUI is correctly isolated via `TYPE_CHECKING`;
  `auditor` does not import TUI; AST test enforces this.
- ✅ **Single LLM instantiation site.** Backend swap is mechanically
  small at the abstraction layer, even if config/CLI/test surface is
  wide.
- ✅ **Foundations are clean.** `events.py`, `config.py`, `atomic_io.py`
  have zero internal dependencies.
- ⚠️ **Type leak via `lmstudio_client`.** `ChatMessage`, `ChatResponse`,
  `ToolCall*` should arguably live in a neutral `llm_types.py`; today
  they leak `lmstudio` into modules that should be backend-agnostic.
  Migration recommendation: re-home these in `senex/llm_client.py` (or a
  new `llm_types.py`) at the same time as the Ollama client lands.
- ⚠️ **`phases/file_audit.py` size.** 750 LOC is the upper bound for
  comfortable maintenance; future work could extract the error-recovery
  matrix into its own module.
- ⚠️ **`cli_handlers.py` (109 LOC)** appears to be a legacy shim that
  predates the per-command split; verify all paths are still live or
  delete dead handlers.
- ✅ **No dead modules detected** in this pass; `handoff.py` and
  `error_artifacts.py` are low-frequency but live.
