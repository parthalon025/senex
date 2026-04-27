# Milestone 9: Subscribers + TUI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting. All `senex/` code complies with `docs/superpowers/conventions.md` (`from __future__ import annotations`; `mypy --strict`; named exceptions only; bounded queues; Pydantic v2 with `extra="forbid"`).

## Context

M9 delivers the user-facing layer: three subscriber implementations (DiskWriter, Headless, Metrics) plus the Textual TUI (Launcher screen + Monitor screen + 5 widgets + TuiSubscriber + command-bus keybindings) and the `senex view` replay handler. The TUI is a thin consumer of the bus — every line on screen is driven by an event the auditor already publishes. `senex view <audit-dir>` replays a completed run's `events.jsonl` through the same Monitor for post-hoc inspection (offline; no LMS required).

**Architectural intent:** Subscribers and TUI are *passive consumers* of the event stream. The auditor coroutine doesn't know they exist; it just publishes. This decoupling means the TUI can crash without taking the audit down — `DiskWriterSubscriber` is the durable record (write-ahead persistence; publisher BLOCKS on its queue per spec §5.6.1). The `TuiSubscriber` drops `*Tick` events under backpressure so a slow render loop never blocks the auditor.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration, M4 Lifecycle, M5 Tool Framework, M6 Compaction, M7 Renderer + Aggregator, M8 Phases + Auditor.
- **Required modules from prior work:**
  - `senex/events.py` — `BaseEvent`, all event subclasses, `EventBus`, `CommandBus`, `Command` (M1 Tasks 1.3.2–1.3.5)
  - `senex/config.py` — `SenexConfig`, `RuntimeConfig`, `load_config()`, `resolve_config()` (M1 Task 1.2)
  - `senex/auditor.py` — `async run_audit(repo, config, lens, bus, command_bus, resume=False)` (M8 Task 8.7)
  - `senex/lmstudio_client.py` — `client.list_loaded_models()` for Launcher's model dropdown (M3)
  - `senex/lens.py` — for Launcher's lens selector (correctness only in v1) (M1)
  - `senex/checkpoint.py` — used by Launcher's resume detector to inspect existing audit dirs (M1)
- **Required tools/state:**
  - `textual >= 0.60` declared in `pyproject.toml` (already added M1).
  - `pytest-asyncio` + `textual.pilot.Pilot` (in `[tool.pytest.ini_options]` from M1).
  - An existing audit dir from `tests/recorded/test_run_audit_e2e.py` (M8) for `senex view` replay tests.
- **GitNexus impact check (mandatory before edits):** because all M9 modules are new files, blast radius is empty for the implementation. However, the planner MUST run `gitnexus_impact({target: "EventBus.publish", direction: "downstream"})` and `gitnexus_impact({target: "CommandBus.post", direction: "downstream"})` before adding any new subscriber registration call sites — confirms no existing consumer is broken by added queue capacity.

## Deliverable

This milestone creates the following files:

- `senex/subscribers/__init__.py` — re-exports the four subscriber classes + `Subscriber` protocol.
- `senex/subscribers/base.py` — `Subscriber` protocol; `SubscriberQueueFull`, `SubscriberShutdownError`.
- `senex/subscribers/disk_writer.py` — `DiskWriterSubscriber` (write-ahead persistence to `events.jsonl`; `drop_policy = "block"`).
- `senex/subscribers/headless_subscriber.py` — `HeadlessSubscriber` (stdout progress + final summary).
- `senex/subscribers/metrics.py` — `MetricsCollectorSubscriber` (running totals; pure aggregator, no I/O).
- `senex/subscribers/tui_subscriber.py` — `TuiSubscriber` (rate-limited 10 Hz; `drop_policy = "drop_token_only"`).
- `senex/tui/__init__.py`
- `senex/tui/app.py` — `SenexApp(App)` with `LauncherScreen` and `MonitorScreen`.
- `senex/tui/launcher.py` — `LauncherScreen(Screen)`.
- `senex/tui/monitor.py` — `MonitorScreen(Screen)` (binds keys + composes widgets).
- `senex/tui/widgets/__init__.py`
- `senex/tui/widgets/progress.py` — `ProgressWidget(Widget)`.
- `senex/tui/widgets/current_file.py` — `CurrentFileWidget(Widget)`.
- `senex/tui/widgets/findings_panel.py` — `FindingsPanelWidget(Widget)`.
- `senex/tui/widgets/error_banner.py` — `ErrorBannerWidget(Widget)`.
- `senex/tui/widgets/status.py` — `StatusStripWidget(Widget)`.
- `senex/tui/exceptions.py` — `WidgetRenderError` (caught at the Textual boundary; never propagates).
- `senex/tui/view.py` — `async run_view(audit_dir: Path, speed: float = 1.0)` — replay entry point.
- Tests:
  - `tests/unit/test_subscribers_base.py`
  - `tests/unit/test_subscribers_disk_writer.py`
  - `tests/unit/test_subscribers_headless.py`
  - `tests/unit/test_subscribers_metrics.py`
  - `tests/unit/test_subscribers_tui.py`
  - `tests/tui/test_app.py`
  - `tests/tui/test_launcher.py`
  - `tests/tui/test_widget_progress.py`
  - `tests/tui/test_widget_current_file.py`
  - `tests/tui/test_widget_findings_panel.py`
  - `tests/tui/test_widget_error_banner.py`
  - `tests/tui/test_widget_status.py`
  - `tests/tui/test_command_bus_keybindings.py`
  - `tests/tui/test_view_replay.py`
  - `tests/fixtures/events/recorded_run.jsonl` — fixture for replay + widget tests.

## Downstream consumers

- **M10** CLI `senex audit` selects between `TuiSubscriber` (default) and `HeadlessSubscriber` (`--no-tui`).
- **M10** CLI `senex view <audit-dir>` invokes `senex.tui.view.run_view()` from this milestone.
- **M10** Live gate 13b verifies the TUI renders Launcher → Monitor → completion end-to-end.

## Spec sections referenced

- §5.6 Event Bus & Subscribers — full event-type table; the three always-on subscribers.
- §5.6.1 Bus semantics — per-subscriber backpressure rules (DiskWriter blocks; TuiSubscriber drops `ThinkingTick`/`OutputTick`; Metrics drops oldest tick).
- §5.6.2 Command bus — typed commands + the auditor's polling points (between files, between phases, after each LMS chunk).
- §5.7 TUI — Launcher fields, Monitor widgets, refresh rate (~10 Hz), findings deque size (30), keybinding table.
- ARCH-5 (carry-over from spec §5.5 / §5.6) — streaming Tick events fan out from `lmstudio_client.chat()`.
- ARCH-16 (carry-over from spec §5.6.2 / §5.7) — keybinding namespacing: Monitor screen owns `q`/`Ctrl+Q`/`s`/`r`/`p`/`t`; Launcher owns nothing global; replay-mode reuses Monitor with `Skip`/`Rerun` disabled.

## Conventions referenced

- §3 Async / concurrency — bounded queues, no sync I/O in async paths, cancellation propagation.
- §6 Testing — TDD with Textual `Pilot`; no mocks of internal code; coverage target 85% on `senex/subscribers/*`.
- §13 Documentation — module docstrings cite spec section; Google-style function docstrings.

## Key contracts

### `Subscriber` protocol (Task 9.1)

```python
from typing import Literal, Protocol
from senex.events import BaseEvent

class Subscriber(Protocol):
    name: str
    queue_capacity: int  # default 1024 per conventions §3
    drop_policy: Literal["block", "drop_oldest", "drop_token_only"]

    async def consume(self, event: BaseEvent) -> None:
        """Process a single event. Called once per event delivered by the bus."""

    async def shutdown(self) -> None:
        """Called by the bus on RunComplete or run abort. Flushes any buffered state."""
```

### Named exceptions (Task 9.1)

```python
# senex/subscribers/base.py
class SubscriberQueueFull(Exception):
    """Raised by the bus when DiskWriter's queue is full and 'block' mode is exceeded
    by the wait timeout (data integrity > liveness; see §5.6.1)."""

class SubscriberShutdownError(Exception):
    """Raised when a subscriber's shutdown() coroutine fails to flush before timeout."""

# senex/tui/exceptions.py
class WidgetRenderError(Exception):
    """Raised by a widget's on_event when it cannot reconcile state. Caught at the
    Textual app boundary (SenexApp._handle_widget_error) and surfaced as an
    ErrorBanner update; NEVER propagates out of the TUI."""
```

Per conventions §4: never bare `except`; catch `WidgetRenderError` only at the app boundary.

### Drop policies summary

| Subscriber | `drop_policy` | When queue full |
|---|---|---|
| `DiskWriterSubscriber` | `"block"` | Publisher awaits; `events.jsonl` MUST be lossless. |
| `HeadlessSubscriber` | `"drop_oldest"` | Drops oldest `*Tick`; preserves all phase events. |
| `MetricsCollectorSubscriber` | `"drop_oldest"` | Drops oldest `*Tick`; preserves all phase events. |
| `TuiSubscriber` | `"drop_token_only"` | Drops `ThinkingTick`/`OutputTick` only; phase events always processed. |

The bus dispatcher reads `subscriber.drop_policy` and applies it when the per-subscriber queue is full; the policy is per-subscriber, not per-event-type. `"drop_token_only"` is a TuiSubscriber-specific shortcut: drop ONLY `ThinkingTick` / `OutputTick`; for any other event when full, log + raise `SubscriberQueueFull` (loud failure).

## Watch-outs

- **DiskWriter MUST block the publisher when full.** Asymmetric backpressure: `events.jsonl` is the durable record; losing events corrupts crash recovery and resume. Other subscribers drop; DiskWriter alone blocks.
- **`TuiSubscriber` drops `*Tick` ONLY**, never `RunStart`, `FileComplete`, `FileError`, or `RunComplete`. Boundary events drive state transitions; ticks animate counters.
- **Launcher's model dropdown calls `client.list_loaded_models()`.** LM Studio must be reachable when Launcher renders; on failure the dropdown shows a `"[no LM Studio]"` placeholder and the Start button is disabled. NEVER surface raw exceptions to the user.
- **Command bus polling cadence is owned by the auditor**, not the TUI. `p` posts `Command(type="Pause")` immediately, but the auditor reads it only at well-defined points (between files, between phases, after each LMS chunk per §5.6.2). Status strip surfaces `"Pausing…"` until the next boundary so the user knows the lag is normal.
- **`senex view` is offline.** It does NOT call LMS, does NOT need a model loaded. It only consumes `events.jsonl`; truncated streams (no `RunComplete`) surface a warning banner.
- **TUI render rate-limited to 10 Hz.** Coalesce ticks in `TuiSubscriber` (one batched widget update per 100ms). Widgets MUST be idempotent — applying the same event twice yields the same state.
- **Tick events MUST flow through `TuiSubscriber`.** A widget that reads from `MetricsCollectorSubscriber` directly bypasses the throttle. The status strip is the ONLY widget allowed to read Metrics directly (and only on phase events, never ticks).
- **Threading boundary.** Textual runs widget callbacks on its event loop. The auditor runs `await run_audit(...)` as an `asyncio.create_task` started by the Launcher. Both must share the same loop. Spec §3 conventions: no `threading`.
- **Cancellation propagation.** When the user hits `Ctrl+Q`, `SenexApp` cancels the audit task; the cancellation surfaces as `asyncio.CancelledError` inside `run_audit`. Each subscriber's `consume()` and `shutdown()` MUST re-raise `CancelledError` after cleanup (per conventions §3).
- **No sync I/O on async paths.** `DiskWriterSubscriber.consume()` writes via `asyncio.to_thread(...)` for `fsync` or uses `aiofiles`. Same for `HeadlessSubscriber.consume()` (stdout writes through `loop.run_in_executor` — `print()` is blocking).
- **ANSI/secret stripping.** Any LLM-derived text rendered in widgets (finding titles, error messages) is already stripped by `secret_redactor.py` (§5.10) before being emitted as event payload. Widgets MUST NOT call the redactor again — double-stripping is a smell. Widgets render the event field as-is.
- **Findings deque eviction is silent.** A 31st finding evicts the oldest with no warning event. This is by design (TUI is a window, not a log).

## Patterns to follow

- **Textual Pilot tests:**
  ```python
  from textual.app import App
  from senex.tui.app import SenexApp
  async def test_launcher_to_monitor_transition() -> None:
      async with SenexApp(...).run_test() as pilot:
          await pilot.press("tab"); await pilot.press("enter")  # focus + click Start
          await pilot.pause()
          assert isinstance(pilot.app.screen, MonitorScreen)
  ```
- **Subscriber TDD:** construct subscriber → drive a deterministic event sequence via `await sub.consume(evt)` → assert post-state. No bus required for unit tests; the bus is integration-tested separately.
- **Widget separation:** each widget = one file + one test file. Widgets are stateful; `on_event(event)` mutates state; `compose()` renders.
- **Replay determinism:** `senex view` reads `events.jsonl` line-by-line, validates each line against the discriminated `BaseEvent` union, and feeds the resulting models into the same `TuiSubscriber.consume()` used live. Default speed = 1.0× wall-clock; `--speed N` accelerates.
- **Pre-commit checks:** `gitnexus_detect_changes({scope: "staged"})` MUST be run before each task's commit to confirm scope.

## Tasks

### Task 9.1: Subscriber protocol + DiskWriterSubscriber

**Files:**
- Create: `senex/subscribers/__init__.py`
- Create: `senex/subscribers/base.py`
- Create: `senex/subscribers/disk_writer.py`
- Create: `tests/unit/test_subscribers_base.py`
- Create: `tests/unit/test_subscribers_disk_writer.py`

**Spec anchors:** §5.6 (event bus + subscribers), §5.6.1 (bus semantics, slow-subscriber policy table); conventions §3 (bounded queues, no sync I/O), §8 (atomic writes, append-only NDJSON).

- [x] **Step 9.1.1: Failing tests.**
  - `test_subscriber_protocol_attrs` — assert `Subscriber` has `name: str`, `queue_capacity: int`, `drop_policy: Literal[...]`, `consume`, `shutdown`. Use `typing.get_type_hints` + `inspect`.
  - `test_disk_writer_writes_events_in_seq_order` — feed 5 events (`seq=1..5`); assert `events.jsonl` lines are JSON-decodable in seq order.
  - `test_disk_writer_writes_one_line_per_event` — assert N events → N newline-terminated lines (no `[`/`]` array wrapper).
  - `test_disk_writer_fsyncs_on_each_line` — patch `os.fsync`; assert called per `consume` call.
  - `test_disk_writer_blocks_publisher_when_queue_full` — fill bounded queue (capacity=2) with 3 puts; the third `await bus.publish(evt)` MUST be suspended until a slot opens (use `asyncio.wait_for` with short timeout to assert TimeoutError, then drain queue → assert publish completes).
  - `test_disk_writer_shutdown_flushes_remaining` — fill queue with 5 events, call `shutdown()`, assert all 5 are persisted.
  - `test_disk_writer_drops_nothing_under_pressure` — assert no event is silently dropped (loop over `consume` 100×, file has exactly 100 lines).
  - `test_disk_writer_emits_subscriber_queue_full_on_block_timeout` — set `block_timeout=0.01`; fill queue; assert `SubscriberQueueFull` raised by bus.

- [x] **Step 9.1.2: Implement** `Subscriber` protocol in `senex/subscribers/base.py` (with `SubscriberQueueFull`, `SubscriberShutdownError`).

- [x] **Step 9.1.3: Implement** `DiskWriterSubscriber` in `senex/subscribers/disk_writer.py`:
  - `name = "disk_writer"`, `queue_capacity = 1024`, `drop_policy = "block"`.
  - `__init__(self, audit_dir: Path)` — opens `audit_dir / "events.jsonl"` in append mode (UTF-8, no BOM, LF line endings per conventions §8).
  - `async consume(event: BaseEvent) -> None` — `line = event.model_dump_json() + "\n"`; `await asyncio.to_thread(self._write_and_fsync, line)`.
  - `_write_and_fsync(line: str) -> None` — `self._fh.write(line); self._fh.flush(); os.fsync(self._fh.fileno())`.
  - `async shutdown() -> None` — drain pending; `await asyncio.to_thread(self._fh.close)`.
  - All exception paths emit a structured log line per conventions §7.

- [x] **Step 9.1.4: Run impact check** before any cross-module symbol use: `gitnexus_impact({target: "BaseEvent.model_dump_json", direction: "upstream"})` — confirm no caller assumes a specific newline behaviour.

- [x] **Step 9.1.5: Verify** `pytest tests/unit/test_subscribers_disk_writer.py -v` and `mypy senex/subscribers/` clean.

- [x] **Step 9.1.6: Commit** `feat(M9): Subscriber protocol + DiskWriterSubscriber with write-ahead persistence`.

### Task 9.2: HeadlessSubscriber

**Files:**
- Create: `senex/subscribers/headless_subscriber.py`
- Create: `tests/unit/test_subscribers_headless.py`

**Spec anchors:** §5.6 (events), §5.6.1 (drop_oldest tick policy); conventions §3 (no sync I/O), §6 (capture stdout via `capsys`).

- [ ] **Step 9.2.1: Failing tests.**
  - `test_headless_prints_one_line_per_file_complete` — feed `RunStart` + 3× `(FileStart, FileComplete)` + `RunComplete`; capture stdout; assert exactly 3 `[N/M] auditing <path> — X findings (high=H, medium=M, low=L)` lines.
  - `test_headless_suppresses_tick_events` — feed 100× `ThinkingTick`; assert stdout produces 0 lines.
  - `test_headless_run_complete_prints_final_summary` — assert final line contains rollup totals + duration + exit_status from `RunComplete`.
  - `test_headless_drop_policy_is_drop_oldest` — assert `sub.drop_policy == "drop_oldest"`.
  - `test_headless_file_error_prints_error_line` — feed `FileError`; assert stdout contains `ERROR <path>: <error_kind>`.
  - `test_headless_handles_unicode_paths` — feed `FileStart(path="src/héllo.py")`; assert stdout encodes UTF-8 cleanly.

- [ ] **Step 9.2.2: Implement.**
  - `name = "headless"`, `queue_capacity = 1024`, `drop_policy = "drop_oldest"`.
  - Format per spec §5.7-style: `[12/87] auditing src/foo.py — 2 findings (high=1, medium=1, low=0)`.
  - Stdout writes via `await asyncio.to_thread(sys.stdout.write, line)`; `flush()` on each line.
  - `RunComplete` produces a final block:
    ```
    --- Run complete ---
    Duration: 12m34s
    Files: 87 audited, 0 errors, 2 skipped
    Findings: HIGH=4 MEDIUM=17 LOW=22 HEALTHY=44
    Exit status: success
    ```
  - Suppress all `*Tick` event types (match by `isinstance(event, (ThinkingTick, OutputTick))`).

- [ ] **Step 9.2.3: Verify** + **commit** `feat(M9): HeadlessSubscriber with concise progress lines + final summary`.

### Task 9.3: MetricsCollectorSubscriber

**Files:**
- Create: `senex/subscribers/metrics.py`
- Create: `tests/unit/test_subscribers_metrics.py`

**Spec anchors:** §5.6 (events), §5.6.1 (drop_oldest tick policy); conventions §3 (no I/O in pure aggregator).

- [ ] **Step 9.3.1: Failing tests.**
  - `test_metrics_starts_zeroed` — assert `sub.metrics == Metrics(files_done=0, ...)`.
  - `test_metrics_increments_files_done_on_file_complete` — feed 5× `FileComplete`; assert `metrics.files_done == 5`.
  - `test_metrics_aggregates_findings_by_priority` — feed `FileComplete(finding_counts={"high":2,"medium":1,"low":3,"healthy":0})` × 2; assert `metrics.findings_by_priority == {"high":4,"medium":2,"low":6,"healthy":0}`.
  - `test_metrics_increments_tool_calls_on_tool_call_event` — feed 7× `ToolCall`; assert `metrics.tool_calls == 7`.
  - `test_metrics_increments_compactions_on_compaction_complete` — assert `metrics.compactions` increments only on `CompactionComplete`, not `CompactionTriggered`.
  - `test_metrics_sums_thinking_seconds_from_thinking_complete` — feed `ThinkingComplete(latency_ms=1500)` × 2; assert `metrics.total_thinking_seconds == 3.0`.
  - `test_metrics_sums_output_seconds_from_output_complete` — symmetric.
  - `test_metrics_drop_policy_is_drop_oldest` — assert `sub.drop_policy == "drop_oldest"`.
  - `test_metrics_no_io` — patch `builtins.open`; feed 100 events; assert `open` never called.

- [ ] **Step 9.3.2: Implement.**
  - `Metrics` is a Pydantic v2 model with `extra="forbid"`: `files_done: int`, `findings_by_priority: dict[Literal["high","medium","low","healthy"], int]`, `tool_calls: int`, `compactions: int`, `total_thinking_seconds: float`, `total_output_seconds: float`.
  - `MetricsCollectorSubscriber.metrics` is a read-only `@property` returning a frozen copy (`Metrics.model_validate(self._metrics.model_dump())`).
  - `name = "metrics"`, `queue_capacity = 1024`, `drop_policy = "drop_oldest"`.
  - `consume(event)` is a `match`/`case` on event subclasses (conventions §1: prefer pattern matching).

- [ ] **Step 9.3.3: Verify** + **commit** `feat(M9): MetricsCollectorSubscriber with running totals`.

### Task 9.4: Textual app skeleton

**Files:**
- Create: `senex/tui/__init__.py`
- Create: `senex/tui/app.py`
- Create: `senex/tui/exceptions.py`
- Create: `tests/tui/test_app.py`
- Create: `tests/tui/__init__.py`

**Spec anchors:** §5.7 (TUI overview); ARCH-16 (keybinding namespacing); conventions §3 (cancellation), §4 (named exceptions).

- [ ] **Step 9.4.1: Failing tests** (Pilot smoke).
  - `test_senex_app_constructs_with_required_args` — `SenexApp(config_path=Path("..."), repo_path_default=Path("..."))` returns instance with both screens registered.
  - `test_senex_app_on_mount_pushes_launcher` — `async with app.run_test() as pilot: await pilot.pause(); assert isinstance(pilot.app.screen, LauncherScreen)`.
  - `test_senex_app_start_audit_pushes_monitor_and_spawns_task` — call `app.start_audit(rt_cfg)`; `await pilot.pause()`; assert current screen is `MonitorScreen` AND `app._audit_task` is a non-done `asyncio.Task`.
  - `test_senex_app_audit_task_exception_renders_error_screen` — feed a `start_audit` whose `run_audit` immediately raises `RuntimeError("boom")`; assert the done-callback caught it AND the error banner shows `"boom"`; assert NO exception propagates from `run_test`.
  - `test_senex_app_ctrl_q_cancels_audit_task_and_exits` — push Monitor; press `ctrl+q`; assert task is cancelled (`task.cancelled() is True`) AND app exited cleanly.

- [ ] **Step 9.4.2: Implement** `SenexApp(App)`:
  ```python
  class SenexApp(App):
      def __init__(
          self,
          config_path: Path,
          repo_path_default: Path,
          bus: EventBus | None = None,
          command_bus: CommandBus | None = None,
      ) -> None:
          super().__init__()
          self._config_path = config_path
          self._repo_path_default = repo_path_default
          self._bus = bus or EventBus()
          self._command_bus = command_bus or CommandBus()
          self._audit_task: asyncio.Task[None] | None = None

      async def on_mount(self) -> None:
          await self.push_screen(LauncherScreen(self._config_path, self._repo_path_default))

      def start_audit(self, runtime_config: RuntimeConfig) -> None:
          self.push_screen(MonitorScreen(self._bus, self._command_bus))
          self._audit_task = asyncio.create_task(
              run_audit(
                  repo=runtime_config.repo,
                  config=runtime_config.config,
                  lens=runtime_config.lens,
                  bus=self._bus,
                  command_bus=self._command_bus,
                  resume=runtime_config.resume,
              )
          )
          self._audit_task.add_done_callback(self._on_audit_done)

      def _on_audit_done(self, task: asyncio.Task[None]) -> None:
          if task.cancelled():
              return
          exc = task.exception()
          if exc is not None:
              self.call_from_thread(self._render_error, exc)
  ```
  - `_render_error(exc: BaseException) -> None` — pushes ErrorBanner update; logs structured event; never raises.

- [ ] **Step 9.4.3: Implement WidgetRenderError** in `senex/tui/exceptions.py` per "Named exceptions" section above. Add `SenexApp._handle_widget_error(self, error: WidgetRenderError) -> None` that catches at the Textual boundary.

- [ ] **Step 9.4.4: Verify** Pilot tests + `mypy senex/tui/app.py`.

- [ ] **Step 9.4.5: Commit** `feat(M9): SenexApp skeleton with Launcher/Monitor screens + audit task lifecycle`.

### Task 9.5: Launcher screen

**Files:**
- Create: `senex/tui/launcher.py`
- Create: `tests/tui/test_launcher.py`

**Spec anchors:** §5.7 (Launcher fields); conventions §3 (no blocking I/O — model list call is async).

**Pseudo-DSL layout:**

```
┌─ senex — Launcher ──────────────────────────────────────────────┐
│ Repo path:    [Input    ] (default: ~/code/myrepo)              │
│ Lens:         [Select correctness ▾]                            │
│ Model:        [Select google/gemma-4-26b-a4b ▾]   (loaded only) │
│ Temperature:  [Input 0.2]   Max tokens: [Input 4096]            │
│ Seed:         [Input 0]     [randomize]                         │
│ Include tests:[Switch off]  Save traces: [Switch on]            │
│                                                                  │
│ Resume:  ⓘ  3 prior runs detected — [Resume] [Fresh]            │
│                                                                  │
│            [ Reset ]   [ Start audit ]                          │
└──────────────────────────────────────────────────────────────────┘
```

- [ ] **Step 9.5.1: Failing tests** (one per field; one for resume detection; one for submission).
  - `test_launcher_renders_all_form_fields` — Pilot: assert each `#repo_path`, `#lens_select`, `#model_select`, `#temperature`, `#max_tokens`, `#seed`, `#include_tests`, `#save_traces`, `#start_btn`, `#reset_btn` is mounted.
  - `test_launcher_repo_path_input_typing` — `await pilot.click("#repo_path"); await pilot.type("/tmp/my-repo"); assert query_one("#repo_path", Input).value == "/tmp/my-repo"`.
  - `test_launcher_model_dropdown_populated_from_lms` — patch `client.list_loaded_models()` → `["a", "b"]`; mount; assert Select options are `["a", "b"]`.
  - `test_launcher_model_dropdown_unreachable_lms_shows_placeholder` — patch `list_loaded_models` to raise `LMSConnectionLost`; mount; assert Select option is `"[no LM Studio]"` AND Start button `disabled is True`.
  - `test_launcher_resume_detection_finds_existing_dirs` — create `<output_root>/<repo_name>/2026-04-26-abc123/` fixture; mount; assert "Resume" button visible AND points to that dir.
  - `test_launcher_resume_no_prior_runs_hides_resume_button` — empty `<output_root>`; assert "Resume" button hidden.
  - `test_launcher_start_button_validates_and_calls_app_start_audit` — fill all fields; click Start; assert `app.start_audit` called with a `RuntimeConfig` whose fields exactly match form values.
  - `test_launcher_start_button_invalid_repo_path_shows_error_label` — type a non-existent path; click Start; assert `app.start_audit` NOT called AND `#error_label` visible.
  - `test_launcher_reset_button_restores_defaults_from_config` — change values; click Reset; assert all fields back to defaults from `SenexConfig`.

- [ ] **Step 9.5.2: Implement** `LauncherScreen(Screen)`:
  - `compose() -> ComposeResult` yields `Container(Vertical(Input, Select, ...))`.
  - `on_mount()` — `await self._populate_models()` and `await self._detect_resumable_runs()`.
  - `on_button_pressed(event: Button.Pressed)` — dispatches by `event.button.id`.
  - `_build_runtime_config()` — pure function, validates, returns `RuntimeConfig` (Pydantic v2; `extra="forbid"`).
  - All input validation produces `ValueError` caught locally and surfaced via `#error_label`; never raises out of the screen.

- [ ] **Step 9.5.3: Verify** + **commit** `feat(M9): Launcher screen with model dropdown + resume detection`.

### Task 9.6: Monitor screen widgets

> The 5 widgets are split into 9.6a–9.6e to ensure each gets its own TDD cycle, file, and test pair. Each step ends with its own commit.

**Common contract for every widget:**
- File: `senex/tui/widgets/<name>.py`.
- Class: `class XWidget(Widget):` inheriting `textual.widget.Widget`.
- `compose(self) -> ComposeResult` — yields child widgets (Static/Label/ProgressBar).
- `def on_event(self, event: BaseEvent) -> None` — dispatches by event subclass; never raises (catches and re-raises as `WidgetRenderError`).
- All tests use Textual `Pilot` to mount the widget inside a minimal host app and drive events.

#### Task 9.6a: ProgressWidget

**Files:**
- Create: `senex/tui/widgets/progress.py`
- Create: `tests/tui/test_widget_progress.py`

**Driven by:** `FileStart`, `FileComplete`. Computes ETA via rolling average of last N=10 per-file durations × remaining-file count.

**Pseudo layout:**
```
[  ████████░░░░░░░  ]  12/87 files  14%  ETA 23m
```

- [ ] **Step 9.6a.1: Failing tests.**
  - `test_progress_initial_render_shows_zero` — mount; assert `query_one("#progress_label").renderable == "0/0 files 0% — ETA --"`.
  - `test_progress_updates_on_file_start_total` — feed `FileStart(idx=1, total=87, path="x")`; assert label shows `1/87 files 1% — ETA --` (no ETA until first complete).
  - `test_progress_eta_after_first_complete` — feed `FileStart(idx=1)` at t=0, `FileComplete(idx=1)` at t=10s; feed `FileStart(idx=2, total=10)`; assert ETA = `9 × 10s = 90s` (≈ "1m30s").
  - `test_progress_eta_uses_rolling_window_n10` — feed 11 file completes with varying durations; assert ETA computed from last 10, not all 11.
  - `test_progress_handles_zero_total` — feed `FileStart(idx=0, total=0)`; assert no ZeroDivisionError; label shows `"0/0 files — — ETA --"`.

- [ ] **Step 9.6a.2: Implement.** Use `textual.widgets.ProgressBar` for the bar; a `Label` for the text. `on_event` matches on `FileStart` (capture `total`) and `FileComplete` (append duration to `collections.deque(maxlen=10)`).

- [ ] **Step 9.6a.3: Verify** + **commit** `feat(M9): ProgressWidget with rolling ETA`.

#### Task 9.6b: CurrentFileWidget

**Files:**
- Create: `senex/tui/widgets/current_file.py`
- Create: `tests/tui/test_widget_current_file.py`

**Driven by:** `FileStart`, `FileContextBuilt`, `ThinkingStarted`, `ThinkingTick`, `ThinkingComplete`, `OutputStarted`, `OutputTick`, `OutputComplete`, `ToolCall`, `FileComplete`.

**Pseudo layout:**
```
File: src/foo.py
Phase: thinking      Tools used: 3
Thinking tokens: 1,247   In: 4,096   Out: 312
```

Phase enum (string): `"context"` (after `FileContextBuilt`) → `"thinking"` (after `ThinkingStarted`) → `"writing"` (after `OutputStarted`) → `"rendering"` (after `OutputComplete`).

- [ ] **Step 9.6b.1: Failing tests.**
  - `test_current_file_initial_state_is_blank` — assert filename label empty; phase = `"-"`.
  - `test_current_file_file_start_sets_filename` — feed `FileStart(path="src/foo.py", idx=1, total=10)`; assert `#filename` shows `"src/foo.py"`.
  - `test_current_file_phase_transitions` — feed each of the 4 phase-starting events in order; after each, assert `#phase` label updates.
  - `test_current_file_thinking_tick_increments_counter` — feed `ThinkingTick(tokens_so_far=500)`; assert `#thinking_tokens` shows `"500"`.
  - `test_current_file_output_tick_updates_out_counter` — feed `OutputTick(tokens_so_far=312)`; assert `#out_tokens` shows `"312"`.
  - `test_current_file_tool_call_increments_tools_used` — feed 3× `ToolCall`; assert `#tools_used` shows `"3"`.
  - `test_current_file_file_complete_resets_for_next_file` — feed `FileComplete` then `FileStart(path="src/bar.py")`; assert all fields reflect bar.py with counters reset to 0.

- [ ] **Step 9.6b.2: Implement.** Per-file counters reset on `FileStart` (NOT on `FileComplete` — the user briefly sees the final state).

- [ ] **Step 9.6b.3: Verify** + **commit** `feat(M9): CurrentFileWidget with live phase + token counters`.

#### Task 9.6c: FindingsPanelWidget

**Files:**
- Create: `senex/tui/widgets/findings_panel.py`
- Create: `tests/tui/test_widget_findings_panel.py`

**Driven by:** `FileComplete.last_finding_summary` (a `FindingSummary | None` field defined on `senex.events.FileComplete` in M1 Task 1.3, per R12). The `FindingSummary` shape is `{priority, title, location | None}` — minimal subset for the TUI panel; full finding text continues to flow through `findings.partial.jsonl` for the on-disk artifacts. Spec §5.6's `FileComplete` row enumerates this field; M7's renderer is the producer (it sets the field on emit when the file produced any finding). When the file produced no findings, the field is `None` and the panel skips that row.

**Pseudo layout:**
```
Recent findings (30 max):
  [HIGH]    src/auth.py        — Password hash uses MD5
  [MEDIUM]  src/api/users.py   — Missing input validation
  [LOW]     src/utils.py       — Unused import
  ...
```

- [ ] **Step 9.6c.1: Failing tests.**
  - `test_findings_panel_initial_empty` — assert no rows.
  - `test_findings_panel_appends_on_file_complete` — feed `FileComplete(path="x", last_finding_summary={"priority":"high","title":"t"})`; assert 1 row.
  - `test_findings_panel_deque_maxlen_30` — feed 31 file completes; assert exactly 30 rows; assert the first event evicted.
  - `test_findings_panel_priority_badge_styling` — assert each priority renders with the correct CSS class (`.priority-high`, `.priority-medium`, `.priority-low`).
  - `test_findings_panel_skips_file_complete_without_finding_summary` — feed `FileComplete(last_finding_summary=None)`; assert no row added.

- [ ] **Step 9.6c.2: Implement.** Use `collections.deque(maxlen=30)`. Render via `DataTable` widget (Textual built-in); recompose on each event.

- [ ] **Step 9.6c.3: Verify** + **commit** `feat(M9): FindingsPanelWidget with deque(maxlen=30) eviction`.

**Watch-out:** `last_finding_summary` is now a first-class field on `FileComplete` (M1 Task 1.3 per R12). If a downstream change ever removes it, this widget MUST degrade to "show priority counters only" rather than silently break — coordinate via plan addendum, not silent code drift.

#### Task 9.6d: ErrorBannerWidget

**Files:**
- Create: `senex/tui/widgets/error_banner.py`
- Create: `tests/tui/test_widget_error_banner.py`

**Driven by:** `FileError`, `ToolError`, `ToolBudgetExhausted`, `CompactionError`, `ModelLoadFailed`, `ModelUnloadFailed`. Sticky banner; dismissible with `e`.

**Pseudo layout:**
```
⚠  3 errors — last: ToolError tool=read_file kind=path_rejected   [press e to dismiss]
```

- [ ] **Step 9.6d.1: Failing tests.**
  - `test_error_banner_hidden_initially` — assert `#error_banner.display == False`.
  - `test_error_banner_appears_on_first_error_event` — feed `FileError(path="x", phase="thinking", error_kind="lms", error_message="boom")`; assert `display == True` AND label shows `"1 errors — last: FileError x: boom"`.
  - `test_error_banner_count_increments` — feed 3 errors; assert label shows `"3 errors"`.
  - `test_error_banner_shows_last_message` — feed errors A, B, C; assert label contains C's message.
  - `test_error_banner_dismiss_with_e_key` — feed error; press `e`; assert `display == False`. Note: this tests the widget binding, NOT the Monitor screen binding (which also handles `e`).
  - `test_error_banner_resurfaces_on_new_error_after_dismiss` — dismiss; feed new error; assert display flips back to True.
  - `test_error_banner_handles_tool_error_kinds` — feed each of the 6 documented `ToolError.kind` values; assert each renders without crash.

- [ ] **Step 9.6d.2: Implement.**

- [ ] **Step 9.6d.3: Verify** + **commit** `feat(M9): ErrorBannerWidget with sticky errors + dismiss key`.

#### Task 9.6e: StatusStripWidget

**Files:**
- Create: `senex/tui/widgets/status.py`
- Create: `tests/tui/test_widget_status.py`

**Driven by:** `MetricsCollectorSubscriber.metrics` (read-only). Updated on every phase event (NOT ticks — see §5.7 "no per-token render"). The widget receives a reference to the metrics subscriber on construction and re-reads on each event delivery.

**Pseudo layout:**
```
HIGH 4 │ MEDIUM 17 │ LOW 22 │ HEALTHY 9 │ TOOLS 12 │ COMPACTIONS 0
```

- [ ] **Step 9.6e.1: Failing tests.**
  - `test_status_strip_initial_zeros` — mount with fresh `MetricsCollectorSubscriber`; assert all counters render `"0"`.
  - `test_status_strip_updates_on_file_complete` — drive a `FileComplete` through the metrics subscriber; trigger widget update; assert counters reflect new totals.
  - `test_status_strip_does_not_update_on_tick` — feed `ThinkingTick`; assert no widget render call (mock the render method; assert call_count unchanged).
  - `test_status_strip_handles_tool_calls_counter` — feed 5× `ToolCall`; assert `TOOLS 5`.
  - `test_status_strip_handles_compactions` — feed 2× `CompactionComplete`; assert `COMPACTIONS 2`.

- [ ] **Step 9.6e.2: Implement.** Construction takes `metrics: MetricsCollectorSubscriber`. `on_event(event)` skips `*Tick` event subclasses; on any other event, calls `_refresh()` which reads `self._metrics.metrics` and updates labels.

- [ ] **Step 9.6e.3: Verify** + **commit** `feat(M9): StatusStripWidget driven by metrics subscriber`.

### Task 9.7: TuiSubscriber

**Files:**
- Create: `senex/subscribers/tui_subscriber.py`
- Create: `tests/unit/test_subscribers_tui.py`

**Spec anchors:** §5.6.1 (drop_token_only policy; never drop boundary events); §5.7 (10 Hz refresh).

- [ ] **Step 9.7.1: Failing tests.**
  - `test_tui_subscriber_drop_policy_is_drop_token_only` — assert constant.
  - `test_tui_subscriber_dispatches_to_widgets_on_phase_events` — register 5 mock widgets; feed `FileStart`; assert all 5 received `on_event` exactly once.
  - `test_tui_subscriber_drops_thinking_ticks_under_pressure` — fill internal batch buffer with 100 `ThinkingTick` events in 100ms; assert ≤ 10 widget update calls (~10 Hz).
  - `test_tui_subscriber_never_drops_run_complete` — fill with 1024 ticks; inject 1 `RunComplete`; assert `RunComplete` was processed.
  - `test_tui_subscriber_never_drops_file_complete` — symmetric.
  - `test_tui_subscriber_never_drops_file_error` — symmetric.
  - `test_tui_subscriber_render_rate_limit_10hz` — feed 1000 events evenly across 1s; assert ≤ 11 batched widget updates (10 Hz + boundary tolerance).
  - `test_tui_subscriber_shutdown_flushes_pending_batch` — buffer 5 events; call `shutdown()`; assert all 5 dispatched before return.
  - `test_tui_subscriber_propagates_cancelled_error` — start the rate-limit loop; cancel; assert `CancelledError` re-raised after cleanup.

- [ ] **Step 9.7.2: Implement.**
  - `name = "tui"`, `queue_capacity = 1024`, `drop_policy = "drop_token_only"`.
  - Internal `asyncio.Queue` of pending events; `_pump()` task batches with `await asyncio.sleep(0.1)` between flushes; coalesces consecutive `*Tick` for the same path.
  - `consume(event)` → `await self._queue.put(event)` if not a tick OR if the queue isn't full; otherwise drop tick (log at DEBUG).
  - `register_widget(widget)` — appends to `self._widgets`; `_dispatch_batch(events)` calls `widget.on_event(evt)` for each (evt, widget); catches `WidgetRenderError`, logs, continues.

- [ ] **Step 9.7.3: Verify** + **commit** `feat(M9): TuiSubscriber with 10 Hz rate-limit + drop_token_only`.

### Task 9.8: Command bus wiring

**Files:**
- Create: `senex/tui/monitor.py` — `MonitorScreen(Screen)` with `BINDINGS`.
- Create: `tests/tui/test_command_bus_keybindings.py`

**Spec anchors:** §5.6.2 (typed Command schema; auditor polling cadence); §5.7 keybinding table; ARCH-16 (keybinding namespacing — Monitor owns these; Launcher owns nothing global).

**Keybindings table (one per row, bound on `MonitorScreen`):**

| Key | Action | Confirms? | Posts |
|---|---|---|---|
| `q` | Quit | Yes (modal dialog) | `Command(type="Quit")` |
| `ctrl+q` | Force-quit | No | `Command(type="Quit")` + cancels audit task |
| `s` | Skip current | Yes | `Command(type="Skip", target=current_path)` |
| `r` | Rerun current | Yes | `Command(type="Rerun", target=current_path)` |
| `p` | Pause/Resume toggle | No | `Command(type="Pause")` then `"Resume"` on next press |
| `t` | Toggle min-priority display | No | (local widget toggle; no Command) |

- [ ] **Step 9.8.1: Failing tests.**
  - `test_p_posts_pause_command` — `await pilot.press("p"); await pilot.pause(); assert command_bus.received == [Command(type="Pause", ...)]`.
  - `test_p_second_press_posts_resume` — press `p` twice; assert `[Pause, Resume]`.
  - `test_q_shows_confirm_dialog_then_posts_quit` — press `q`; assert dialog visible; press `enter`; assert `Command(type="Quit")` posted.
  - `test_q_dialog_dismiss_does_not_post` — press `q`; press `escape`; assert NO command posted.
  - `test_ctrl_q_posts_quit_immediately_no_dialog` — press `ctrl+q`; assert `Command(type="Quit")` posted AND no dialog rendered AND audit task cancelled.
  - `test_s_with_confirm_posts_skip_with_target_path` — set `current_path = "src/foo.py"`; press `s`; confirm; assert `Command(type="Skip", target="src/foo.py")`.
  - `test_r_with_confirm_posts_rerun_with_target_path` — symmetric.
  - `test_t_toggles_min_priority_display_locally` — press `t`; assert local state flipped; assert NO command posted.
  - `test_keybindings_only_active_on_monitor_not_launcher` — push Launcher; press `p`; assert NO command posted (ARCH-16: keybinding namespacing).

- [ ] **Step 9.8.2: Implement.**
  ```python
  class MonitorScreen(Screen):
      BINDINGS = [
          Binding("q", "request_quit", "Quit"),
          Binding("ctrl+q", "force_quit", "Force quit"),
          Binding("s", "request_skip", "Skip"),
          Binding("r", "request_rerun", "Rerun"),
          Binding("p", "toggle_pause", "Pause/Resume"),
          Binding("t", "toggle_min_priority", "Toggle priority filter"),
      ]
  ```
  Action methods (`action_request_quit`, etc.) post on `self.app._command_bus` after optional `await self._confirm(...)` modal.

- [ ] **Step 9.8.3: Verify** + **commit** `feat(M9): Monitor command bus keybindings (q/ctrl+q/s/r/p/t)`.

### Task 9.9: View command (replay completed run)

**Files:**
- Create: `senex/tui/view.py`
- Create: `tests/tui/test_view_replay.py`
- Create: `tests/fixtures/events/recorded_run.jsonl`
- Create: `tests/fixtures/events/recorded_run_truncated.jsonl` (no `RunComplete`)

**Spec anchors:** §5.6 (event-line schema for replay parsing); §5.7 (Monitor reuse).

- [ ] **Step 9.9.1: Failing tests.**
  - `test_view_replays_events_through_monitor` — call `await run_view(audit_dir=fixture_dir, speed=1000.0)`; capture final widget state; assert it matches the expected post-run state.
  - `test_view_warns_on_truncated_run_no_run_complete` — point at `recorded_run_truncated.jsonl`; assert error banner shows `"audit was interrupted"`.
  - `test_view_speed_flag_accelerates_replay` — at `speed=10.0`, total replay duration ≤ original / 10 (within tolerance).
  - `test_view_default_speed_is_1x` — without `speed` arg, replay roughly matches original wall-clock spacing.
  - `test_view_invalid_jsonl_line_raises_named_exception` — corrupt one line; assert `ReplayError` (NOT a generic `JSONDecodeError`) is raised.
  - `test_view_offline_no_lms_call` — patch `client.list_loaded_models` to raise; assert `run_view` completes successfully (no LMS dependency).
  - `test_view_disables_skip_and_rerun_in_replay_mode` — load Monitor in replay mode; press `s`; assert NO Command posted (replay mode mutes mutating commands per ARCH-16).

- [ ] **Step 9.9.2: Implement.**
  ```python
  async def run_view(audit_dir: Path, speed: float = 1.0) -> None:
      events_path = audit_dir / "events.jsonl"
      if not events_path.exists():
          raise ReplayError(f"no events.jsonl in {audit_dir}")
      events = list(_parse_events_jsonl(events_path))  # bounded read; iterator validates each line
      has_complete = any(isinstance(e, RunComplete) for e in events)
      app = SenexApp.in_replay_mode(audit_dir=audit_dir, truncated=not has_complete)
      async with app.run_test() as pilot:
          await pilot.pause()
          await _replay(events, app, speed)
  ```
  - `_parse_events_jsonl` validates each line via the discriminated `BaseEvent` union; on invalid JSON or unknown discriminator, raises `ReplayError` (named exception in `senex/tui/view.py`).
  - Replay loop awaits `asyncio.sleep((next.ts - prev.ts).total_seconds() / speed)` between events, capped at 1.0s for sanity.
  - Monitor screen receives a `replay_mode=True` flag that disables `s`/`r` keybindings.

- [ ] **Step 9.9.3: Verify** + **commit** `feat(M9): senex view replay handler with controllable speed`.

## Acceptance criteria

- `pytest tests/unit/test_subscribers_*.py tests/tui/ -v` is 100% green.
- Coverage on `senex/subscribers/*` and `senex/tui/*` ≥ 85% (`pytest --cov`).
- `mypy --strict senex/subscribers/ senex/tui/` is clean (zero errors).
- `ruff check senex/subscribers/ senex/tui/ tests/unit/test_subscribers_*.py tests/tui/` is clean.
- `gitnexus_detect_changes({scope: "all"})` after each task confirms changes are scoped to expected files.
- DiskWriter test asserts that filling the bounded queue causes the publisher to block until a slot opens.
- TuiSubscriber test asserts that under backpressure (queue full of ticks), only `*Started`/`*Complete` boundary events are processed; `*Tick` events are dropped.
- HeadlessSubscriber smoke test asserts a `RunComplete` event prints a final summary line to stdout.
- Textual Pilot smoke test: app starts → Launcher renders all required fields → clicking Start transitions to Monitor → all 5 widgets render → no exception propagates from `run_test()`.
- Command bus tests assert: `p` posts Pause; `p` again posts Resume; `q` posts Quit only after dialog confirm; `ctrl+q` cancels the audit task and posts Quit immediately; replay mode disables `s` and `r`.
- `senex view <fixture-audit-dir>` replays all events through Monitor and shows the final state correctly; truncated run (no `RunComplete`) shows a warning banner.
- Findings panel test asserts `deque(maxlen=30)` — 31st finding evicts the first.
- TUI render throttle test: 100 Tick events in 100ms produce ≤ 10 widget update calls.
- All named exceptions (`SubscriberQueueFull`, `SubscriberShutdownError`, `WidgetRenderError`, `ReplayError`) live at module top-level and are tested for their raise conditions.
- No bare `except` in any M9 file; no `contextlib.suppress(Exception)`; all tasks await `gitnexus_impact` before edits to existing symbols and `gitnexus_detect_changes` before commit.
