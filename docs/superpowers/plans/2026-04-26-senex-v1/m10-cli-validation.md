# Milestone 10: CLI + Validation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M10 is the final mile: the `senex` CLI entry point, all subcommands (`audit`, `view`, `doctor`, `aggregate`, `config`, `lifecycle`), Windows scheduler scripts, README, the full live-validation gate suite (13a–13e against a real LM Studio + indexed repo), and the v1.0.0 release tag. After M10, senex is shippable.

**Architectural intent:** Layer 7 is the entry point. The CLI does *no* business logic — it parses args, builds the config + lens + bus + lifecycle + subscriber set, and calls into M8's `run_audit()` or other handlers. The live-validation gates exist because the entire stack has been tested with mocks/recorded fixtures up to this point; M10 is the first time we run against real LM Studio HTTP, real `npx gitnexus`, real Textual rendering. Gates 13a–13e are the proof.

## Prerequisites

- **Completed milestones:** ALL — M1 through M9.
- **Required modules from prior work:** Every module in `senex/`. The CLI is the composition root.
- **Required tools/state:**
  - LM Studio running with `google/gemma-4-26b-a4b` loaded (for live gates 13a/13b/13c/13d/13e)
  - `tests/fixtures/repos/tiny_python/` (from M2) indexed via `npx gitnexus analyze` (for live gates)
  - `npx gitnexus` available on PATH
  - `lms` CLI on PATH (or `lmstudio` Python SDK installed) for lifecycle backend
  - GitHub CLI (`gh`) authenticated for the v1.0.0 release step (Task 10.10.5)

## Deliverable

This milestone creates the following files:

- `senex/cli.py` — `main()` with argparse for all 6 subcommands
- `scripts/run_senex.bat` — Windows scheduled-task entrypoint
- `scripts/setup.ps1` — first-time setup (venv + deps + verify)
- `README.md` — full quickstart (replaces one-line placeholder)
- Refreshed `senex.config.toml.example` (final sync with `SenexConfig`)
- `docs/validation/2026-04-26-v1-validation.md` — gate-by-gate evidence
- `CHANGELOG.md` — v1.0.0 entry
- Bumped versions: `senex/__init__.py.__version__ = "1.0.0"`, `pyproject.toml.version = "1.0.0"`
- Git tag `v1.0.0` + GitHub release

## Downstream consumers

None — M10 is the leaf milestone. After M10 ships, downstream is real users.

## Spec sections referenced

- §10 CLI Surface — full subcommand specification (audit, view, doctor, aggregate, config, lifecycle) and common flags (`--config`, `--no-tui`, `--verbose`, `--quiet`, `--json`)
- §API-4 — argparse subcommand structure
- §8.1 Pre-flight — reused by `senex doctor`
- §13 Glossary — terms used in README
- Validation gates 13a–13e:
  - 13a: `senex doctor` + headless audit produces complete artifacts
  - 13b: TUI renders launcher + monitor + completion
  - 13c: Resume from mid-run kill produces clean continuation
  - 13d: Tool loop produces evidence (a finding quoting tool result)
  - 13e: Compaction smoke fires + completes

## Key contracts

- **`main()`** in `senex/cli.py` — argparse with 6 subcommands. Common flags: `--config`, `--no-tui`, `--verbose`, `--quiet`, `--json`.
- **`senex audit [repo]`** — composes config + lens + bus + (TuiSubscriber OR HeadlessSubscriber) + run_audit() + lifecycle. Flags: `--resume`, `--nightly` (iterates `config.repos`).
- **`senex view <audit-dir>`** — calls M9's view replay handler.
- **`senex doctor [repo]`** — runs all preflight checks; `--json` outputs structured.
- **`senex aggregate <audit-dir>`** — re-runs Phase 5 (AggregatePhase) against an existing audit dir.
- **`senex config show [repo]`** — resolves config + prints fully merged TOML.
- **`senex lifecycle status [--json]`** + **`senex lifecycle clear-locks [--force]`** — wired in M4 Task 4.4; CLI exposes them here.

## Watch-outs

- **Argparse mutual exclusion:** `--no-tui` and `--verbose` interact (verbose only meaningful in headless mode); `--json` only meaningful with subcommands that support it (doctor, lifecycle status, config show). Test these.
- **`--nightly` iterates `config.repos`** — a malformed entry should not abort the whole batch. Each repo is independent; failures are logged.
- **Live validation gates require real LM Studio.** They are NOT part of `pytest tests/ -v --ignore=tests/live`. They run manually (or in a separate CI lane) per Task 10.9 step list.
- **Gate 13c (resume) requires killing a process mid-run.** Simulate with a SIGTERM after N seconds, or via a forced exception inside FileAuditPhase. Restart with `--resume` and verify the completed files are skipped.
- **Gate 13e (compaction smoke) needs a synthetic-bloat env var.** Implement a `SENEX_FORCE_COMPACTION_AT_FILE=N` toggle (already used in M6 tests) so the gate can deterministically trigger compaction.
- **Coverage targets are non-uniform.** 85% on critical paths (auditor, renderer, walker, checkpoint, events, secret_redactor, findings_aggregator, phases/*, tools/*); 60% elsewhere. Don't waste time on TUI-widget coverage; do invest in auditor coverage.
- **README is shipped, not draft.** It's the first thing a new user reads. Quickstart must be runnable.
- **GitHub release uses `gh release create`.** This requires `gh auth login` to have happened. Authenticate before Task 10.10.5.

## Patterns to follow

- **Composition root pattern:** `cli.py` is *only* glue. It builds objects and wires them. No business logic.
- **Validation report (Task 10.9.8):** structured Markdown with one section per gate, evidence paths, latencies, redaction examples. This is the proof artifact for v1.0.0.
- **Atomic version bump + tag:** Task 10.10 happens in one commit chain. Don't bump version without tagging; don't tag without releasing.

## Tasks

### Task 10.1: CLI entry point + argparse

**Files:**
- Create: `senex/cli.py`

- [ ] **Step 10.1.1: Implement `main()` with argparse** — subcommands: `audit`, `view`, `doctor`, `aggregate`, `config`, `lifecycle`. Common flags: `--config`, `--no-tui`, `--verbose`, `--quiet`, `--json`. Per spec §10 + API-4.

- [ ] **Step 10.1.2: Test** that each subcommand parses correctly; `--help` shows expected text; mutually-exclusive flags fire correctly.

- [ ] **Step 10.1.3: Commit** `feat(M10): CLI entry point + argparse`.

### Task 10.2: senex audit (with + without TUI)

- [ ] **Step 10.2.1: Implement audit subcommand** — composes config + lens + bus + (TuiSubscriber OR HeadlessSubscriber) + run_audit() + lifecycle.

- [ ] **Step 10.2.2: `--resume` flag** wires to `run_audit(resume=True)`.

- [ ] **Step 10.2.3: `--nightly` flag** iterates `config.repos` list.

- [ ] **Step 10.2.4: Tests + commit** `feat(M10): senex audit subcommand`.

### Task 10.3: senex doctor

- [ ] **Step 10.3.1: Implement** — runs all preflight checks against either configured `audit.config.toml` or a target repo. `--json` outputs structured.

- [ ] **Step 10.3.2: Test + commit** `feat(M10): senex doctor`.

### Task 10.4: senex aggregate

- [ ] **Step 10.4.1: Implement** — re-runs Phase 5 against an existing audit dir. Useful when aggregation crashed mid-way.

- [ ] **Step 10.4.2: Test + commit** `feat(M10): senex aggregate`.

### Task 10.5: senex config show

- [ ] **Step 10.5.1: Implement** — resolves config for given repo path, prints fully merged TOML to stdout.

- [ ] **Step 10.5.2: Test + commit** `feat(M10): senex config show`.

### Task 10.6: scripts/run_senex.bat + setup.ps1

- [ ] **Step 10.6.1: Write `scripts/run_senex.bat`**:
  ```bat
  @echo off
  title senex -- Nightly Audit
  cd /d "%~dp0\.."
  call .venv\Scripts\activate.bat
  python -m senex audit --nightly
  ```

- [ ] **Step 10.6.2: Write `scripts/setup.ps1`** that creates `.venv`, runs `pip install -e .`, verifies `senex --version`, prints next steps.

- [ ] **Step 10.6.3: Commit** `feat(M10): Windows scripts`.

### Task 10.7: README + .config.example refresh

- [ ] **Step 10.7.1: Replace one-line README** with full quickstart: install, configure, run, view.

- [ ] **Step 10.7.2: Verify `senex.config.toml.example`** is in sync with current `SenexConfig`.

- [ ] **Step 10.7.3: Commit** `docs(M10): README + config example`.

### Task 10.8: Full test suite green

- [ ] **Step 10.8.1: Run** `pytest tests/ -v --ignore=tests/live`. Must be 100% green.

- [ ] **Step 10.8.2: Coverage check** `pytest --cov=senex --cov-report=term-missing`. Target: 85% on auditor, renderer, walker, checkpoint, events, secret_redactor, findings_aggregator, phases/*, tools/*; 60% elsewhere.

- [ ] **Step 10.8.3: Fix gaps** until targets met.

- [ ] **Step 10.8.4: Commit** `test(M10): full test suite green at coverage targets`.

### Task 10.9: Live validation gates 13a-13e

**Prerequisites:** LM Studio running with `google/gemma-4-26b-a4b` loaded. Tiny fixture repo at `tests/fixtures/repos/tiny_python/` indexed via `npx gitnexus analyze`.

- [ ] **Step 10.9.1: Gate 13: pytest** `pytest tests/ -v --ignore=tests/live` → all green (already done in 10.8).

- [ ] **Step 10.9.2: Gate 13a: senex doctor** against live LM Studio → exit 0.

- [ ] **Step 10.9.3: Gate 13a: live audit** `senex audit tests/fixtures/repos/tiny_python --no-tui` → produces complete audit dir. Verify: combined.md exists, findings.json schema-validates, claude-handoff.md exists, at least one `<file>.md` exists. Manual inspection of one report.

- [ ] **Step 10.9.4: Gate 13b: TUI render** `senex audit tests/fixtures/repos/tiny_python` (with TUI) → launcher renders, monitor updates, completion screen shown.

- [ ] **Step 10.9.5: Gate 13c: resume** kill mid-run; restart with `--resume`; verify no re-audit of completed files; clean continuation.

- [ ] **Step 10.9.6: Gate 13d: tool loop** verify at least one `<file>.thinking.md` shows tool-call evidence (find a finding that quotes a tool result).

- [ ] **Step 10.9.7: Gate 13e: compaction smoke** add a synthetic-bloat env var to client to force compaction; verify `CompactionTriggered` + `CompactionComplete` events; audit completes.

- [ ] **Step 10.9.8: Validation report** — write `docs/validation/2026-04-26-v1-validation.md` with each gate's pass/fail, evidence paths, latencies, redaction examples.

- [ ] **Step 10.9.9: Commit** `validate(M10): live gates 13a-13e green`.

### Task 10.10: v1.0.0 tag + release

- [ ] **Step 10.10.1: Bump** `senex/__init__.py` `__version__ = "1.0.0"` and `pyproject.toml`.

- [ ] **Step 10.10.2: Update CHANGELOG.md** with v1.0.0 entry.

- [ ] **Step 10.10.3: Commit** `release: v1.0.0`.

- [ ] **Step 10.10.4: Tag** `git tag -a v1.0.0 -m "senex v1.0.0 — local-LLM repo audit tool"` + `git push origin v1.0.0`.

- [ ] **Step 10.10.5: Create GitHub release** via `gh release create v1.0.0 --notes-file CHANGELOG.md` (or manual notes from spec §1 summary).

## Acceptance criteria

- `pytest tests/ -v --ignore=tests/live` is 100% green.
- Coverage report meets targets: ≥85% on auditor, renderer, walker, checkpoint, events, secret_redactor, findings_aggregator, phases/*, tools/*; ≥60% elsewhere.
- All 5 live gates (13a, 13b, 13c, 13d, 13e) pass with evidence captured in `docs/validation/2026-04-26-v1-validation.md`.
- Gate 13a: a real `senex audit` run on `tests/fixtures/repos/tiny_python/` produces `combined.md`, `findings.json` (schema-validates), `claude-handoff.md`, and per-file `<file>.md` for each fixture file.
- Gate 13c: a killed-and-resumed run skips the files completed before the kill (verify via checkpoint + final findings file count).
- Gate 13d: at least one `<file>.thinking.md` quotes evidence from a tool result (manual inspection criterion).
- Gate 13e: `CompactionTriggered` and `CompactionComplete` events appear in `events.jsonl` for the bloat-forced run, and the run still completes.
- `senex --version` prints `1.0.0`.
- Git tag `v1.0.0` exists; `gh release view v1.0.0` returns release metadata.
- `README.md` quickstart is runnable end-to-end by a new user (manual verification).
