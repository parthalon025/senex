# senex TUI - Accessibility Audit (Read-Only)

**Auditor**: accessibility-tester (senior, WCAG 2.1/3.0)
**Scope**: E:/senex/senex/tui/ + adjacent CLI surface (senex/cli.py, senex/cli_audit.py, senex/cli_wizard.py, senex/subscribers/headless_subscriber.py)
**Date**: 2026-04-29
**Standards reference**: WCAG 2.1 (mapped to TUI semantics - Textual rendering layer), Section 508, EN 301 549
**Tools**: source review only - no live ATs run (terminal screen readers: NVDA-with-terminal, JAWS, Orca, macOS VoiceOver).

> Important caveat. WCAG was authored for the visual web; mapping it onto a terminal UI requires interpretation. Textual itself emits no real ARIA - terminal screen readers consume the raw cells the emulator renders. The findings below treat each WCAG success criterion at the spirit level (e.g., color must not be the only signal, every interactive element must be keyboard-reachable) and note where the canonical WCAG cell-mapping is informal.

---

## Executive summary

| Severity | Count | Examples |
|---|---|---|
| Must-fix (blocking publishability) | 4 | F-1, F-9, F-10, F-13 |
| Should-fix | 7 | F-2, F-3, F-5, F-6, F-7, F-8, F-12 |
| Nice-to-have | 5 | F-4, F-11, N-1, N-2, N-3 |

Headline: the TUI is broadly accessible by terminal-UI standards - keybindings are documented in the Textual footer, the FindingsPanel uses a text marker ([HIGH]/[MEDIUM]/[LOW]) so color is not the sole channel, and tick events are correctly debounced. Four issues block publishability:

1. Banner says press e to dismiss but no e keybinding actually exists. (F-1)
2. senex audit --no-tui --json advertises JSON output the headless subscriber never emits. (F-13)
3. No top-level help/keybindings screen in the Monitor. Users with cognitive load issues (and new users) have no in-app discoverability beyond the Textual footer. (F-9)
4. Modal ConfirmDialog has no explicit focus restoration on dismiss. Textual handles most cases by default, but the contract is not asserted, and the underlying Monitor has no focusable widgets to land on. (F-10)

---

## 1. Keyboard navigation

### F-1 - Banner advertises a keybinding (e) that does not exist
- WCAG: 3.3.2 Labels or Instructions (Level A); 3.2.4 Consistent Identification (Level AA)
- Priority: must
- File:line: senex/tui/widgets/error_banner.py:9-10 (docstring), :123 (rendered hint)
- Detail: The banner renders the literal text "press e to dismiss" and the module docstring claims "dismissed (key e on the host)". MonitorScreen.BINDINGS (monitor.py:80-86) only registers q, s, r, p, t. There is no e binding anywhere in the TUI tree, no action_dismiss_banner, and ErrorBannerWidget.dismiss() is only defined - never called by any keypress handler.
- Impact: A keyboard-only user reads the prompt, presses e, nothing happens. A screen-reader user is told the same lie. The banner remains sticky forever (until the next error replaces the message - but the message keeps lying).
- Fix path (out of scope): add Binding("e", "dismiss_banner", "Dismiss banner") to MonitorScreen.BINDINGS plus an action_dismiss_banner that queries #error_banner and calls .dismiss().

### F-2 - Pause/Resume label drift
- WCAG: 4.1.2 Name, Role, Value (Level A)
- Priority: should
- File:line: monitor.py:84
- Detail: The footer label is static "Pause/Resume" regardless of current state. A screen reader announcing the keybinding footer cannot tell the user whether pressing p will pause or resume. A user with cognitive load reading the footer mid-stream loses state.
- Fix path: dynamic binding label tied to self._paused; or render a small status pill in the StatusStrip showing PAUSED.

### F-3 - t (toggle priority) is local-only, never reflected in UI
- WCAG: 3.3.1 / 4.1.3 Status Messages (Level AA)
- Priority: should
- File:line: monitor.py:221-222
- Detail: action_toggle_min_priority flips self._min_priority_display but no widget reads it. The user gets zero feedback that their keypress did anything. A blind user gets no announcement; a sighted user gets no visual change. Violates perceivable feedback for user action.
- Fix path: have FindingsPanelWidget consult the flag and re-render filtered rows; emit a one-line announcement on the StatusStrip.

### F-4 - Tab order on LauncherScreen relies on Textual default DOM order
- WCAG: 2.4.3 Focus Order (Level A)
- Priority: nice (advisory; no defect)
- File:line: launcher.py:64-112 (compose)
- Detail: Compose order is logical (repo path -> repo select -> scan -> lens -> model -> numerics -> switches -> buttons). Textual visits widgets in DOM order, so Tab order matches reading order. The Horizontal containers at :79, :102, :108 group multiple controls; Textual traverses left-to-right, which is correct here. The #scan_btn followed by an inert #scan_status Static is a non-issue only because Static is non-focusable. Audit note: do not introduce focusable widgets into status displays.

### F-5 - No skip-link / jump-to-recent within long FindingsPanel
- WCAG: 2.4.1 Bypass Blocks (Level A - relaxed for terminals)
- Priority: should
- File:line: senex/tui/widgets/findings_panel.py:53-54
- Detail: The findings panel concatenates up to 30 rows into a single Static. A screen-reader user has no way to skip to the most recent (last) row - the AT will read top-to-bottom. The deque intent is newest at bottom; there is no newest-first toggle.
- Fix path: keybinding f for jump to last finding; or render newest-first in reverse-chronological order.

### Mouse-only paths
None observed. Every interactive element has a Textual Binding or is reachable via Tab.

---

## 2. Screen-reader friendliness

### F-6 - Static used where Label would announce better
- WCAG: 1.3.1 Info and Relationships (Level A); 4.1.2 Name, Role, Value (Level A)
- Priority: should
- Files:lines:
  - launcher.py:66 - Static("senex - Launcher", id="title") (page title - should be heading-equivalent)
  - launcher.py:81 - Static("", id="scan_status") (status - should be Label with live-region behavior or Textual RichLog)
  - launcher.py:107 - Static("", id="resume_info")
  - launcher.py:112 - Static("", id="error_label") (this is critical - error messages must be live-announced)
  - widgets/findings_panel.py:53-54 - Static("Recent findings (0):", id="findings_header") + Static("", id="findings_body")
  - monitor.py:55 - Static(self._prompt, id="confirm_prompt") inside the modal
- Detail: Textual Label is the announceable widget; Static is a render-only blob. Terminal screen readers depend on the emulator + Textual accessibility hooks (currently informal). Treating a status string as a Static means update-mutations may be missed by live-region polling. The error-label case (launcher.py:112) is the most painful: a blind user submits the form, validation fails, the message updates - and no announcement fires.
- Fix path: convert all status/error/heading widgets to Label. Where dynamic announcements are needed (errors, scan results), Textual notify() is the canonical screen-reader-friendly path.

### F-7 - ErrorBanner uses Widget + nested Label (good) but with display: none (announcement risk)
- WCAG: 4.1.3 Status Messages (Level AA)
- Priority: should
- File:line: senex/tui/widgets/error_banner.py:38-41
- Detail: The CSS display: none is the right reset, but Textual compose may not (re)mount the inner Label until display = True. Some terminal screen readers ignore late-mounted live regions. Consider keeping the widget mounted (visibility: hidden semantics) so AT can latch onto it before the first error.
- Fix path: visibility: hidden instead of display: none; or always mount with empty text and toggle display.

### F-8 - No accessible name on Switch widgets
- WCAG: 1.3.1 / 4.1.2 (Level A)
- Priority: should
- File:line: launcher.py:103-106
- Detail: Switch(value=False, id="include_tests") and Switch(value=True, id="save_traces") have adjacent Label siblings but no programmatic association (Textual has no for=/htmlFor equivalent). Some AT will announce switch on/off with no semantic linkage to the visual label Include tests.
- Fix path: Textual Switch accepts a name; or wrap both in a Container with a tooltip / name attribute that reads the label.

---

## 3. Color contrast

### Compliant: priority text-marker channel (PASS)
- WCAG: 1.4.1 Use of Color (Level A)
- File:line: senex/tui/widgets/findings_panel.py:31-34
- Detail: FindingRow.render_line() emits [HIGH]    path (loc) - title etc. The bracketed marker is the primary signal; CSS classes .priority-high { color: red } etc. are defined at :42-45 but never assigned to elements (the body is a single concatenated Static). Color is decorative-only. No code change required. WCAG 1.4.1 PASS.

### N-1 - StatusStrip rollup has no color distinction at all (informational)
- Priority: nice
- File:line: senex/tui/widgets/status.py:34-44
- Detail: HIGH 5 | MEDIUM 3 | LOW 12 | HEALTHY 22 | TOOLS 87 | COMPACTIONS 0 is rendered as plain text. Fine for accessibility (no color to fail on). For sighted users a small color accent on HIGH would aid scanning, but adding it would mandate paired text markers - which already exist as the words HIGH/MEDIUM/LOW.

### N-2 - LauncherScreen #error_label color-only
- WCAG: 1.4.1 Use of Color (Level A)
- Priority: should-but-low
- File:line: senex/tui/launcher.py:52
- Detail: Errors are red text only. A user with red/green color-blindness on a high-contrast terminal where $error resolves to a hard-to-distinguish hue may miss the signal. The text content itself is informative (repo path does not exist:), so the message conveys meaning regardless - but the visual treatment alone would not.
- Fix path: prefix with [ERROR] / ! so the marker is text-channel.

---

## 4. Reduced-motion / tick policy

### Compliant (PASS)
- WCAG: 2.3.3 Animation from Interactions (Level AAA - aspirational)
- File:line: widgets/status.py:46-48, subscribers/headless_subscriber.py:72-73, events.py:16 (COALESCE_SAFE_TYPES = ThinkingTick, OutputTick)
- Detail: StatusStrip explicitly skips ThinkingTick and OutputTick events (status.py:47). HeadlessSubscriber does the same. The bus-level coalescing (COALESCE_SAFE_TYPES) is the canonical drop-oldest policy from spec section 5.6.1. CurrentFileWidget DOES update per-tick (current_file.py:80-91) - the tokens-so-far counters tick incrementally. This is per-spec (counters reset on FileStart) but a vestibular-sensitive user watching a counter spin from 0 to 8000 in 30 seconds will see motion.
- Verdict: spec-compliant; no must-fix. Consider adding a --reduced-motion CLI flag that throttles CurrentFile to 1 Hz updates (nice-to-have N-3).

### N-3 - Per-tick token counter motion
- Priority: nice
- File:line: senex/tui/widgets/current_file.py:80-91

---

## 5. Focus management (modal dialog)

### F-10 - ConfirmDialog does not assert focus restoration; Monitor has no focusable host
- WCAG: 2.4.3 Focus Order (Level A); 2.4.7 Focus Visible (Level AA)
- Priority: must (because the entire workflow lives in the modal)
- File:line: senex/tui/monitor.py:41-62 (dialog), monitor.py:112-117 (Monitor compose - all five widgets are non-interactive)
- Detail: ConfirmDialog(ModalScreen) calls self.dismiss(True/False). Textual ModalScreen.dismiss typically returns focus to the previously focused widget on the underlying screen - but this contract is implicit, not asserted. The Monitor screen has zero focusable widgets (all five widgets are read-only Labels/Static blobs), so on dismiss focus has nowhere to land. Result: Tab from nothing-focused cycles through nothing, and q/s/r/p/t keybindings keep working only because they are screen-level, not focus-dependent. Acceptable in practice but not asserted in tests - a future Textual upgrade could break this silently.
- Fix path: add a focusable invisible monitor pane that holds focus while the modal is closed; add a Pilot test that asserts app.focused is non-None after dismiss.

### Compliant: Escape always dismisses (PASS)
- File:line: monitor.py:46
- Detail: Binding("escape", "cancel", "Cancel") is registered on the modal. There is no override or trap - escape will always close the dialog with False. WCAG 2.1.2 No Keyboard Trap PASS.

---

## 6. Text resize / zoom

### F-11 - Hardcoded width: 80% on launcher form
- WCAG: 1.4.10 Reflow (Level AA)
- Priority: nice
- File:line: senex/tui/launcher.py:51
- Detail: LauncherScreen #form { width: 80%; height: auto; ... } - at very narrow terminals (<60 cols), 80% may not leave room for label text. Textual generally re-flows, but the inputs (Select, Input) have implicit minimum widths. Zoom in a terminal context = font-size increase reducing column count; very high zoom collapses the form.
- Fix path: add min-width: 40 and max-width: 100 (cell units), or switch to width: auto.

### Compliant: per-widget heights are 1-cell or auto (PASS)
- All widgets except CurrentFileWidget (height: 5) and FindingsPanel (min-height: 6) use height: 1 or height: auto. No fixed pixel widths anywhere.

---

## 7. Documentation / discoverability

### F-9 - No in-app help screen; senex audit --help does not list TUI keybindings
- WCAG: 3.3.5 Help (Level AAA - but should-have for cognitive accessibility)
- Priority: must (cognitive accessibility regression)
- File:line:
  - monitor.py:80-86 - bindings registered with footer labels (so Textual footer does show them)
  - cli.py:128-191 - audit parser; no --help text mentions q/s/r/p/t
  - No ? or F1 help binding anywhere in MonitorScreen
- Detail: Textual default footer does render the binding labels - that is the primary discoverability path and it works. However:
  1. The e (dismiss banner) misadvertisement (F-1) means the footer + banner instructions disagree.
  2. senex audit --help says nothing about the TUI keys; a user reading help before launching has no idea about s/r until they are in.
  3. There is no expanded help screen for users who want detail (what does r rerun do? what is min-priority?).
- Fix path: add Binding("?", "show_help", "Help") opening a HelpScreen(ModalScreen) with the full table; mirror the table in senex audit --help epilog.

---

## 8. Internationalization / encoding

### F-12 - TUI Labels contain em-dash and arrows; cli_wizard fallback is fixed but TUI is not
- WCAG: 3.1.1 (Level A - degraded mapping); 4.1 robustness
- Priority: should
- Detail: cli_wizard.py:118-132 implements _print with _ASCII_FALLBACKS (arrow -> ->, em-dash -> --). The TUI bypasses _print and writes directly through Textual:
  - launcher.py:66 - Static(senex - Launcher, ...) (em-dash literal in source)
  - launcher.py:306 - Inference server unavailable - cannot start audit (em-dash in source)
  - widgets/error_banner.py:91, 105, 123 - em-dashes in dynamic banner text
  - widgets/findings_panel.py:34 - f"... - {self.title}" (em-dash in source)
  - widgets/progress.py:48, 73, 78 - em-dashes
  - view.py:35 - multiplication sign in a comment (not user-visible)
- Risk: Textual writes UTF-8 to stdout; on Windows cp1252-default consoles without Textual alternate-screen, this would fail. In practice Textual forces UTF-8 mode, so the risk is negligible at runtime. Risk surfaces in:
  - cmd.exe with chcp 437 (rare; defense field laptops do it)
  - Logging/error scrapers that capture app.run() output via subprocess
  - Snapshot tests that compare raw bytes
- Verdict: low real-world risk; should-fix for defense-in-depth. The cli_wizard precedent shows the team has the pattern - apply it to TUI text too.

---

## 9. Keyboard-only input flow (wizard, stdin)

### Compliant: wizard handles Ctrl+D / Ctrl+C / piped stdin (PASS)
- WCAG: 2.1.1 Keyboard (Level A); 2.1.2 No Keyboard Trap (Level A)
- File:line: senex/cli_wizard.py:87-103 (_read_line)
- Detail:
  - KeyboardInterrupt (Ctrl+C) -> WizardCancelled -> exit 130 (PASS)
  - EOF (line == empty string) -> WizardCancelled -> exit 130 (PASS) - Ctrl+D on Unix; Ctrl+Z on Windows; closed pipe
  - pipe < script.txt -> reads each line until EOF; bounded retry loops (for _ in range(8)) prevent infinite re-prompt on malformed scripts (PASS)
  - q | quit | exit strings -> cancellation at every prompt (PASS)
- Verdict: PASS. This is the strongest accessibility area in the codebase.

### Advisory - Bounded retry of 8 may surprise scripted users
- Priority: nice
- File:line: every wizard for _ in range(8) loop
- Detail: A scripted setup that pipes 9 wrong inputs gets WizardCancelled(...exhausted retries) rather than the expected next prompt. Documented in module docstring; not a defect.

---

## 10. Output redirection / --no-tui --json

### F-13 - senex audit --no-tui --json advertises but does not deliver JSON output
- WCAG: 3.3.2 Labels or Instructions (Level A); 3.2.4 Consistent Identification (Level AA)
- Priority: must
- File:line:
  - cli.py:82-87 - --json flag registered globally on the parent parser, with help text: Emit machine-readable JSON output (where supported).
  - cli.py:128-191 - audit subcommand inherits --json (via parents=[parent])
  - cli_audit.py:76-129 - cmd_audit never reads args.as_json
  - cli_audit.py:322-388 - _run_headless builds HeadlessSubscriber() unconditionally (no JSON variant)
  - subscribers/headless_subscriber.py:79-125 - _format_event returns human-readable strings only; line 89 produces [{idx}/{total}] auditing {path} - {n} findings (high=, medium=, low=)
- Detail: senex audit --no-tui --json | jq will:
  1. argparse accepts --json (sets args.as_json = True).
  2. cmd_audit ignores it.
  3. HeadlessSubscriber writes human text with em-dashes and (...) to stdout.
  4. jq fails on the first non-JSON line (--- senex audit: ...).
- Impact: Publishability blocker for any CI/scripting integration. The where supported caveat in help text is buried; the global flag invites misuse. The where supported is doctor, config show, lifecycle status - explicitly NOT audit.
- Fix paths (any one acceptable):
  1. Strip --json from the audit subcommand parents (most honest).
  2. Implement JSONLinesHeadlessSubscriber that emits one JSON-Lines event per line; gate on args.as_json in cli_audit._run_headless.
  3. Document the limitation prominently in senex audit --help epilog.
- Recommendation: option 2 - events.jsonl already exists on disk; emitting it to stdout when --json is set is a 30-line subscriber.

---

## Cross-cutting non-defect observations

### O-1 - Replay mode (senex view) correctly mutes destructive bindings (PASS)
- monitor.py:181-212: _replay_mode short-circuits s (Skip), r (Rerun), and converts q from confirm-dialog to instant-exit. This prevents a screen-reader user replaying a run from accidentally posting a Skip command into a dead command-bus.
- WCAG 3.3.4 Error Prevention (Level AA) - PASS.

### O-2 - Widget render errors converted to user-visible banner, never propagated (PASS)
- monitor.py:155-160: every widget call is wrapped in try/except that re-raises as WidgetRenderError and surfaces via the banner. A blind user gets a textual error, not a crash. WCAG 3.3.1 Error Identification - PASS.

### O-3 - ctrl+q priority binding ensures escape hatch is always available (PASS)
- app.py:58-60: Binding("ctrl+q", "force_quit", priority=True). Even if every other binding broke, ctrl+q exits cleanly with 130. WCAG 2.1.2 No Keyboard Trap - PASS.

---

## Findings table (compact)

| # | WCAG | Level | Priority | File:line | Description |
|---|---|---|---|---|---|
| F-1 | 3.3.2 | A | must | widgets/error_banner.py:9, 123 | Banner advertises e to dismiss; no e binding exists |
| F-2 | 4.1.2 | A | should | monitor.py:84 | Pause/Resume label is static |
| F-3 | 4.1.3 | AA | should | monitor.py:221-222 | t toggle has no UI feedback |
| F-4 | 2.4.3 | A | nice | launcher.py:64-112 | Tab order acceptable; advisory only |
| F-5 | 2.4.1 | A | should | widgets/findings_panel.py:53-54 | No skip-to-recent in findings |
| F-6 | 1.3.1, 4.1.2 | A | should | multiple (see section 2) | Static used where Label would announce |
| F-7 | 4.1.3 | AA | should | widgets/error_banner.py:38-41 | display: none may break live-region latch |
| F-8 | 1.3.1, 4.1.2 | A | should | launcher.py:103-106 | Switches lack programmatic name binding |
| F-9 | 3.3.5 | AAA | must | cli.py:128-191; no help screen | No in-app help; audit --help omits TUI keys |
| F-10 | 2.4.3 | A | must | monitor.py:41-62 + Monitor compose | Modal focus restoration not asserted; no focusable host |
| F-11 | 1.4.10 | AA | nice | launcher.py:51 | Hardcoded width: 80% |
| F-12 | 3.1.1 | A | should | TUI Labels (multiple) | em-dash/arrow not ASCII-safe in TUI text |
| F-13 | 3.3.2, 3.2.4 | A/AA | must | cli.py:82-87; cli_audit.py:322-388; subscribers/headless_subscriber.py | audit --json advertised but not implemented |
| N-1 | - | - | nice | widgets/status.py:34-44 | StatusStrip color accent for HIGH would aid scanning |
| N-2 | 1.4.1 | A | nice | launcher.py:52 | Error label color-only; add [ERROR] text marker |
| N-3 | 2.3.3 | AAA | nice | widgets/current_file.py:80-91 | Per-tick token counter motion (no --reduced-motion) |

Compliant (passes verified):

| Area | Files |
|---|---|
| Color not sole channel for findings priority | widgets/findings_panel.py:31-34 ([HIGH] text marker) |
| Tick coalescing (StatusStrip + Headless) | widgets/status.py:46-48, subscribers/headless_subscriber.py:72-73, events.py:16 |
| Escape always closes modal | monitor.py:46 |
| Ctrl+C / Ctrl+D / piped stdin in wizard | cli_wizard.py:87-103 |
| Replay mode mutes destructive keys | monitor.py:181-212 |
| Widget errors caught, surfaced not propagated | monitor.py:155-160 |
| ctrl+q priority escape hatch | app.py:58-60 |
| Wizard ASCII fallback for cp1252 terminals | cli_wizard.py:105-132 |

---

## Publishability gate

| Gate | Status |
|---|---|
| Zero must-fix at WCAG Level A | FAIL - F-1, F-9, F-10, F-13 |
| Zero color-only signals | PASS |
| Keyboard reach to every action | PARTIAL - F-1 (advertised key absent) |
| Tick / motion compliance | PASS |
| Documentation parity (help vs behavior) | FAIL - F-9, F-13 |

Verdict: NOT PUBLISHABLE as-is. Fix F-1, F-9, F-10, F-13 to clear the gate. The other findings can ship as a follow-up M12 accessibility milestone.

---

## Suggested remediation order (out of scope - for planning)

1. F-13 (audit --json mismatch) - strip the flag from the audit parser OR implement JSONLinesHeadlessSubscriber. ~1-day fix.
2. F-1 (banner-advertised e key) - add the binding + action. ~30-minute fix.
3. F-9 (in-app help screen + --help epilog) - small HelpScreen(ModalScreen), plus a 10-line epilog. ~half-day.
4. F-10 (modal focus restoration) - introduce a focusable Monitor pane + Pilot test asserting focus state pre/post modal. ~half-day.
5. F-2, F-3, F-5, F-6, F-7, F-8, F-12 - batched milestone. ~2-3 days.

---

Report saved to E:/senex/docs/publishability/accessibility-report.md
