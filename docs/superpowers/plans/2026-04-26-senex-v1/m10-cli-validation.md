# Milestone 10: CLI + Validation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M10 is the final mile: the `senex` CLI entry point, all subcommands (`audit`, `view`, `doctor`, `aggregate`, `config`, `lifecycle`), Windows scheduler scripts, README, the full live-validation gate suite (13a–13e against a real LM Studio + indexed repo), and the v1.0.0 release tag. After M10, senex is shippable.

**Architectural intent:** Layer 7 is the entry point. The CLI does *no* business logic — it parses args, builds the config + lens + bus + lifecycle + subscriber set, and calls into M8's `run_audit()` or other handlers. The live-validation gates exist because the entire stack has been tested with mocks/recorded fixtures up to this point; M10 is the first time we run against real LM Studio HTTP, real `npx gitnexus`, real Textual rendering. Gates 13a–13e are the proof.

## Prerequisites

- **Completed milestones:** ALL — M1 through M9.
- **Required modules from prior work:** Every module in `senex/`. The CLI is the composition root.
  - `senex/config.py` (M1) — `load_config()`, `resolve_config()`, `SenexConfig`
  - `senex/lens.py` (M1) — `load_lens()`
  - `senex/events.py` (M1) — `EventBus`, `CommandBus`
  - `senex/lmstudio_client.py` (M3) — `LMStudioClient`
  - `senex/lmstudio_lifecycle.py` (M4) — `Lifecycle`, `lifecycle_status()`, `clear_locks()`
  - `senex/graph_awareness.py` (M2) — `GraphContextProvider`
  - `senex/tools/registry.py`, `senex/tools/loop.py` (M5)
  - `senex/compaction.py` (M6) — `Compactor`
  - `senex/renderer.py`, `senex/findings_aggregator.py` (M7)
  - `senex/auditor.py` (M8) — `run_audit()`
  - `senex/phases/preflight.py` (M8) — `check_config`, `check_lms`, `check_addendum_safety`, etc. (reused by `senex doctor`)
  - `senex/phases/aggregate.py` (M8) — `AggregatePhase` (reused by `senex aggregate`)
  - `senex/subscribers/*` (M9) — `DiskWriterSubscriber`, `MetricsCollectorSubscriber`, `HeadlessSubscriber`, `TuiSubscriber`
  - `senex/tui/app.py` (M9) — `SenexApp`
- **Required tools/state:**
  - LM Studio running with `google/gemma-4-26b-a4b` loaded (for live gates 13a/13b/13c/13d/13e). Verify with `lms ps`.
  - `tests/fixtures/repos/tiny_python/` (from M2) indexed via `npx gitnexus analyze` (for live gates)
  - `npx gitnexus` available on PATH
  - `lms` CLI on PATH (or `lmstudio` Python SDK installed) for lifecycle backend
  - GitHub CLI (`gh`) authenticated for the v1.0.0 release step (Task 10.10.5)
  - Free disk space ≥ 1 GB at the configured `output.audit_dir` for live gates

## Deliverable

This milestone creates the following files:

- `senex/cli.py` — `main()` with argparse for all 6 subcommands; named exceptions `CLIArgError`, `ValidationGateFailed`
- `senex/__main__.py` — shim so `python -m senex` works (calls `cli.main()`)
- `scripts/run_senex.bat` — Windows scheduled-task entrypoint (verbatim per spec §10)
- `scripts/setup.ps1` — first-time setup (venv + deps + verify)
- `README.md` — full quickstart (replaces one-line placeholder)
- Refreshed `senex.config.toml.example` (final sync with `SenexConfig`)
- `docs/validation/2026-04-26-v1-validation.md` — gate-by-gate evidence
- `CHANGELOG.md` — v1.0.0 entry
- Bumped versions: `senex/__init__.py.__version__ = "1.0.0"`, `pyproject.toml.version = "1.0.0"`
- Git tag `v1.0.0` + GitHub release
- Tests:
  - `tests/unit/test_cli_argparse.py` — argparse structure, mutually-exclusive flags, `--help` text
  - `tests/unit/test_cli_audit.py` — composition; mocked deps; exit-code matrix (0/1/2/3/130)
  - `tests/unit/test_cli_doctor.py` — single-repo + all-repos branches; `--json` schema
  - `tests/unit/test_cli_aggregate.py` — re-run Phase 5; missing-required-files refusal
  - `tests/unit/test_cli_config_show.py` — TOML round-trip; `--json` output
  - `tests/unit/test_cli_lifecycle.py` — status table + JSON; clear-locks with/without `--force`
  - `tests/unit/test_setup_ps1.py` — runs `setup.ps1` in tmp dir; asserts `.venv` created and `senex` importable

## Downstream consumers

None — M10 is the leaf milestone. After M10 ships, downstream is real users.

## Spec sections referenced

- §10 CLI Surface — full subcommand specification (audit, view, doctor, aggregate, config, lifecycle) and common flags (`--config`, `--no-tui`, `--verbose`, `--quiet`, `--json`)
- §API-4 — argparse subcommand structure
- §API-5 — sentinel handling (`@auto`, `@first`) for `--model`
- §8.1 Pre-flight — reused by `senex doctor`
- §8.4 Observability — `senex doctor` is the operator-facing diagnostic
- §11 Security & Privacy — README threat-model summary; `doctor` does not print secrets
- §13 Glossary — terms used in README
- Conventions: §11 git (commit prefix), §12 code review (gate before merge), §13 docs (README contract)
- Validation gates 13a–13e:
  - 13a: `senex doctor` + headless audit produces complete artifacts
  - 13b: TUI renders launcher + monitor + completion
  - 13c: Resume from mid-run kill produces clean continuation
  - 13d: Tool loop produces evidence (a finding quoting tool result)
  - 13e: Compaction smoke fires + completes

## Key contracts

- **`main()`** in `senex/cli.py` — argparse with 6 subcommands. Common flags via *parent parser* (so every subcommand inherits): `--config`, `--no-tui`, `--verbose`, `--quiet`, `--json`, `--no-load`, `--no-unload`, `--unload-after`. `main(argv: list[str] | None = None) -> int` returns exit code; `if __name__ == "__main__": sys.exit(main())`.
- **Exit-code contract (per spec §8.1):** `0` success · `1` partial success (some files errored) · `2` config / setup error · `3` external dependency error · `130` interrupted (SIGINT). Tests assert each path returns the documented code.
- **Named exceptions:**
  - `CLIArgError(Exception)` — invalid CLI arg combinations (e.g. `--resume` + `--nightly`). Caught in `main()`, prints to stderr, returns exit code 2.
  - `ValidationGateFailed(Exception)` — raised by `senex doctor` when any check returns `fail` and `--json` was not requested (so we still exit non-zero with a human-readable banner). Caught in `main()`, returns exit code from the failing check.
- **`senex audit [repo]`** — composes config + lens + bus + (TuiSubscriber OR HeadlessSubscriber) + run_audit() + lifecycle. Flags: `--resume`, `--nightly` (iterates `config.repos`), `--no-tui`, `--include-tests`, `--lens <name>`, `--min-priority <level>`, `--model <id>`, `--allow-mixed-resume`, `--unsafe-resume`. Mutually exclusive: `--resume` + `--nightly` (raises `CLIArgError`).
- **`senex view [audit-dir]`** — calls M9's view replay handler. Flags: `--speed N` (replay speed multiplier; default 1.0).
- **`senex doctor [repo]`** — runs all preflight functions from `senex.phases.preflight` against either: target repo (if `<repo-path>` given) OR every repo in `config.repos` (if no path). `--json` outputs structured per `senex doctor --json` schema below. Returns aggregate exit code (worst of all checks).
- **`senex aggregate <audit-dir>`** — re-runs Phase 5 (`AggregatePhase`) against an existing audit dir. Validates audit_dir structure (required files: `checkpoint.json`, `findings.partial.jsonl`) before running.
- **`senex config show [repo]`** — resolves config (defaults → file → per-repo entry) for given repo path. Outputs as TOML by default (re-serialize via `tomli_w` or by formatting `model_dump()`); with `--json`, outputs JSON. Supports `--all` to dump every repo entry.
- **`senex lifecycle status [--json]`** + **`senex lifecycle clear-locks [--force]`** — wired in M4 Task 4.4; CLI exposes them here. Status output is human table by default; `--json` for scripting. `clear-locks` refuses live-process entries unless `--force`.

## Watch-outs

- **Argparse mutual exclusion:** `--resume` and `--nightly` are mutually exclusive on `audit` (raise `CLIArgError`, exit 2). `--no-load` and `--unload-after` are mutually compatible (different concerns) but `--no-tui` + interactive TUI flags is a no-op (warn). `--json` only meaningful with subcommands that support it (doctor, lifecycle status, config show). Test these combinations.
- **`--nightly` iterates `config.repos`** — a malformed entry should not abort the whole batch. Each repo is independent; failures are logged; the final exit code is the *worst* of all repo outcomes. If 3 repos succeed and 1 errors, exit code is 1 (partial success).
- **Sentinel handling for `--model`** (per §API-5): `@auto` (use whatever's loaded), `@first` (use first loaded), or a literal model id. CLI does not resolve sentinels — passes them to lifecycle; resolution happens at preflight.
- **Live validation gates require real LM Studio.** They are NOT part of `pytest tests/ -v --ignore=tests/live`. They run manually (or in a separate CI lane) per Task 10.9 step list.
- **Gate 13c (resume) requires killing a process mid-run.** The exact procedure (Task 10.9.5) uses `Ctrl+C` after ~3 files completed; the partial 4th file's `<file>.md.tmp` (if any) is discarded by the resume code (M8 Task 8.7); resume continues from file 4. Phase 4 (cross-cut) and Phase 5 (aggregate) re-run on the *full* set per spec §8.3.
- **Gate 13e (compaction smoke) needs a synthetic-bloat env var.** `SENEX_FORCE_COMPACTION_AT_FILE=N` (already used in M6 tests) deterministically triggers compaction at file N.
- **Coverage targets are non-uniform.** 85% on critical paths (auditor, renderer, walker, checkpoint, events, secret_redactor, findings_aggregator, phases/*, tools/*); 60% elsewhere. CI fails if any module is below its target. Don't waste time on TUI-widget coverage; do invest in auditor coverage.
- **README is shipped, not draft.** It's the first thing a new user reads. Quickstart must be runnable end-to-end on a fresh Windows install.
- **`senex.config.toml.example` MUST stay in sync with `SenexConfig`.** Task 10.7.2 includes a programmatic check: `pytest tests/unit/test_config_example_in_sync.py`.
- **GitHub release uses `gh release create`.** This requires `gh auth login` to have happened. Authenticate before Task 10.10.5.
- **`doctor` does not print secrets** (§11). The `--json` output is filtered through `secret_redactor` before serialization.
- **No business logic in `cli.py`.** Composition only. If a test reaches into `cli.py` to verify behavior beyond "args parsed correctly" or "deps wired correctly", that logic belongs in a lower module.

## Patterns to follow

- **Composition root pattern:** `cli.py` is *only* glue. It builds objects and wires them. No business logic. Each subcommand is a `def cmd_<name>(args, config) -> int` function returning exit code.
- **Parent-parser pattern for common flags:**
  ```python
  parent = argparse.ArgumentParser(add_help=False)
  parent.add_argument("--config", type=Path)
  parent.add_argument("--no-tui", action="store_true")
  parent.add_argument("--verbose", action="store_true")
  parent.add_argument("--quiet", action="store_true")
  parent.add_argument("--json", action="store_true")
  # ... lifecycle common flags
  subparsers = root.add_subparsers(dest="command", required=True)
  audit_p = subparsers.add_parser("audit", parents=[parent])
  ```
- **Validation report (Task 10.9.8):** structured Markdown with one section per gate, evidence paths, latencies, redaction examples. This is the proof artifact for v1.0.0.
- **Atomic version bump + tag:** Task 10.10 happens in one commit chain. Don't bump version without tagging; don't tag without releasing.
- **Per-subcommand TDD:** each test file follows the same shape — parse args → mock deps → call `cmd_<name>` → assert exit code + side effects.

## Tasks

### Task 10.1: CLI entry point + argparse

**Files:**
- Create: `senex/cli.py`
- Create: `senex/__main__.py`
- Create: `tests/unit/test_cli_argparse.py`

- [x] **Step 10.1.1: Failing tests** — assert each subcommand parses correctly with expected attributes; `--help` returns 0 and contains expected subcommand names; `--version` prints version from `senex.__version__`; `audit --resume --nightly` raises `CLIArgError` (exit 2); `audit --model "@auto"` parses sentinel as literal string. One test per parser branch.

- [x] **Step 10.1.2: Implement `main()` with argparse** — define named exceptions `CLIArgError` and `ValidationGateFailed` at module top. Use parent parser for common flags (`--config`, `--no-tui`, `--verbose`, `--quiet`, `--json`, `--no-load`, `--no-unload`, `--unload-after`). Subparsers:
  - `audit`: `<repo-path>` positional (optional when `--nightly`), `--resume`, `--nightly`, `--include-tests`, `--lens <name>`, `--min-priority <level>`, `--model <id>`, `--allow-mixed-resume`, `--unsafe-resume`. `--resume` + `--nightly` mutually exclusive.
  - `view`: `[<audit-dir>]` optional, `--speed <float>` (default 1.0).
  - `doctor`: `[<repo-path>]` optional.
  - `aggregate`: `<audit-dir>` required.
  - `config`: subcommand `show` taking `<repo-path>` or `--all`.
  - `lifecycle`: subcommands `status`, `clear-locks [--force]`.
  Per spec §10 + API-4. Reference `senex.__version__` for `--version`.

- [x] **Step 10.1.3: Implement `senex/__main__.py`** — single line: `from senex.cli import main; import sys; sys.exit(main())`. Verifies `python -m senex` invocation.

- [x] **Step 10.1.4: Test pass + commit** `feat(M10): CLI argparse skeleton with all 6 subcommands`.

### Task 10.2: senex audit (with + without TUI)

**Files:**
- Edit: `senex/cli.py` (add `cmd_audit`)
- Create: `tests/unit/test_cli_audit.py`

- [x] **Step 10.2.1: Failing tests** — exit-code matrix:
  - happy path → exit 0
  - one file errored → exit 1
  - bad config → exit 2
  - LMS unreachable → exit 3
  - SIGINT mid-run → exit 130
  - `--nightly` with one bad repo + 2 good → exit 1 (partial)
  - `--no-tui` selects `HeadlessSubscriber`; default selects `TuiSubscriber`
  All deps mocked (config, lens, bus, run_audit, lifecycle).

- [x] **Step 10.2.2: Implement `cmd_audit(args, config) -> int`** — composes: load config → resolve config (CLI flags + repo entry) → load lens → instantiate `EventBus` + `CommandBus` → instantiate subscribers (`DiskWriterSubscriber` + `MetricsCollectorSubscriber` + (`TuiSubscriber` if not `--no-tui` else `HeadlessSubscriber`)) → instantiate clients (`LMStudioClient`, `GraphContextProvider`, `ToolRegistry`, `ToolLoop`, `Compactor`, `Renderer`, `FindingsAggregator`, `Lifecycle`) → call `run_audit()` → return exit code. TUI mode: instantiate `SenexApp(...)`, call `app.run()`. Headless: `await run_audit(...)` directly via `asyncio.run()`.

- [x] **Step 10.2.3: `--resume` flag** wires to `run_audit(resume=True)`; auto-detects latest unfinished audit dir for the given repo path.

- [x] **Step 10.2.4: `--nightly` flag** iterates `config.repos`. Each repo wrapped in try/except; failures logged but loop continues. Final exit code is the worst (max numeric) outcome across all repos. Mutual exclusion with `--resume` enforced via `CLIArgError`.

- [x] **Step 10.2.5: Test pass + commit** `feat(M10): senex audit subcommand with full exit-code matrix`.

### Task 10.3: senex doctor

**Files:**
- Edit: `senex/cli.py` (add `cmd_doctor`)
- Create: `tests/unit/test_cli_doctor.py`

- [x] **Step 10.3.1: Failing tests** — test each branch with fixture configs:
  - `senex doctor` (no path, no `--all`) → runs against every `config.repos`
  - `senex doctor /path/to/repo` → runs against single repo
  - `senex doctor --json` → outputs structured JSON matching schema below; `exit_code` in JSON matches process exit
  - All-pass run → exit 0
  - One `fail` check → exit 2 or 3 (matching the failing check's documented exit code per §8.1)
  - Verify `secret_redactor` is applied to `details` field before JSON serialization (no `api_key`/`*_token`/`*_secret` substrings in output)

- [x] **Step 10.3.2: Implement `cmd_doctor(args, config) -> int`** — runs all preflight functions from `senex.phases.preflight` (the same `check_config`, `check_lms`, `check_addendum_safety`, etc. from M8 Task 8.2.3) against either: target repo (if `<repo-path>` given) OR every repo in config (if no path). Aggregate exit code = worst (max) over all checks. `--json` output schema:
  ```json
  {
    "version": 1,
    "repo": "/path/to/repo",
    "checks": [
      {"name": "config_parses", "status": "pass", "message": "...", "details": {}},
      {"name": "lmstudio_reachable", "status": "fail", "message": "connect refused", "details": {"base_url": "http://localhost:1234"}}
    ],
    "exit_code": 3
  }
  ```
  `status` ∈ `{"pass", "warn", "fail"}`. Multi-repo: top-level is a list of these objects.

- [x] **Step 10.3.3: Test pass + commit** `feat(M10): senex doctor reusing M8 preflight checks`.

### Task 10.4: senex aggregate

**Files:**
- Edit: `senex/cli.py` (add `cmd_aggregate`)
- Create: `tests/unit/test_cli_aggregate.py`

- [x] **Step 10.4.1: Failing tests**:
  - happy path: aggregate against a fixture audit dir → produces `findings.json`, `combined.md`, `claude-handoff.md`; exit 0
  - missing `checkpoint.json` → exit 2 with explicit error
  - missing `findings.partial.jsonl` → exit 2 with explicit error
  - corrupt `findings.partial.jsonl` (one bad line) → exit 1 (partial); skip bad line, log warning

- [x] **Step 10.4.2: Implement `cmd_aggregate(args, config) -> int`** — re-runs Phase 5 (`AggregatePhase` from M8) against an existing audit dir. Validates audit_dir structure (required files: `checkpoint.json`, `findings.partial.jsonl`) before running. Useful when aggregation crashed mid-way.

- [x] **Step 10.4.3: Test pass + commit** `feat(M10): senex aggregate with audit-dir validation`.

### Task 10.5: senex config show

**Files:**
- Edit: `senex/cli.py` (add `cmd_config_show`)
- Create: `tests/unit/test_cli_config_show.py`

- [x] **Step 10.5.1: Failing tests**:
  - TOML round-trip: `senex config show /path/to/repo` outputs valid TOML that, when re-loaded via `load_config`, produces the same `SenexConfig`
  - `--json` outputs JSON with same structure
  - `--all` dumps a list of (repo_path, resolved_config) pairs
  - secret fields (`api_key`, `*_token`, `*_secret`) are redacted in output (per §SEC-6)

- [x] **Step 10.5.2: Implement `cmd_config_show(args, config) -> int`** — resolves config (defaults → file → per-repo entry) for given repo path. Outputs as TOML by default (re-serialize via `tomli_w` or by formatting `config.model_dump()` through a TOML serializer). With `--json`, output JSON via `model_dump_json()`. Apply `secret_redactor` to all string values before serialization.

- [x] **Step 10.5.3: Test pass + commit** `feat(M10): senex config show with TOML round-trip + secret redaction`.

### Task 10.6: scripts/run_senex.bat + setup.ps1

**Files:**
- Create: `scripts/run_senex.bat`
- Create: `scripts/setup.ps1`
- Create: `tests/unit/test_setup_ps1.py`

- [x] **Step 10.6.1: Write `scripts/run_senex.bat`** verbatim per spec §10:
  ```bat
  @echo off
  title senex -- Nightly Audit
  cd /d "%~dp0\.."
  call .venv\Scripts\activate.bat
  python -m senex audit --nightly
  ```

- [x] **Step 10.6.2: Write `scripts/setup.ps1`** — full content:
  ```powershell
  # senex/scripts/setup.ps1 — first-time setup on Windows
  $ErrorActionPreference = "Stop"
  $RepoRoot = Split-Path -Parent $PSScriptRoot
  Set-Location $RepoRoot

  Write-Host "[setup] Creating virtual environment at .venv ..." -ForegroundColor Cyan
  if (Test-Path .venv) { Write-Host "[setup] .venv already exists; skipping creation." }
  else { python -m venv .venv }

  Write-Host "[setup] Installing senex (editable) + dependencies ..." -ForegroundColor Cyan
  & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
  & ".\.venv\Scripts\python.exe" -m pip install -e .

  Write-Host "[setup] Verifying senex --version ..." -ForegroundColor Cyan
  & ".\.venv\Scripts\python.exe" -m senex --version

  Write-Host ""
  Write-Host "[setup] Done." -ForegroundColor Green
  Write-Host "Next steps:"
  Write-Host "  1. Copy senex.config.toml.example -> senex.config.toml and edit"
  Write-Host "  2. Run: .\.venv\Scripts\activate; senex doctor"
  Write-Host "  3. Run: senex audit C:\path\to\your\repo"
  ```

- [x] **Step 10.6.3: Failing test** — `tests/unit/test_setup_ps1.py` runs `setup.ps1` in a temp dir (skipped on non-Windows via `pytest.skip`); asserts `.venv` is created and `python -m senex --version` returns 0 with version string.

- [x] **Step 10.6.4: Test pass + commit** `feat(M10): Windows scheduled-task scripts`.

### Task 10.7: README + .config.example refresh

**Files:**
- Edit: `README.md`
- Edit: `senex.config.toml.example`
- Create: `tests/unit/test_config_example_in_sync.py`

- [x] **Step 10.7.1: Replace one-line README** with full quickstart. Required sections:
  - **Overview** (1 paragraph from spec §1)
  - **Prerequisites** — Python 3.11+, LM Studio (download URL), `google/gemma-4-26b-a4b` model loaded, GitNexus optional but recommended
  - **Install** — `git clone …; cd senex; .\scripts\setup.ps1` (Windows) or `python -m pip install -e .` (manual)
  - **Configure** — copy `senex.config.toml.example` → `senex.config.toml`; edit `[lmstudio].base_url` and `[[repos]]` entries
  - **Run** — `senex audit C:\path\to\repo` (TUI), `senex audit C:\path\to\repo --no-tui` (headless), `senex audit --nightly` (all configured repos)
  - **View results** — `senex view` (latest audit), `senex view <audit-dir>` (specific run)
  - **Diagnostics** — `senex doctor` (run before first audit), `senex lifecycle status`
  - **Where reports go** — explain `E:\senex-audits\<repo>\<DATE>-<run_id_short>\` layout (combined.md, findings.json, claude-handoff.md, per-file `.md`)
  - **Security note** — 3 bullets from §11: source stays local; no telemetry; secret redaction applied to all persisted artifacts
  - **Troubleshooting** — common doctor failures + fixes
  - **License + contribution links**

- [x] **Step 10.7.2: Verify `senex.config.toml.example`** is in sync with current `SenexConfig`. Failing test: `tests/unit/test_config_example_in_sync.py` loads the example, validates it against `SenexConfig`, and asserts every field in `SenexConfig` (including nested) appears as a comment or active key in the example file. Refresh the example file as needed.

- [x] **Step 10.7.3: Test pass + commit** `docs(M10): README quickstart + config example sync`.

### Task 10.8: Full test suite green

- [ ] **Step 10.8.1: Run** `pytest tests/ -v --ignore=tests/live`. Must report `failed=0`. If anything fails, fix before proceeding.

- [ ] **Step 10.8.2: Coverage check** `pytest tests/ -v --ignore=tests/live --cov=senex --cov-report=term-missing --cov-report=xml`. Expected output snippet to pin (numbers will vary, structure must match):
  ```
  ============================== test session starts ===============================
  ...
  tests/unit/test_cli_argparse.py::test_audit_subcommand PASSED
  ...
  ===================================== PASSED =====================================
  ---------- coverage: platform win32, python 3.11.x ----------
  Name                                  Stmts   Miss  Cover   Missing
  -------------------------------------------------------------------
  senex/auditor.py                        220     22  90.0%   ...
  senex/renderer.py                       180     20  88.9%   ...
  ...
  TOTAL                                  4500    400  91.1%
  ============================ N passed in MM.MMs ==============================
  ```
  Coverage targets per conventions §6: ≥85% on `auditor`, `renderer`, `walker`, `checkpoint`, `events`, `secret_redactor`, `findings_aggregator`, `phases/*`, `tools/*`; ≥60% elsewhere. CI fails if any module below its target.

- [ ] **Step 10.8.3: Fix gaps** until per-module targets met. Add tests where coverage is low; do NOT add `# pragma: no cover` except for genuine `if __name__ == "__main__":` blocks.

- [ ] **Step 10.8.4: Commit** `test(M10): full test suite green at coverage targets`.

### Task 10.9: Live validation gates 13a-13e

**Prerequisites:** LM Studio running with `google/gemma-4-26b-a4b` loaded (verify with `lms ps`). Tiny fixture repo at `tests/fixtures/repos/tiny_python/` (created in M2 Task 2.1.4 — see R10 ownership note in `m2-walker-graph.md`) indexed via `npx gitnexus analyze`. Free disk ≥ 1 GB at output path. `senex.config.toml` configured with the fixture repo as a `[[repos]]` entry.

Each gate has its own subtask with: setup, command (verbatim), expected output (key lines), manual verification checklist, pass criteria.

- [ ] **Step 10.9.1: Gate 13: pytest** — already done in 10.8.1. Re-confirm `pytest tests/ -v --ignore=tests/live` is green; do not rerun if nothing has changed since 10.8.

- [ ] **Step 10.9.2: Gate 13a (part 1): senex doctor against live LM Studio**
  - **Setup:** `lms ps` confirms `google/gemma-4-26b-a4b` is loaded.
  - **Command:** `senex doctor tests/fixtures/repos/tiny_python --json > docs/validation/evidence/13a-doctor.json`
  - **Expected output (key lines):** `"lmstudio_reachable": "pass"`, `"model_loaded": "pass"`, `"gitnexus_index_fresh": "pass"`, `"exit_code": 0`.
  - **Pass criteria:** exit 0; every check `pass` or `warn` (no `fail`).

- [ ] **Step 10.9.3: Gate 13a (part 2): live headless audit**
  - **Setup:** doctor green from 13a.1.
  - **Command:** `senex audit tests/fixtures/repos/tiny_python --no-tui --no-unload`
  - **Expected output:** stdout shows `[1/N] auditing …` lines from `HeadlessSubscriber`; final summary line; exit 0.
  - **Manual verification checklist:**
    - [ ] `combined.md` exists at `<audit-dir>/combined.md`
    - [ ] `findings.json` validates against `senex/schema/findings_index.schema.json`
    - [ ] `claude-handoff.md` exists
    - [ ] At least one per-file `<file>.md` exists for each fixture file
    - [ ] Open one per-file report and confirm structure matches §7.1
  - **Pass criteria:** all 5 checklist items checked; exit 0; latency captured.

- [ ] **Step 10.9.4: Gate 13b: TUI render**
  - **Setup:** clean audit-dir state (delete or move any prior audit for this repo).
  - **Command:** `senex audit tests/fixtures/repos/tiny_python --no-unload` (default = TUI on).
  - **Manual verification checklist:**
    - [ ] Launcher screen renders all 7 form fields
    - [ ] Click Start → Monitor screen transitions
    - [ ] Progress bar advances; current_file widget updates each file
    - [ ] Findings panel populates with at least one finding
    - [ ] Completion screen renders at end with final counts
    - [ ] No render exceptions in `audit.log`
  - **Pass criteria:** all 6 checklist items; exit 0.

- [ ] **Step 10.9.5: Gate 13c: resume**
  - **Setup:** clean audit-dir state.
  - **Procedure:**
    1. Open terminal A: `senex audit tests/fixtures/repos/tiny_python --no-tui --no-unload`
    2. After approximately 3 files have completed (watch headless output), press `Ctrl+C` in terminal A. Audit should exit 130.
    3. Inspect: `cat <audit-dir>/checkpoint.json` — confirm `completed_files` length is 3; `current_phase` is `"file_audit"`.
    4. In same terminal: `senex audit tests/fixtures/repos/tiny_python --no-tui --resume`.
    5. Watch headless output: should NOT re-audit files 1-3; resumes at file 4.
    6. Audit completes; `combined.md` references all N files; `findings.json` count = full set.
  - **Manual verification checklist:**
    - [ ] First run interrupted cleanly with exit 130
    - [ ] Checkpoint shows 3 completed files at interrupt time
    - [ ] Resume run skips files 1-3 (no `[1/N] auditing` lines for them)
    - [ ] Phase 4 (cross-cut) re-runs on full set (event in `events.jsonl`)
    - [ ] Phase 5 (aggregate) re-runs on full set
    - [ ] Final `findings.json` has findings for ALL files (not just files 4-N)
  - **Pass criteria:** all 6 checklist items.

- [ ] **Step 10.9.6: Gate 13d: tool loop produces evidence**
  - **Setup:** completed audit-dir from 13a.2 or 13b.
  - **Procedure:** open `<audit-dir>/<file>.thinking.md` for each fixture file; find at least one that contains a tool-call trace AND a finding in the corresponding `<file>.md` quoting evidence from a tool result (e.g., a finding citing a line found via `grep` or `gitnexus_query`).
  - **Manual verification checklist:**
    - [ ] At least one `<file>.thinking.md` shows tool-call evidence (tool_calls block visible)
    - [ ] At least one finding in that file quotes the tool result content as evidence
    - [ ] Capture path to the example in `13d-evidence.md`
  - **Pass criteria:** all 3 checklist items.

- [ ] **Step 10.9.7: Gate 13e: compaction smoke**
  - **Setup:** clean audit-dir state. Set env var: `$env:SENEX_FORCE_COMPACTION_AT_FILE = "2"` (PowerShell).
  - **Command:** `senex audit tests/fixtures/repos/tiny_python --no-tui --no-unload`
  - **Manual verification checklist:**
    - [ ] `events.jsonl` contains `CompactionTriggered` event for file 2
    - [ ] `events.jsonl` contains `CompactionComplete` event for file 2
    - [ ] Audit completes with exit 0 (compaction did not crash)
    - [ ] Token-budget delta visible in `CompactionComplete.payload`
  - **Pass criteria:** all 4 checklist items.

- [ ] **Step 10.9.8: Validation report** — write `docs/validation/2026-04-26-v1-validation.md`. Template:
  ```markdown
  # senex v1.0.0 Validation Report — 2026-04-26

  **Run environment:** Windows 11 · Python 3.11.x · LM Studio <ver> · gemma-4-26b-a4b · GitNexus <ver>

  **Fixture:** `tests/fixtures/repos/tiny_python/` (N files)

  ## Gate 13: pytest
  - Command: `pytest tests/ -v --ignore=tests/live`
  - Result: PASS (XXX passed, 0 failed)
  - Coverage: XX.X% overall; per-module targets met
  - Evidence: `evidence/13-pytest.txt`, `evidence/13-coverage.xml`

  ## Gate 13a: doctor + headless audit
  - **13a.1 doctor:** PASS · exit 0 · evidence: `evidence/13a-doctor.json`
  - **13a.2 audit:**  PASS · exit 0 · latency: M:SS · audit-dir: `<path>`
  - Artifact checklist: combined.md [x] · findings.json [x] · claude-handoff.md [x] · per-file .md [x]
  - Redaction example: input contained `api_key=sk-...`; output `<file>.md` shows `api_key=<REDACTED>`

  ## Gate 13b: TUI render
  - Result: PASS · evidence: screen-recording at `evidence/13b-tui.gif`
  - Checklist: launcher [x] · monitor [x] · progress [x] · findings panel [x] · completion screen [x]

  ## Gate 13c: resume
  - Result: PASS · interrupted at file 3/N · resumed; final findings count = N (matches uninterrupted run)
  - Evidence: `evidence/13c-checkpoint-pre.json`, `evidence/13c-checkpoint-post.json`, `evidence/13c-events.jsonl`

  ## Gate 13d: tool loop evidence
  - Result: PASS · example: `<audit-dir>/foo.py.thinking.md` shows `gitnexus_query` call; `<audit-dir>/foo.py.md` finding cites tool result
  - Evidence: `evidence/13d-thinking-excerpt.md`

  ## Gate 13e: compaction smoke
  - Result: PASS · `SENEX_FORCE_COMPACTION_AT_FILE=2` triggered compaction; audit completed exit 0
  - Token-budget delta: 32k → 18k after compaction
  - Evidence: `evidence/13e-events.jsonl`

  ## Summary
  All gates green. v1.0.0 cleared for tag.
  ```
  Save evidence files in `docs/validation/evidence/`.

- [ ] **Step 10.9.9: Commit** `validate(M10): live gates 13a-13e green; v1.0.0 validation report`.

### Task 10.10: v1.0.0 tag + release

- [ ] **Step 10.10.1: Pre-release verification** — confirm: every Task 10.9 gate green; `pytest tests/ -v --ignore=tests/live` green (do not rerun if nothing changed since 10.8); `gh auth status` shows authenticated.

- [ ] **Step 10.10.2: Bump versions** — edit `senex/__init__.py`: `__version__ = "1.0.0"`. Edit `pyproject.toml`: `version = "1.0.0"`. Verify with `python -m senex --version` → prints `1.0.0`.

- [ ] **Step 10.10.3: Update CHANGELOG.md** — exact template for the v1.0.0 entry:
  ```markdown
  ## [1.0.0] - 2026-04-26

  First public release. senex is a local-LLM-powered repository audit tool that file-by-file
  analyzes a target repo via LM Studio (`google/gemma-4-26b-a4b`), with GitNexus graph
  awareness, structured findings, and a Claude Code handoff artifact.

  ### Added
  - Six CLI subcommands: `audit`, `view`, `doctor`, `aggregate`, `config show`, `lifecycle`
  - Structured per-file Markdown reports + `findings.json` + combined report + `claude-handoff.md`
  - Six-tool framework: gitnexus_query / gitnexus_context / gitnexus_impact / read_file / grep / search_code
  - Bounded tool-call loop with budget enforcement
  - Context compaction safety net for long-context runs
  - Full LM Studio model lifecycle (auto-load + auto-unload via runlock)
  - Integrated Textual TUI with Launcher + Monitor screens
  - Resume from interrupted runs with HMAC-signed checkpoints
  - Comprehensive secret redaction on all persisted artifacts
  - Pre-flight diagnostic via `senex doctor` (`--json` for CI)
  - Windows scheduled-task entrypoint (`scripts/run_senex.bat`)
  - Live validation gates 13a-13e against real LM Studio (see `docs/validation/2026-04-26-v1-validation.md`)

  ### Security
  - Source code never leaves the local machine
  - Trust-boundary clauses in system prompts (per §SEC-2)
  - Path-safety for tool inputs (per §SEC-3, §5.11.4)
  - Loopback-only LM Studio binding by default (§SEC-5)
  - Checkpoint HMAC integrity (§SEC-8)
  ```

- [ ] **Step 10.10.4: Commit** `release: v1.0.0`.

- [ ] **Step 10.10.5: Tag**
  ```bash
  git tag -a v1.0.0 -m "senex v1.0.0 — local-LLM repo audit tool"
  git push origin v1.0.0
  ```

- [ ] **Step 10.10.6: GitHub release**
  ```bash
  gh release create v1.0.0 --notes-file CHANGELOG.md --title "senex v1.0.0"
  ```
  (or, for inline notes from spec §1 summary: `gh release create v1.0.0 --notes "Local-LLM-powered repo audit tool. See CHANGELOG for details."`)
  Verify with `gh release view v1.0.0`. If a CI workflow exists, run `gh run watch` to confirm the release pipeline succeeds.

## Acceptance criteria

- `pytest tests/ -v --ignore=tests/live` is 100% green (`failed=0`).
- Coverage report meets per-module targets: ≥85% on `auditor`, `renderer`, `walker`, `checkpoint`, `events`, `secret_redactor`, `findings_aggregator`, `phases/*`, `tools/*`; ≥60% elsewhere. CI gate enforces this.
- All 6 subcommands parse correctly; `--help` returns exit 0; `--version` prints `1.0.0`.
- `audit --resume --nightly` raises `CLIArgError` and exits 2 with explicit message.
- Exit-code matrix verified in unit tests: 0 (success), 1 (partial), 2 (config), 3 (external dep), 130 (SIGINT).
- `senex doctor` reuses M8 preflight functions; `--json` output validates against the documented schema; `secret_redactor` applied to all serialized strings.
- `senex aggregate` refuses to run on an audit-dir missing `checkpoint.json` or `findings.partial.jsonl` (exit 2).
- `senex config show` round-trips: re-loading its TOML output produces an equal `SenexConfig`. Secret fields redacted in output.
- `senex lifecycle status` and `clear-locks` are wired through the M4 handlers; `--force` is required for live-process entries.
- `scripts/run_senex.bat` matches spec §10 verbatim. `scripts/setup.ps1` creates `.venv`, installs senex editable, verifies `--version`, prints next steps. `tests/unit/test_setup_ps1.py` runs end-to-end on Windows.
- `senex.config.toml.example` is in sync with `SenexConfig` (`tests/unit/test_config_example_in_sync.py` green).
- `README.md` is runnable end-to-end by a new user (manual verification on a fresh Windows VM or container).
- All 5 live gates (13a, 13b, 13c, 13d, 13e) pass with evidence captured in `docs/validation/2026-04-26-v1-validation.md` and `docs/validation/evidence/`.
  - Gate 13a: `combined.md`, `findings.json` (schema-validates), `claude-handoff.md`, per-file `<file>.md` for each fixture file.
  - Gate 13c: killed-and-resumed run skips pre-kill files (verify via checkpoint diff + final findings count = uninterrupted-run count). Phase 4 + Phase 5 re-run on full set.
  - Gate 13d: at least one `<file>.thinking.md` quotes evidence from a tool result (manual inspection criterion).
  - Gate 13e: `CompactionTriggered` and `CompactionComplete` events appear in `events.jsonl` for the bloat-forced run, and the run still completes exit 0.
- `senex --version` prints `1.0.0`; `pyproject.toml` `version = "1.0.0"`.
- Git tag `v1.0.0` exists; `gh release view v1.0.0` returns release metadata; CI (if present) green.
- CHANGELOG.md `[1.0.0]` entry matches the template above.
