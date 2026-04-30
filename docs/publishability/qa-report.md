# senex - QA / Test Coverage Report

- **Date**: 2026-04-29
- **Branch**: main
- **HEAD**: 1afb7a5 feat(cli): pre-flight SGLang container start in cmd_audit
- **Test suite snapshot**: 912 collected (5 skipped), wall ~38s
- **Test source files**: 56 (tests/{unit,tui,integration,golden,recorded,fixtures}/)
- **Test functions counted**: 831 (parametrize expands to 912)
- **Scope**: read-only audit of test coverage, anti-patterns, fixtures, CI/pre-commit posture.

> Convention: priority is **must** (release blocker), **should** (next sprint), **nice** (backlog). Effort is S (<= half day), M (1-2 days), L (3+ days).

---
## 1. Coverage gap inventory - last 7 commits

Output of git log --oneline -7:

```
1afb7a5 feat(cli): pre-flight SGLang container start in cmd_audit
b5684c0 feat(wizard): single-model fast path + server-aware context window + richer confirm (#6)
adea7aa fix(wizard): ASCII fallback for non-cp1252 characters (#5)
9196e00 feat(sglang): backend-agnostic UX + opt-in container lifecycle management (#4)
adc5681 feat(tui): surface skills + cross-file memory + tool budget in Monitor (#3)
709a2e9 feat: SGLang migration + 3 new tools + skills + cross-file memory (#2)
cd86797 fix(tools): remove invalid --query/--target/--json flags from gitnexus subprocess calls; wrap 4xx as SchemaNegotiationFailed
```

### 1.1 cd86797 - gitnexus subprocess flag fix (HEAD~6)

- **Tested**: yes - tests/unit/test_lmstudio_client.py updated (+45 lines / +9 cases) to cover the 4xx -> SchemaNegotiationFailed wrap. The two gitnexus tools have positive-path tests in test_tool_gitnexus_query.py / test_tool_gitnexus_impact.py.
- **Gap**: no regression test that asserts --query / --target / --json are NOT present in the argv list passed to asyncio.create_subprocess_exec. The very bug this commit fixed could re-emerge if a future contributor adds them back.
- **Recommendation (should, S)**: add an argv-shape assertion in test_tool_gitnexus_query.py and test_tool_gitnexus_impact.py that asserts the forbidden flags do not appear in the argv list.

### 1.2 709a2e9 - SGLang migration + 3 new tools + skills + cross-file memory (HEAD~5, +1729 lines)

This is by far the largest gap.

| Component                        | LOC        | Behavioral tests | Status   |
| -------------------------------- | ---------- | ---------------- | -------- |
| senex/tools/list_dir.py          | 138        | 0                | **none** |
| senex/tools/list_symbols.py      | 542        | 0                | **none** |
| senex/tools/run_semgrep.py       | 288        | 0                | **none** |
| senex/skills.py                  | 57         | 0                | **none** |
| senex/memory.py                  | 84         | 0                | **none** |
| SGLang infra (infra/sglang)      | ~140       | 0                | **none** |
| lmstudio_lifecycle.HTTPBackend   | ~180 (new) | 0 direct         | **none** |

The three new tools and skills.py / memory.py appear ONLY in tests/unit/test_lens.py::test_lens_load_correctness_returns_lens_with_nine_tools, which simply asserts the registry knows about them. None of the handlers, validators, or trigger logic is exercised.

The SGLang migration also introduced a 105-line rewrite of lmstudio_client.py (response_format=json_schema, native tools), but test_lmstudio_client.py was only patched (+8 lines) - major SGLang code paths are not regression-pinned.

### 1.3 adc5681 - TUI surface for skills/memory/tool budget (HEAD~4)

- **Tested**: TUI widget tests exist (test_widget_current_file.py - 8 cases). The tools_max + Skills: + Memory: labels are likely covered there, but two new event types were added.
- **Gap**: events SkillsInjected and MemoryInjected have schema entries (senex/schema/events.schema.json lines 1584-1660) but **no test** asserts a sample of either against the schema. test_schemas.py::test_events_schema_has_one_def_per_event_class will catch a missing $def, but not a malformed payload.
- **Gap**: no test verifies FileAuditPhase._audit_one actually publishes SkillsInjected / MemoryInjected in the right order.
- **Recommendation (should, S)**: add test_events_schema_validates_skills_injected_event and test_events_schema_validates_memory_injected_event to test_schemas.py. Add ordering assertion in test_phases/test_file_audit_phase.py.

### 1.4 9196e00 - backend-agnostic UX + opt-in container lifecycle management (HEAD~3, +240 lines)

- **Tested**: 4 lines in test_launcher.py - wizard label assertion only.
- **Gap**: HTTPBackend is the heart of this PR (~180 new LOC: _docker_argv, _run_docker, _wait_for_health, _list_models, is_loaded, load, unload, list_loaded). **Zero direct unit tests**. The only references in tests are a comment in test_lifecycle.py:109 and a comment in test_preflight_checks.py:251.
- **Gap**: LifecycleBackendFactory.select was extended to handle manage_container=True (returns backend without probing). No test of this branch.
- **Gap**: _docker_argv mixes WSL vs non-WSL prefixes, and compose_file / env_file parameterization. Argv-shape regression risk for shell-injection.
- **Recommendation (must, M)**: add tests/unit/test_http_backend.py covering all 8 methods with respx + mocked asyncio.create_subprocess_exec. See section 2 below for the test matrix.

### 1.5 adea7aa - ASCII fallback for non-cp1252 characters

- **Tested**: no test added. Visual fix only.
- **Gap**: a regression here would only surface on a Windows cmd console with cp1252; CI on Linux will never see it. Acceptable for now.
- **Recommendation (nice, S)**: parametrize one wizard test with a fake stdout whose .encoding = "cp1252" and assert no UnicodeEncodeError is raised when the string contains non-cp1252 glyphs (left/right arrows, ellipsis).

### 1.6 b5684c0 - wizard fast path + server-aware ctx window + richer confirm (HEAD~1)

- **Tested**: test_select_context_window_* cases were rewritten and test_wizard_effort_and_ctx_propagate_to_config now uses two models so the menu still appears.
- **Gap (high)**: the new single-served-model fast path in _select_model is **completely untested** - test_wizard_lists_loaded_models exercises the 3-model menu, but no test asserts the 1-model code path that bypasses the menu.
- **Gap (high)**: _detect_served_context_window(base_url) (the new httpx probe) is untested; no test mocks /v1/models to return max_model_len and asserts (a) options larger than the cap are dropped, (b) "server caps at N" message appears, (c) configured default clamps when it exceeds.
- **Gap**: _detect_backend(base_url) (used in confirm message: SGLang / LM Studio / inference server) is untested.
- **Gap**: confirm-prompt rendering - the new "ctx N, tools N/file" line is not asserted in any test.
- **Recommendation (must, M)**: add 5 tests covering (i) single-served-model fast path with id matching default, (ii) single-served-model fast path with id differing from default, (iii) ctx clamp when max_model_len < current, (iv) ctx menu strips options above cap, (v) _detect_backend for sglang/lmstudio/unknown URLs.

### 1.7 1afb7a5 - pre-flight SGLang container start in cmd_audit (HEAD, +67 lines)

- **Tested**: **zero**. _ensure_managed_container_up is a 60-line function that is the new gatekeeper for every audit run when manage_container=true.
- **Gap**: no test for the early-return branch (sglang_cfg is None or manage_container=False - the most common path), the success path, the ModelLoadFailed path, or the unexpected-exception path.
- **Gap**: stdout/stderr writes are not asserted (the user only sees these messages).
- **Recommendation (must, M)**: add test_cli_audit_preflight_* cases in test_cli_audit.py. Manage_container=False should be a no-op (one assertion). Failure path should monkeypatch asyncio.run to raise ModelLoadFailed and assert exit code _EXIT_EXTERNAL (3).

---

## 2. Specific unit-test gaps (must-fix)

### 2.1 senex/tools/list_dir.py - none

| Priority | Effort | Test                                                                 |
| -------- | ------ | -------------------------------------------------------------------- |
| must     | S      | empty / "." / explicit relpath all return same shape, repo_root case |
| must     | S      | symlinks ignored (POSIX-only - guard with the existing skip helper)  |
| must     | S      | dirs sorted before files, both alphabetical                          |
| must     | S      | truncated=True when count > max_entries (default 50)                 |
| must     | S      | PathOutsideRepo raised for ../../etc/passwd                          |
| must     | S      | ToolDispatchFailed for missing path / not-a-directory                |
| must     | S      | pydantic extra=forbid on input rejects unknown fields                |

### 2.2 senex/tools/list_symbols.py - none

| Priority | Effort | Test                                                          |
| -------- | ------ | ------------------------------------------------------------- |
| must     | M      | Python AST: top-level functions, methods, classes, async      |
| must     | M      | nested function bodies are skipped (only top-level returned)  |
| must     | S      | line-scan path for one non-Python language (e.g., TypeScript) |
| must     | S      | unknown extension uses fallback regex without crashing        |
| must     | S      | _MAX_FILE_SIZE_BYTES cap raises ToolDispatchFailed            |
| must     | S      | _MAX_SYMBOLS=100 triggers truncated=True                      |
| must     | S      | symlink + path-traversal rejection                            |

### 2.3 senex/tools/run_semgrep.py - none

| Priority | Effort | Test                                                                      |
| -------- | ------ | ------------------------------------------------------------------------- |
| must     | S      | ToolUnavailable raised when shutil.which("semgrep") returns None          |
| must     | S      | rules regex ^[a-zA-Z0-9/_:. -]+$ rejects shell metacharacters             |
| must     | S      | argv list-form: assert shell=False and no metacharacters in any element   |
| must     | M      | semgrep rc=1 with valid JSON is not an error (findings exist case)        |
| must     | S      | semgrep rc!=0 with no JSON raises ToolDispatchFailed                      |
| must     | S      | findings count capped at max_findings (default 20)                        |
| must     | S      | engine-version 3-second sub-timeout silently degrades to "semgrep"        |

### 2.4 senex/skills.py::select_skills - none

The trigger combinatorics here are exactly what fuzzing/parametrize is for. No tests for any of the three trigger types.

| Priority | Effort | Test                                                              |
| -------- | ------ | ----------------------------------------------------------------- |
| must     | S      | path_pattern matches file_path, returns (name, text)              |
| must     | S      | content_pattern matches source                                    |
| must     | S      | graph_fanin: total callers >= min_callers triggers; < does not    |
| must     | S      | graph_fanin no-op when awareness is None or not available         |
| must     | S      | one-trigger-match-per-skill rule (break after first match)        |
| must     | S      | empty skills.toml -> empty list (no crash)                        |
| must     | S      | _load_skill_text cache hit on repeat call                         |

### 2.5 senex/memory.py::MemoryBuffer - none

| Priority | Effort | Test                                                                                                  |
| -------- | ------ | ----------------------------------------------------------------------------------------------------- |
| must     | S      | update() ingests new findings; idempotent on repeat call (seen_ids dedup)                             |
| must     | S      | min_priority="medium" filters out low and healthy                                                     |
| must     | S      | malformed JSON line in findings.partial.jsonl is silently skipped                                     |
| must     | S      | missing partial file -> no-op (no crash)                                                              |
| must     | S      | format_injection() returns None when buffer is empty                                                  |
| must     | S      | format_injection() truncates with "... (additional findings omitted)" when over max_tokens*4 chars    |
| must     | S      | finding_count reflects deduped count                                                                  |
| must     | S      | priority cutoff is inclusive (e.g., medium cutoff includes medium)                                    |

### 2.6 senex/lmstudio_lifecycle.py::HTTPBackend - 0 direct tests

| Priority | Effort | Test                                                                                            |
| -------- | ------ | ----------------------------------------------------------------------------------------------- |
| must     | S      | is_loaded(model_id) returns True when /v1/models lists the id                                   |
| must     | S      | is_loaded returns False when /v1/models raises (no crash)                                       |
| must     | S      | load raises ModelLoadFailed when model id is not in served list (with help msg)                 |
| must     | S      | load returns ModelInfo with backend="http" and synthesized fingerprint                          |
| must     | M      | manage_container=True calls _run_docker("up -d") then _wait_for_health                          |
| must     | M      | _docker_argv argv shape - WSL prefix on, off; with/without env_file                             |
| must     | S      | _docker_argv raises ModelLoadFailed when compose_file missing + manage_container                |
| must     | S      | _run_docker timeout: process killed; ModelLoadFailed raised                                     |
| must     | S      | _wait_for_health polls until 200, or raises after deadline                                      |
| must     | S      | unload is no-op when manage_container=False                                                     |
| must     | S      | unload calls _run_docker("down") when manage_container=True                                     |
| must     | S      | LifecycleBackendFactory.select returns HTTPBackend without probing when manage_container=true   |
| must     | S      | argv list-form / shell=False - assert no metachar in any arg                                    |

### 2.7 senex/cli_audit.py::_ensure_managed_container_up - none

| Priority | Effort | Test                                                                            |
| -------- | ------ | ------------------------------------------------------------------------------- |
| must     | S      | sglang_cfg is None -> returns True without side effects                         |
| must     | S      | manage_container=False -> returns True without side effects                     |
| must     | S      | manage_container=True + success -> returns True; stdout has [sglang] lines      |
| must     | S      | manage_container=True + ModelLoadFailed -> returns False; stderr has reason     |
| must     | S      | manage_container=True + unexpected Exception -> returns False; stderr cleanup   |
| should   | S      | cmd_audit returns _EXIT_EXTERNAL (=3) when this returns False                   |

---

## 3. Integration test gaps

### 3.1 Current state of tests/integration/

Only two files:

- test_tool_loop_with_client.py - ToolLoop + LMStudioClient with respx-mocked LMS (2 test functions).
- test_view_replay.py - replays a recorded events.jsonl (10 test functions).

### 3.2 Missing

| Priority | Effort | Test                                                                                                                                                                        |
| -------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| should   | L      | Docker SGLang boot test marked @pytest.mark.docker (skipped by default; run on --run-docker)                                                                                |
| must     | M      | Headless full-pipeline smoke: preflight -> discover -> 1 file audit -> crosscut -> aggregate, with respx-mocked LMS and a 1-file fixture in tests/fixtures/repos/tiny_python |
| should   | S      | cmd_audit --no-tui end-to-end against the recorded LMS fixture, asserting exit code 0 and findings_index.json shape                                                          |
| should   | M      | cmd_audit --nightly exit-code aggregation (worst-of) with two repos                                                                                                         |
| should   | S      | Nightly: one repo passes, one fails -> exit code is max - currently only unit-tested through dispatcher mocks                                                                |

The Docker test must be opt-in. Use pytest.mark.skipif(shutil.which("docker") is None, reason="docker required") and a session-scoped fixture that runs docker compose up -d against infra/sglang/docker-compose.yml with a tiny model.

---

## 4. Test-quality anti-patterns

Findings from grep-level audit. None are blockers, but the listed items are worth tightening before publication.

| Finding                                                                                                                | Priority | Effort | Notes                                                                                                                                                                                                                                                                                                                              |
| ---------------------------------------------------------------------------------------------------------------------- | -------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Real-time asyncio.sleep of 60s in tests/tui/test_app.py:91 and tests/unit/test_graph_awareness.py:148                  | should   | S      | These look like timeout-fence guards rather than real waits; verify they are inside try/except asyncio.TimeoutError or wrapped in asyncio.wait_for. If not, replace with monkeypatched cancel.                                                                                                                                     |
| await asyncio.sleep(5.0) in test_tool_registry.py:130 and test_tool_gitnexus_query.py:109                              | should   | S      | 5s is enough to slow CI cumulatively. Replace with asyncio.wait_for(..., timeout=5.0) over an asyncio.Event.                                                                                                                                                                                                                      |
| caplog used in 3 test files - likely asserting on log strings                                                          | nice     | S      | Asserting on log messages is brittle. Prefer asserting on event payloads or return values. Audit test_cli_aggregate.py, test_lens_tool_intersection.py, test_tool_grep.py.                                                                                                                                                        |
| Module-scoped fixture in test_secret_redactor.py                                                                       | nice     | -      | Acceptable since SecretRedactor() is stateless; not a real anti-pattern.                                                                                                                                                                                                                                                          |
| Test interdependence                                                                                                   | -        | -      | None observed. Each test creates its own tmp_path; lifecycle tests use an isolated_runlock fixture that monkeypatches RunLock._default_root - this is correct.                                                                                                                                                                    |
| Over-mocking                                                                                                           | should   | -      | test_cli_audit.py mocks _run_single, Lens.load, resolve_config etc. - appropriate for a composition-root test. Lifecycle tests mock the backend Protocol (8 implementations) - appropriate. The pattern is consistent. However the new _ensure_managed_container_up will need similar mocking discipline; do not let it accumulate untyped MagicMocks (use respx for httpx + monkeypatch.setattr on asyncio.create_subprocess_exec). |

---

## 5. CI / GitHub Actions

**Status: NO .github/ directory exists. There is no CI.**

This is the single largest publishability risk in this report. 912 tests passing on the developer laptop today does not prove they pass on a clean machine, on Linux, on Python 3.11 + 3.12, with optional deps absent, etc.

### Recommended workflow (must, M)

Place at .github/workflows/ci.yml:

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        py: ["3.11", "3.12"]
    runs-on: MATRIX_OS
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: MATRIX_PY
          cache: pip
      - run: pip install -e .[dev]
      - run: ruff check senex tests
      - run: mypy senex
      - run: pytest -x --cov=senex --cov-report=xml -n auto
      - uses: codecov/codecov-action@v4
        if: matrix.os == 'ubuntu-latest' && matrix.py == '3.11'

  schema-roundtrip:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e .[dev]
      - run: pytest tests/unit/test_schemas.py -v
      - run: python -c "import senex.events; assert senex.events.ALL_EVENT_TYPES"

  prompt-hashes:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e .[dev]
      - run: pytest tests/unit/test_anchor_loader.py -v
      # Asserts expected_prompt_hashes.json matches the committed prompts.
```

Note: in the real workflow file, replace MATRIX_OS with the GitHub Actions matrix expression for matrix.os and MATRIX_PY for matrix.py (double-curly + matrix.NAME). Literal expression syntax omitted here so this report does not break shell tooling that interpolates curly braces.

### Recommendations

| Priority | Effort | Action                                                                                                       |
| -------- | ------ | ------------------------------------------------------------------------------------------------------------ |
| must     | M      | Create .github/workflows/ci.yml (above) - gate merges on green                                                |
| must     | S      | Add dependabot.yml for pip + actions weekly bumps                                                             |
| should   | S      | Add a release.yml that builds + uploads sdist+wheel to PyPI on tag push                                       |
| should   | S      | Add a coverage-floor check (--cov-fail-under=85) - once the gaps in section 2 are closed                      |
| nice     | S      | Add a docs-build job that renders the markdown in docs/ to confirm no broken links                            |

---

## 6. Pre-commit hooks

**Status: no .pre-commit-config.yaml exists.**

### Recommended config (must, S)

Place at .pre-commit-config.yaml:

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: check-merge-conflict
      - id: detect-private-key

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        additional_dependencies: [pydantic, types-tomli]
        args: [--strict, senex]

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ["--baseline", ".secrets.baseline"]

  - repo: local
    hooks:
      - id: events-schema-roundtrip
        name: Events schema is in sync with senex.events
        entry: pytest tests/unit/test_schemas.py::test_events_schema_has_one_def_per_event_class -q
        language: system
        pass_filenames: false
        files: ^(senex/events\.py|senex/schema/events\.schema\.json)$

      - id: prompt-hash-check
        name: Prompt hashes are in sync
        entry: pytest tests/unit/test_anchor_loader.py -q
        language: system
        pass_filenames: false
        files: ^senex/prompts/.*\.md$
```

The local hooks are the load-bearing ones - they prevent the most common silent-drift failure modes: a new event class without a $def, a prompt edited without rehashing.

| Priority | Effort | Action                                                              |
| -------- | ------ | ------------------------------------------------------------------- |
| must     | S      | Create .pre-commit-config.yaml (above)                              |
| must     | S      | Run detect-secrets scan > .secrets.baseline once and commit         |
| should   | S      | Add pre-commit install to the scripts/setup.ps1 flow                |

---

## 7. Test-data fixtures health

### 7.1 tests/fixtures/ inventory

| Path                            | Files                                                | Health                                                                                                                                                |
| ------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| events/                         | 2 (recorded_run.jsonl, recorded_run_truncated.jsonl) | OK; recorded_run.jsonl has 6 lines covering RunStart -> FileStart x2 -> FileComplete x2 -> RunComplete                                                 |
| lms_responses/                  | 3 (example.json, simple_audit.json, sha256-keyed)    | OK; respx-replay infrastructure in tests/recorded/conftest.py is sound                                                                                |
| gitnexus_outputs/               | 4 sample fixtures                                    | OK                                                                                                                                                    |
| repos/tiny_python/              | 1 toy repo                                           | OK; conftest.py auto-materializes .git/HEAD                                                                                                           |
| expected_prompt_hashes.json     | -                                                    | **stale risk**: 10 hashes for 10 prompts, but no test asserts there are no orphan entries (e.g., a deleted prompt whose hash is still in the file)    |

### 7.2 expected_prompt_hashes.json review

- Schema is sound: algorithm: sha256, encoding: utf-8, newline: lf, schema_version: 1.
- The 10 mapped prompts match the 10 files in senex/prompts/. Two prompts (compaction.md, cross_cutting.md) were touched in commit 709a2e9 - hashes were updated.
- **Gap**: no test_no_orphan_prompt_hashes - a contributor could delete a prompt file and forget to remove its entry, causing silent drift.
- **Recommendation (should, S)**: extend tests/unit/test_anchor_loader.py with test_no_orphan_hashes() that asserts every key in expected_prompt_hashes.json::prompts corresponds to a file under senex/prompts/.

### 7.3 events.schema.json review

- 1661 lines, 47 event types. oneOf discriminator on type field - correct shape.
- test_events_schema_has_one_def_per_event_class enforces the events.py-to-schema sync rule. **This is the load-bearing safety net for the entire event system.**
- **Gap**: the schema has no additionalProperties: false at the per-event level (verify by inspection if needed). Without it, a typo in a field name will be silently accepted by jsonschema validators, defeating the purpose of the schema.
- **Recommendation (should, M)**: audit each $def in events.schema.json and add additionalProperties: false. Add a meta-test that loops over schema $defs and asserts each has additionalProperties: False.

### 7.4 Recorded LMS fixtures

- The recorded_lms fixture in tests/recorded/conftest.py is well-designed: SHA256 of canonical request -> fixture file. Capture mode (RECORD_LMS=1) is gated to loopback http only. Good defense.
- **Gap**: only 3 fixtures captured. After the SGLang migration the recorded transcripts may no longer match SGLang response shape (token IDs, finish reasons, tool_call deltas).
- **Recommendation (must, S)**: re-capture simple_audit.json and example.json against an SGLang container, commit, and add a small README in tests/fixtures/lms_responses/ documenting the source server.

---

## 8. Fuzzing / property-based testing

hypothesis is in [project.optional-dependencies].dev but a recursive grep for hypothesis and @given returns zero matches across the entire test tree.

The library is a dev-dep but unused. This is a missed opportunity given how many places in senex have well-defined input grammars.

### High-value Hypothesis targets

| Target                                              | Property                                                                                                                                                                                                  | Priority | Effort |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ------ |
| senex/secret_redactor.py::SecretRedactor.redact     | For any input s, the output never contains the literal substring of any seeded secret pattern from the redactor pattern set. (Find adversarial inputs that bypass the regex.)                              | should   | M      |
| senex/secret_redactor.py::SecretRedactor.redact_dict | Idempotent: redact_dict(redact_dict(d)) == redact_dict(d).                                                                                                                                                 | should   | S      |
| senex/walker.py::Walker.walk                        | For any synthesized directory tree (Hypothesis recursive strategy), no path returned escapes the repo root, no symlink target is dereferenced, no .gitignored path is included.                            | must     | M      |
| senex/lmstudio_lifecycle.py::validate_model_id      | For any string drawn from ^[A-Za-z0-9_./-]+$ plus .. segments, accepts iff the regex matches AND no .. segment is present. (This is partly there with parametrize; Hypothesis would broaden it.)         | nice     | S      |
| senex/schema/events.schema.json validator           | For any BaseEvent subclass instance built via Hypothesis pydantic strategy, events.schema.json validates the JSON-serialized form. (Round-trip property.)                                                  | should   | M      |
| senex/tools/safety.py::validate_repo_path           | For any path string, output is always inside repo_root OR raises PathOutsideRepo/SymlinkRefused - no third outcome.                                                                                       | should   | S      |
| senex/skills.py::select_skills                      | Trigger combinations: model the skills.toml config space and assert the break-after-first-match rule holds.                                                                                              | nice     | M      |
| senex/memory.py::MemoryBuffer.update                | Idempotency: calling update(audit_dir) twice yields identical _seen_ids and _lines.                                                                                                                       | should   | S      |

### Recommendation

| Priority | Effort | Action                                                                                                                                                                  |
| -------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| should   | M      | Add tests/property/test_redactor_properties.py, test_walker_properties.py, test_validate_repo_path_properties.py - the three highest-leverage targets.                  |
| nice     | S      | Add a CI job that runs property tests with a higher max_examples (e.g., 500) on a nightly schedule, separate from PR CI (which uses the default 100).                   |

---

## 9. Summary scorecard

| Area                                                            | State                                                                                  | Risk                                                                          |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Existing unit suite quality                                     | Good - clean fixtures, no interdependence, asyncio used correctly                      | Low                                                                           |
| New tools (list_dir, list_symbols, run_semgrep)                 | **Untested** - registered but no behavioral coverage                                   | **High**                                                                      |
| skills.py / memory.py                                           | **Untested**                                                                           | **High**                                                                      |
| HTTPBackend + manage_container                                  | **Untested**                                                                           | **High**                                                                      |
| _ensure_managed_container_up                                    | **Untested**                                                                           | **High**                                                                      |
| Wizard ctx auto-detect / single-model fast path / backend label | **Untested**                                                                           | Medium                                                                        |
| Lifecycle (existing)                                            | Comprehensive (60+ cases, all error branches)                                          | Low                                                                           |
| TUI widgets                                                     | Comprehensive                                                                          | Low                                                                           |
| Schema sync                                                     | Enforced by 1 test (test_events_schema_has_one_def_per_event_class)                    | Medium - additionalProperties: false missing on $defs                          |
| Prompt-hash sync                                                | Enforced by test_anchor_loader.py                                                      | Low - but no orphan check                                                      |
| Integration tests                                               | Minimal - replay only; no full pipeline; no Docker                                     | **High**                                                                      |
| CI / GitHub Actions                                             | **Absent**                                                                             | **Critical**                                                                  |
| Pre-commit hooks                                                | **Absent**                                                                             | High                                                                          |
| Property-based testing                                          | Hypothesis is a dev-dep but unused                                                     | Medium                                                                        |
| Fixtures                                                        | Healthy; SGLang-era LMS replays may need re-capture                                    | Medium                                                                        |

### Top-5 publishability blockers (in order)

1. **No CI.** Add .github/workflows/ci.yml. (must, M)
2. **HTTPBackend + _ensure_managed_container_up untested.** Two new code paths gate every audit. (must, M)
3. **Three new tools untested.** list_dir, list_symbols, run_semgrep. (must, M each -> ~L total)
4. **No pre-commit.** Add .pre-commit-config.yaml with the events-schema-roundtrip and prompt-hash local hooks. (must, S)
5. **skills.py + memory.py untested.** Trigger logic and rolling-buffer idempotency are silent-failure-prone. (must, S each)

### Aggregate effort

- **must** items: ~14 days of dedicated test-writing + ~half a day each for CI / pre-commit setup.
- **should** items: ~5 days additional.
- **nice** items: ~2 days additional.

Total to reach a publishable QA posture: **~3 working weeks** of focused effort. The existing 912-test suite is high-quality; the gaps are concentrated in the seven PRs merged today.
