# Milestone 9: Subscribers + TUI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M9 delivers the user-facing layer: three subscriber implementations (DiskWriter, Headless, Metrics) and the Textual TUI app (Launcher screen + Monitor screen + 5 widgets + TuiSubscriber + command-bus keybindings). The TUI is a thin consumer of the bus — every line on screen is driven by an event the auditor already publishes. The `senex view` command replays a completed run's `events.jsonl` through the same Monitor screen for post-hoc inspection.

**Architectural intent:** Subscribers and TUI are *passive consumers* of the event stream. The auditor coroutine doesn't know they exist; it just publishes. This decoupling means the TUI can crash without taking the audit down (DiskWriter's write-ahead persistence is the durable record). The TuiSubscriber drops `*Tick` events under backpressure so a slow render loop never blocks the auditor.

## Prerequisites

- **Completed milestones:** M1 Foundation, M2 Walker + Graph Awareness, M3 LM Studio Integration, M4 Lifecycle, M5 Tool Framework, M6 Compaction, M7 Renderer + Aggregator, M8 Phases + Auditor
- **Required modules from prior work:**
  - `senex/events.py` — `EventBus`, `CommandBus`, all event types (M1)
  - `senex/auditor.py` — `run_audit()` (launched as Textual worker by Launcher) (M8)
  - `senex/lmstudio_client.py` — `client.list_loaded_models()` for Launcher's model dropdown (M3)
  - `senex/config.py` — `SenexConfig` for Launcher form defaults (M1)
  - `senex/lens.py` — for Launcher's lens selector (correctness only in v1) (M1)
- **Required tools/state:**
  - `textual` installed (already in pyproject from M1)
  - `pytest-asyncio` + Textual's `Pilot` for smoke tests
  - An existing audit dir (from M8 e2e tests) for `senex view` replay tests

## Deliverable

This milestone creates the following files:

- `senex/subscribers/__init__.py`
- `senex/subscribers/base.py` — `Subscriber` protocol
- `senex/subscribers/disk_writer.py` — `DiskWriterSubscriber` (write-ahead persistence to `events.jsonl`)
- `senex/subscribers/headless_subscriber.py` — stdout progress lines
- `senex/subscribers/metrics.py` — running totals collector
- `senex/subscribers/tui_subscriber.py` — TUI updater with rate-limited renders
- `senex/tui/__init__.py`
- `senex/tui/app.py` — `SenexApp(App)` with two screens
- `senex/tui/launcher.py` — Launcher screen (config form)
- `senex/tui/monitor.py` — Monitor screen (live progress)
- `senex/tui/widgets/progress.py`, `current_file.py`, `findings_panel.py`, `error_banner.py`, `status.py`
- Command bus keybindings on Monitor screen: `q`, `Ctrl+Q`, `s`, `r`, `p`, `t`
- `senex view <audit-dir>` handler — replays `events.jsonl` through Monitor screen
- Tests: subscriber unit tests + Textual `Pilot` smoke tests

## Downstream consumers

- **M10** CLI `senex audit` selects between TuiSubscriber (default) and HeadlessSubscriber (`--no-tui` flag).
- **M10** CLI `senex view` invokes the M9 view replay handler.
- **M10** Live gate 13b verifies the TUI renders launcher + monitor + completion screen end-to-end.

## Spec sections referenced

- §5.6.1 Bus semantics — per-subscriber backpressure rules (DiskWriter blocks, TUI drops Tick, Metrics drops oldest Tick)
- §5.6.2 Command bus — `q`/`Ctrl+Q`/`s`/`r`/`p`/`t` keybindings + Pause/Resume/Skip/Rerun/Quit semantics
- §5.7 TUI — Launcher fields, Monitor widgets, refresh rate (~10 Hz), findings deque size (30)

## Key contracts

- **`Subscriber` protocol** — `name: str`, `capacity: int`, `async run(queue, ...)`.
- **`DiskWriterSubscriber`** — write-ahead persistence; uses bounded queue; publisher BLOCKS if full (§5.6.1).
- **`HeadlessSubscriber`** — `[12/87] auditing src/foo.py — 2 findings (1 high, 1 medium)`-style stdout; final summary at `RunComplete`.
- **`MetricsCollectorSubscriber`** — running totals (files, findings by priority, tool calls, compactions, durations).
- **`TuiSubscriber`** — drops `*Tick` events under backpressure; updates widget state on each event; throttled to ~10 Hz.
- **`SenexApp(App)`** — two screens (Launcher, Monitor); `run_audit()` launched as worker task on launcher's "Start" button click.
- **Launcher widgets** — repo dropdown, lens selector, model dropdown (from `client.list_loaded_models()`), sampling fields (temperature, max_tokens, seed), include-tests toggle, resume detector, reset/start buttons.
- **Monitor widgets** — `progress.py` (file N/M progress bar), `current_file.py` (which file is auditing), `findings_panel.py` (deque(maxlen=30) of recent findings), `error_banner.py` (warning surface), `status.py` (model + lens + tools header).
- **`senex view <audit-dir>`** — opens Monitor; replays `events.jsonl` deterministically; warns if no `RunComplete` (truncated).

## Watch-outs

- **DiskWriter MUST block the publisher when full.** This is the asymmetric backpressure rule — `events.jsonl` is the durable record; losing events corrupts crash recovery. Every other subscriber drops; DiskWriter blocks.
- **TuiSubscriber drops `*Tick` events under backpressure**, never `*Started`/`*Complete`. Boundary events drive state transitions; ticks just animate counters. Drop ticks freely.
- **Launcher's model dropdown calls `client.list_loaded_models()`.** This requires LM Studio to be reachable when the Launcher renders — handle the unreachable case with a "[no LM Studio]" placeholder + greyed-out Start button.
- **Command bus polling cadence is decided by the auditor, not the TUI.** Pressing `p` posts a `Pause` command immediately, but the auditor only reads it at the next iteration boundary (between files / between phases). Tell the user this lag is normal.
- **`senex view` replay is offline.** It does NOT call LMS, does NOT need a model loaded. It only consumes `events.jsonl`. Truncated events (no `RunComplete`) surface a warning banner.
- **TUI updates throttled to ~10 Hz.** Otherwise high-thinking-token-rate models flood the render loop. Coalesce ticks in the subscriber, not in widgets.
- **Tick events drive thinking-token counter.** A widget that reads from `MetricsCollectorSubscriber` directly bypasses the TUI's update cadence — wire ticks through `TuiSubscriber` so the throttle applies.

## Patterns to follow

- **Textual Pilot smoke tests** (Task 9.4.2): `async with app.run_test() as pilot: await pilot.click("#start"); await pilot.pause()` to drive the TUI and assert state.
- **Subscriber TDD** — write a failing test that constructs a subscriber, feeds a sequence of events, asserts the post-state. Each subscriber is small and pure.
- **Widget separation:** each of the 5 monitor widgets is a separate file with its own test. Widgets are stateful; their state is mutated by the subscriber.
- **Replay determinism:** `senex view` reads `events.jsonl` and replays in order with no synthetic delays — useful for both manual inspection and CI smoke tests.

## Tasks

### Task 9.1: Subscriber protocol + DiskWriterSubscriber

**Files:**
- Create: `senex/subscribers/base.py`
- Create: `senex/subscribers/disk_writer.py`

- [ ] **Step 9.1.1: Failing test** — DiskWriter receives events, writes to `events.jsonl` in seq order; final `RunComplete` fsync-flushed before subscriber exits.

- [ ] **Step 9.1.2: Implement `Subscriber` protocol** + `DiskWriterSubscriber`. Uses bounded queue (publisher blocks if full per spec §5.6.1).

- [ ] **Step 9.1.3: Commit** `feat(M9): DiskWriterSubscriber with write-ahead persistence`.

### Task 9.2: HeadlessSubscriber

- [ ] **Step 9.2.1: Implement** — prints concise progress lines to stdout: `[12/87] auditing src/foo.py — 2 findings (1 high, 1 medium)`; final summary at RunComplete.

- [ ] **Step 9.2.2: Test + commit** `feat(M9): HeadlessSubscriber for --no-tui mode`.

### Task 9.3: MetricsCollectorSubscriber

- [ ] **Step 9.3.1: Implement** — running totals (files, findings by priority, tool calls, compactions, durations).

- [ ] **Step 9.3.2: Test + commit** `feat(M9): metrics collector`.

### Task 9.4: Textual app skeleton

**Files:**
- Create: `senex/tui/app.py`
- Create: `senex/tui/launcher.py`
- Create: `senex/tui/monitor.py`

- [ ] **Step 9.4.1: Implement `SenexApp(App)`** with two screens (Launcher, Monitor); `run_audit()` launched as worker task on launcher's "Start" button click.

- [ ] **Step 9.4.2: Smoke test** with Textual Pilot — app starts, launcher renders, "Start" transitions to monitor without crash.

- [ ] **Step 9.4.3: Commit** `feat(M9): Textual app skeleton`.

### Task 9.5: Launcher screen

- [ ] **Step 9.5.1: Implement Launcher widgets**: repo dropdown (config entries + manual input), lens selector (correctness only for v1), model dropdown (populated from `client.list_loaded_models()`), sampling fields (temperature, max_tokens, seed), include-tests toggle, resume detector, "Reset to defaults" button, "Start audit" button.

- [ ] **Step 9.5.2: Smoke test** widget rendering + form submission.

- [ ] **Step 9.5.3: Commit** `feat(M9): launcher screen`.

### Task 9.6: Monitor screen widgets

**Files:**
- Create: `senex/tui/widgets/progress.py`
- Create: `senex/tui/widgets/current_file.py`
- Create: `senex/tui/widgets/findings_panel.py`
- Create: `senex/tui/widgets/error_banner.py`
- Create: `senex/tui/widgets/status.py`

- [ ] **Step 9.6.1-9.6.5: Implement each widget** per spec §5.7. Findings panel uses deque(maxlen=30). Tick events drive thinking-token counter.

- [ ] **Step 9.6.6: Test each widget** with Pilot.

- [ ] **Step 9.6.7: Commit** `feat(M9): monitor widgets`.

### Task 9.7: TuiSubscriber

- [ ] **Step 9.7.1: Implement** — drops `*Tick` events under backpressure; updates widget state on each event; TUI updates throttled to ~10 Hz.

- [ ] **Step 9.7.2: Test + commit** `feat(M9): TuiSubscriber with rate-limited updates`.

### Task 9.8: Command bus wiring (pause/quit/skip/rerun)

- [ ] **Step 9.8.1: Implement keybindings** in monitor screen: `q` (confirm-quit), `Ctrl+Q` (no-confirm), `s` (skip with confirm), `r` (rerun with confirm), `p` (pause/resume), `t` (toggle min-confidence).

- [ ] **Step 9.8.2: Test command flow** — pressing `p` posts Command, auditor pauses at next iteration boundary.

- [ ] **Step 9.8.3: Commit** `feat(M9): TUI command bus integration`.

### Task 9.9: View command (replay completed run)

- [ ] **Step 9.9.1: Implement `senex view <audit-dir>`** — opens monitor screen; replays `events.jsonl` deterministically; warns if no `RunComplete` (truncated).

- [ ] **Step 9.9.2: Test + commit** `feat(M9): senex view replay`.

## Acceptance criteria

- `pytest tests/unit/test_subscribers_*.py tests/tui/ -v` is 100% green.
- DiskWriterSubscriber test asserts that filling the bounded queue causes the publisher to block (await is suspended) until a slot opens.
- TuiSubscriber test asserts that under backpressure (queue full of Ticks), only `*Started`/`*Complete` boundary events are processed; `*Tick` events are dropped.
- HeadlessSubscriber smoke test asserts a `RunComplete` event prints a final summary line to stdout.
- Textual Pilot smoke test: app starts → Launcher renders all 7 fields → clicking Start transitions to Monitor → all 5 widgets render → no crash.
- Command bus test: pressing `p` in Monitor posts a `Pause` command; mock auditor reads it at next boundary and suspends.
- `senex view <audit-dir>` against an existing audit dir (from M8 e2e test output) replays all events through Monitor and shows the final state correctly; truncated run (no `RunComplete`) shows a warning banner.
- Findings panel test asserts deque(maxlen=30) — 31st finding evicts the first.
- TUI render throttle test: 100 Tick events in 100ms produce ≤ 1 widget render call.
