# Test Automation Plan — senex

**Author:** test-automator (read-only review)
**Date:** 2026-04-29
**Scope:** Read-only review of the senex repo at `E:\senex` ahead of v1.x publishability work. Recommends CI, pre-commit, coverage, missing test surface for newly-added modules, integration tests, property-based tests, mutation testing, performance benchmarks, schema-pinning, and a smoke script.

> **No CI workflow files are written by this plan.** The YAML below is a recommendation for the next wave of work. All sources of truth live in `pyproject.toml`, `senex/`, and `tests/` as they do today.

---

## 0. Current state

| Area | Status |
|---|---|
| Tests | 912 unit + TUI tests, 5 skipped, organized under `tests/{unit,tui,integration,golden,recorded,fixtures}/` |
| Runner | pytest with `pytest-asyncio` (asyncio_mode=auto), `respx` for HTTP mocking, `hypothesis` listed in dev deps but unused |
| CI | None — no `.github/workflows/` directory exists |
| Pre-commit | None — no `.pre-commit-config.yaml` |
| Coverage | None — `pytest-cov` listed in dev deps but no config or threshold |
| Lint | `ruff` configured (line-length=100, target=py311) |
| Type-check | `mypy` strict configured for python_version=3.11 |
| Schema-pinning | Prompt SHA-256 hashes pinned at `tests/fixtures/expected_prompt_hashes.json` (10 prompts) |
| Modules added today, **no tests yet** | `senex/skills.py`, `senex/memory.py`, `senex/tools/list_dir.py`, `senex/tools/list_symbols.py`, `senex/tools/run_semgrep.py`, `senex/lmstudio_lifecycle.py::HTTPBackend`, `senex/cli_audit.py::_ensure_managed_container_up` |

Priority/effort legend:
- **Priority:** must / should / nice
- **Effort:** S (≤ 1 day), M (1–3 days), L (3+ days)

---

## 1. CI workflow recommendation

**Priority: must — Effort: M**

Recommended path: `.github/workflows/ci.yml`. Five jobs, three of them gated. Matrix is OS x Python; the integration job is single-OS (Ubuntu) because it needs Docker.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

# Cancel in-flight runs for the same ref on a new push.
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  # ------------------------------------------------------------------
  # 1. Lint — ruff + mypy strict on senex/
  # ------------------------------------------------------------------
  lint:
    name: lint (ruff + mypy)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install dev deps
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: ruff check
        run: ruff check senex tests
      - name: ruff format --check
        run: ruff format --check senex tests
      - name: mypy strict (senex/)
        run: mypy senex

  # ------------------------------------------------------------------
  # 2. Unit + TUI tests — full matrix
  # ------------------------------------------------------------------
  unit-tests:
    name: unit (${{ matrix.os }} / py${{ matrix.python }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
          cache: pip
      - name: Cache HF model artifacts (optional pulls)
        uses: actions/cache@v4
        with:
          path: ~/.cache/huggingface
          key: hf-${{ runner.os }}-${{ hashFiles('pyproject.toml') }}
          restore-keys: |
            hf-${{ runner.os }}-
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Run unit + TUI + golden tests with coverage
        env:
          # Optional — populated only when org secret is set; never required.
          HF_TOKEN: ${{ secrets.HF_TOKEN }}
        run: |
          pytest tests/unit tests/tui tests/golden tests/recorded \
            --cov=senex --cov-branch \
            --cov-report=xml --cov-report=term \
            -m "not integration"
      - name: Upload coverage (Linux + py3.12 only)
        if: matrix.os == 'ubuntu-latest' && matrix.python == '3.12'
        uses: actions/upload-artifact@v4
        with:
          name: coverage-xml
          path: coverage.xml

  # ------------------------------------------------------------------
  # 3. Integration tests — gated on Docker availability
  # ------------------------------------------------------------------
  integration-tests:
    name: integration (Docker)
    runs-on: ubuntu-latest
    # Skip on draft PRs to save runner minutes.
    if: github.event.pull_request.draft != true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
      - name: Verify Docker available
        run: docker version
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Run integration tests
        env:
          SENEX_INTEGRATION: "1"
          HF_TOKEN: ${{ secrets.HF_TOKEN }}
        run: pytest tests/integration -m "integration or not slow"

  # ------------------------------------------------------------------
  # 4. Build — sdist + wheel
  # ------------------------------------------------------------------
  build:
    name: build (sdist + wheel)
    runs-on: ubuntu-latest
    needs: [lint, unit-tests]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: |
          python -m pip install --upgrade pip build
          python -m build
      - name: Verify metadata
        run: |
          pip install twine
          twine check dist/*
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  # ------------------------------------------------------------------
  # 5. Schema / hash verify — pin guards
  # ------------------------------------------------------------------
  schema-hash-verify:
    name: schema + prompt hash verify
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install
        run: pip install -e ".[dev]"
      - name: Verify pinned prompt hashes
        run: pytest tests/unit/test_schemas.py tests/unit/test_config_example_in_sync.py -v
      - name: Validate JSON schemas parse
        run: python -c "import json,glob; [json.load(open(p,encoding='utf-8')) for p in glob.glob('senex/schema/*.json')]"
```

Notes:
- **Matrix justification.** The repo declares `requires-python = ">=3.11"` so all three are in scope. Windows is non-negotiable because `_ensure_managed_container_up` and `lmstudio_client` ship a `via_wsl` code path tested on Windows hosts.
- **Caching.** `actions/setup-python@v5` with `cache: pip` is the cheapest pip cache available. `actions/setup-node@v4` adds the npm cache for the integration job that runs `npx gitnexus`. The HF cache is conditional — only used when `HF_TOKEN` is set; if it never hits the cache key still costs nothing.
- **HF_TOKEN.** Optional org secret. Tests that pull HF artifacts must `skip` when `HF_TOKEN` is unset (this discipline does not exist today; see §4.6).
- **Marker discipline.** Today no `pytest.mark.integration` exists. Add one in `pyproject.toml` `[tool.pytest.ini_options].markers` and gate `tests/integration/` behind it. The unit job runs `-m "not integration"`.

---

## 2. Pre-commit recommendation

**Priority: should — Effort: S**

Recommended path: `.pre-commit-config.yaml`. Plus two custom local hooks for prompt-hash and events-schema validation.

```yaml
default_language_version:
  python: python3.12

repos:
  # ---- Whitespace / structural ----
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
        exclude: '\.md$'  # markdown trailing-space sometimes meaningful
      - id: check-toml
      - id: check-yaml
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: check-merge-conflict
      - id: mixed-line-ending
        args: [--fix=lf]
        exclude: '\.bat$|setup\.ps1$'

  # ---- Lint + format ----
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.7.4
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
      - id: ruff-format

  # ---- Type check (strict on senex/, not tests/) ----
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy
        args: [--strict, senex]
        # Pull in runtime deps so mypy sees the real types.
        additional_dependencies:
          - "pydantic>=2.5,<3"
          - "openai>=1.50,<2.0"
          - "tiktoken"
          - "httpx"
          - "jsonschema>=4.20"
          - "regex"
          - "tomli-w"
          - "ulid-py"
          - "pathspec>=0.12.0"
          - "portalocker"
          - "textual>=0.50"
          - "detect-secrets"
        files: '^senex/'

  # ---- Custom local hooks ----
  - repo: local
    hooks:
      # Recompute SHA-256 of every senex/prompts/*.md and compare to
      # tests/fixtures/expected_prompt_hashes.json. Fails if a prompt was
      # modified without updating the pin. The pinned file already exists
      # at tests/fixtures/expected_prompt_hashes.json.
      - id: prompt-hash-verify
        name: verify pinned prompt hashes
        entry: python scripts/verify_prompt_hashes.py
        language: system
        pass_filenames: false
        files: '^(senex/prompts/.*\.md|tests/fixtures/expected_prompt_hashes\.json)$'

      # Validate that every entry in senex.events.ALL_EVENT_TYPES has a
      # matching $defs definition in senex/schema/events.schema.json.
      - id: events-schema-validate
        name: validate events.schema.json coverage
        entry: python scripts/verify_events_schema.py
        language: system
        pass_filenames: false
        files: '^(senex/events\.py|senex/schema/events\.schema\.json)$'
```

Two new scripts to add (deferred to next wave):
- `scripts/verify_prompt_hashes.py` — already implementable as a thin wrapper around the existing `tests/unit/test_schemas.py` logic that reads `expected_prompt_hashes.json`.
- `scripts/verify_events_schema.py` — see §9.

---

## 3. Coverage strategy

**Priority: should — Effort: S**

| Knob | Recommended value | Rationale |
|---|---|---|
| Tool | `pytest-cov` + `coverage.py` (already in dev deps) | No new dep |
| Line target | **85%** | The codebase is heavily mock-driven and every public phase has unit tests; 85% is the floor for "no untested branch shipped" |
| Branch target | **70%** | Async + many `try/except` sites — 70% is realistic and forces conditional coverage without making `if not bus: ...` style guards painful |
| Fail under | line=85, branch=70, configured in `[tool.coverage.report]` |
| Exclusions | `tests/`, `senex/__main__.py`, generated schemas, `senex/tui/launcher.py` (interactive), `senex/handoff.py` (mostly Pydantic models) — see below |

Add to `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["senex"]
branch = true
omit = [
  "senex/__main__.py",        # entrypoint shim
  "senex/tui/launcher.py",    # interactive bootstrap
  "tests/*",
  "**/__init__.py",
]

[tool.coverage.report]
fail_under = 85
show_missing = true
skip_covered = false
exclude_lines = [
  "pragma: no cover",
  "raise NotImplementedError",
  "if TYPE_CHECKING:",
  "if __name__ == .__main__.:",
  "\\.\\.\\.",                # protocol method bodies
  "@(abc\\.)?abstractmethod",
]

[tool.coverage.paths]
source = ["senex/", "*/site-packages/senex/"]
```

Branch-target separately validated with `coverage report --fail-under=70 --include='*' -m`. **Do not gate the unit-tests CI job on coverage on day 1**: collect for two weeks, then ratchet up. See §11.

---

## 4. Test framework gaps — modules added today

Each new module needs a dedicated unit test file at `tests/unit/test_<module>.py`. Cell counts assume mock fixtures only (no live LM Studio, no Docker).

### 4.1 `senex/skills.py`

**Priority: must — Effort: S**

The module exposes `select_skills(file_path, source, awareness, lens_dir)` and two `lru_cache`-wrapped loaders. Required tests:

| Case | Trigger type | Verifies |
|---|---|---|
| `path_pattern` matches | `path_pattern` | re.search hit on file path → skill returned |
| `path_pattern` miss | `path_pattern` | no match → skill omitted |
| `content_pattern` matches | `content_pattern` | regex against `source` text |
| `content_pattern` miss | `content_pattern` | source has no match → skill omitted |
| `graph_fanin` >= threshold | `graph_fanin` | `awareness.callers_d1_count` summed >= `min_callers` |
| `graph_fanin` below threshold | `graph_fanin` | sum < threshold → omitted |
| `graph_fanin` with awareness=None | `graph_fanin` | gracefully omitted (do not raise) |
| `graph_fanin` with `awareness.available=False` | `graph_fanin` | omitted |
| Multi-trigger skill — first hit wins, no double-add | any | `break` short-circuit |
| Two skills, both match — both returned in TOML order | any | order preservation |
| Empty `skills.toml` | — | returns `[]` |
| Missing `skills.toml` | — | returns `[]` (no exception) |
| **Cache behavior — `_load_skills_cfg`** | — | second call with same `skills_dir` does not re-read from disk (mock `tomllib.load`, assert call_count==1) |
| **Cache behavior — `_load_skill_text`** | — | repeated `(skills_dir, filename)` loads serve from cache |
| Returned tuple shape | — | `(name: str, text: str)` per skill |

Suggested fixtures:
- `tests/fixtures/lenses/test_skills/skills.toml` with three skills (one per trigger type).
- A graph stub: `class _StubGraph: available=True; callers_d1_count={"a.b": 5}`.

**Risk if not done:** the skill-injection path is invisible to the audit prompt and any regex/threshold typo silently degrades audit quality.

### 4.2 `senex/memory.py`

**Priority: must — Effort: S**

`MemoryBuffer.update / format_injection` is unbounded-input file I/O with priority + dedup logic. Required tests:

| Case | Verifies |
|---|---|
| Empty / missing `findings.partial.jsonl` | `update` is no-op; `format_injection() is None` |
| Single high-priority finding | `update` adds 1; `format_injection()` includes `[HIGH]` line + `_HEADER` |
| Idempotent re-`update` | second call on unchanged file does not double-count (`finding_count` stays the same) |
| `min_priority="medium"` filters out `low` and `healthy` | priority filter works |
| `min_priority="high"` filters out `medium` | priority filter works |
| Garbage JSON line (single bad line in middle of file) | skipped; surrounding lines still ingested |
| Missing `id` field | record not deduped (since `id == ""` → first wins, repeats skipped) — match observed behavior |
| Token cap respected | `max_tokens=10` (40 chars) → `format_injection()` ends with `... (additional findings omitted)\n` |
| Token cap exact-fit | budget exactly fits N lines → no omission marker |
| Header always included when buffer non-empty | `_HEADER` is the prefix |
| `format_injection()` returns `None` on empty buffer | default state |
| Priority ordering preserved (insertion order, not re-sorted) | matches code |
| Findings with missing `location.line_start` | renders as `?` |
| **Token budget = 0** | edge: nothing fits except possibly the header — verify expected behavior, document if buggy |

Property tests for dedup + cap → see §6.

### 4.3 `senex/tools/list_dir.py`

**Priority: must — Effort: S**

| Case | Verifies |
|---|---|
| Empty `relpath` returns repo_root listing | normalization branch |
| `relpath="."` returns repo_root listing | normalization branch |
| Relpath outside repo → `PathOutsideRepo` | safety |
| Symlink at relpath itself → `SymlinkRefused` | safety |
| Symlink children skipped | safety |
| Output sorted: dirs first, then files, both alpha-case-insensitive | stable order |
| `truncated=True` when len > `max_entries` | cap |
| `truncated=False` when len ≤ `max_entries` | cap |
| Non-existent relpath → `ToolDispatchFailed("directory not found")` | dispatch failure |
| Relpath is a file → `ToolDispatchFailed("not a directory")` | dispatch failure |
| File `size_bytes` populated; dir `size_bytes` is None | DirEntry shape |
| `OSError` on `iterdir` → `ToolDispatchFailed("cannot list directory")` | error wrap |
| Per-child `OSError` (e.g. is_dir raise) → child skipped, others returned | resilience |
| `ListDirInput` rejects extra keys | pydantic strict |
| `max_entries` bounds: 1 valid, 200 valid, 0 rejected, 201 rejected | pydantic Field |

Use `tmp_path` for filesystem fixtures, plus `os.symlink` (gated on `not sys.platform.startswith("win")` or use `Path.symlink_to` with admin fallback skip).

### 4.4 `senex/tools/list_symbols.py`

**Priority: must — Effort: M**

The module is large (~540 LOC) with per-language regex tables. Tests should be parametrized.

| Case | Verifies |
|---|---|
| **Python** — top-level `def` extracted | AST path, exact line range |
| **Python** — class with method extracts both | class kind + `Cls.method` name |
| **Python** — async fn / async method classify as `async_function`/`async_method` | kind |
| **Python** — nested fn skipped | `_in_function` guard |
| **Python** — SyntaxError → empty list | resilience |
| **Python** — full signature including pos-only, kwonly, defaults, `*args`, `**kwargs`, return annotation | `_format_python_sig` correctness |
| **Python** — sig truncated to 200 chars | length cap |
| **TypeScript** — `export class Foo`, `export function bar()` extracted | line-scan regex |
| **Go** — `func Foo()`, `type Bar struct`, `type Baz interface` | line-scan |
| **Rust** — `pub async fn foo`, `impl Foo`, `trait Bar` | line-scan |
| **Java** — class with method | line-scan |
| **C#** / **Kotlin** / **Ruby** / **Swift** / **C++** / **PHP** / **shell** / **PowerShell** — one canonical sample each | line-scan |
| **Brace-balanced end-line detection** for braced langs | `_find_brace_end` |
| **Ruby `end`-keyword** matching | `_find_ruby_end` |
| Unknown extension uses `_GENERIC_PATTERN` | fallback |
| `_NOISE_NAMES` filtered (`if`, `for`, etc. not returned as symbols) | noise filter |
| Symbol cap at 100 — `truncated=True` | cap |
| File over 5MB → `ToolDispatchFailed("file too large")` | size guard |
| Path-safety: traversal → `PathOutsideRepo` | shared with §4.3 |
| Symlink → `SymlinkRefused` | shared with §4.3 |
| Non-existent file → `ToolDispatchFailed("file not found")` | dispatch failure |
| Directory → `ToolDispatchFailed("not a regular file")` | dispatch failure |
| Dedup by `start_line` (multiple patterns matching same line → one entry) | dedup loop |

Use a `parametrize` table with `(lang, source_snippet, expected_symbols_min)` to compress.

### 4.5 `senex/tools/run_semgrep.py`

**Priority: should — Effort: S**

Must mock `asyncio.create_subprocess_exec` and `shutil.which`. Required tests:

| Case | Verifies |
|---|---|
| `RunSemgrepInput.rules` rejects shell metacharacters via field_validator | injection guard |
| Belt-and-suspenders re-check raises `ToolDispatchFailed` if pydantic somehow let it through (test the handler with input that bypasses field_validator via `model_construct`) | defense in depth |
| `shutil.which("semgrep") is None` → `ToolUnavailable` | unavailable path |
| Path safety: traversal → `PathOutsideRepo` | shared |
| Path safety: symlink → `SymlinkRefused` | shared |
| Non-existent file → `ToolDispatchFailed` | dispatch failure |
| Non-file → `ToolDispatchFailed` | dispatch failure |
| Argv list-form: assert exact `argv == ["semgrep", "--json", "--quiet", "--config", rules, str(resolved)]` | SEC-4 |
| `shell=True` is **never** passed to `create_subprocess_exec` | SEC-4 |
| Non-zero rc + valid JSON in stdout → findings parsed (semgrep exit 1 on findings) | exit-code handling |
| Non-zero rc + no JSON → `ToolDispatchFailed(stderr[:300])` | exit-code handling |
| Empty stdout, rc=0 → empty findings | benign-empty path |
| `_parse_findings` accepts `results` list with mixed-shape entries | resilience |
| `_parse_findings` raises `ToolDispatchFailed("unexpected semgrep output format")` when key missing | format guard |
| `max_findings` cap with `truncated=True` flag | cap |
| Findings sorted by `line` ascending | determinism |
| `engine` populated from `_get_semgrep_version` | metadata |
| `_get_semgrep_version` 3s timeout → `"semgrep"` fallback | best-effort |
| Tool timeout via `asyncio.wait_for` raises `TimeoutError` (re-raises after kill) | timeout |

`pytest.MonkeyPatch` to substitute `asyncio.create_subprocess_exec` with an async stub returning canned stdout/stderr/rc.

### 4.6 `senex/lmstudio_lifecycle.py::HTTPBackend`

**Priority: must — Effort: M**

Two operating modes (`manage_container=False` vs `True`). Use `respx` to mock `GET /v1/models` and a subprocess stub for `_run_docker`.

#### Mode A: `manage_container=False`

| Case | Verifies |
|---|---|
| `is_loaded(model_id)` returns True when model in `data` array | happy path |
| `is_loaded` returns False when model not in list | normal miss |
| `is_loaded` returns False on HTTP error (e.g. connect refused) | resilience |
| `is_loaded` rejects invalid model_id via `validate_model_id` | shared validator |
| `load(model_id, timeout)` returns `ModelInfo` with `quant="auto"`, `digest=created`, `backend="http"`, fingerprint computed | happy path |
| `load` raises `ModelLoadFailed` listing available ids when model not served | informative error |
| `unload` is a no-op when `manage_container=False` (no docker subprocess invoked) | spec invariant |
| `list_loaded` returns one `ModelInfo` per `data` record | listing |

#### Mode B: `manage_container=True`

| Case | Verifies |
|---|---|
| `_docker_argv("up -d")` builds `[wsl, -d, Ubuntu, -e, docker, compose, -f, <file>, --env-file, <file>, up, -d]` when `via_wsl=True` | argv form (no shell) |
| `_docker_argv("down")` same with `down` | action arg |
| `_docker_argv` without `via_wsl` produces bare `[docker, compose, ...]` | branch |
| `_docker_argv` raises `ModelLoadFailed("manage_container=true but compose_file is not set")` when compose_file is None | guard |
| `compose_file` and `env_file` containing shell metacharacters (`; rm -rf /`) flow through as literal argv elements (do not get interpolated) | SEC-4 / no-shell invariant |
| `_run_docker("up -d", timeout=30)` rc=0 → returns | happy path |
| `_run_docker("up -d", timeout=30)` rc=1 → `ModelLoadFailed` with `(stderr[:1000])` | error |
| `_run_docker` timeout → `ModelLoadFailed("docker compose ... timed out")` | timeout |
| `load` calls `_run_docker("up -d")` then `_wait_for_health` then `_list_models` | sequence |
| `_wait_for_health` polls until 200, then returns | happy path |
| `_wait_for_health` times out → `ModelLoadFailed("inference server did not become healthy")` | timeout |
| `unload` calls `_run_docker("down", timeout=60)` | shutdown path |
| `unload` swallows nothing — if `_run_docker` raises, propagate | error path |

Use `respx.mock(base_url=...)` and a stub for `asyncio.create_subprocess_exec` returning `(b"", b"", 0)`.

**Critical safety test:** assert `compose_file = "; touch /tmp/pwn"` does NOT result in a file being written (i.e., the literal `; touch /tmp/pwn` appears as an argv element of the docker call, not a shell command). This is the SEC-4 fail-closed invariant for `_docker_argv`.

### 4.7 `senex/cli_audit.py::_ensure_managed_container_up`

**Priority: should — Effort: M (integration), S (unit)**

Two flavors:

**Unit test (mock-driven):**

| Case | Verifies |
|---|---|
| `sglang_cfg is None` → returns True (no-op) | early exit |
| `sglang_cfg.manage_container=False` → returns True (no-op) | early exit |
| `manage_container=True`, `_run_docker` succeeds, `_wait_for_health` succeeds → True + stdout messages emitted | happy path |
| `_run_docker` raises `ModelLoadFailed` → False + stderr message | failure path |
| `_run_docker` raises unexpected exception → False + "unexpected error" message | failure path |

Mock `HTTPBackend` instance methods directly with `monkeypatch`.

**Integration test (Docker-gated, see §5):** end-to-end SGLang container start + audit + stop.

---

## 5. Integration tests

**Priority: should — Effort: L**

All gated behind `pytest.mark.integration` + env var `SENEX_INTEGRATION=1` so they never run in the unit-tests job.

### 5.1 End-to-end audit of fixture repo with known findings

**Priority: must — Effort: M**

The repo currently has `tests/fixtures/repos/tiny_python/` with five files (README, healthy, io_helper, main, util) and no asserted findings. Recommendation: add a deliberately-vulnerable fixture inspired by today's `E:/senex-test/src/auth.py`.

Layout:
```
tests/fixtures/repos/known_findings/
  .git/HEAD                   # placeholder — handled by conftest auto-bootstrap
  src/auth.py                 # known: hardcoded secret, SQL injection, weak hash
  src/safe.py                 # known healthy
  expected_findings.json      # canonical asserted findings (priority + CWE)
```

Test:
```python
@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("SENEX_INTEGRATION"), reason="set SENEX_INTEGRATION=1")
async def test_e2e_audit_known_findings(tmp_path):
    # Run senex audit against the fixture repo with a stubbed/replayed LMS.
    # Assert at least one [high] finding per CWE class in expected_findings.json.
```

LMS client must be replayed from `tests/fixtures/lms_responses/` (the directory already exists). When `SENEX_INTEGRATION=1` is unset, this test is skipped.

### 5.2 Resume-from-checkpoint test

**Priority: should — Effort: M**

Two-phase: run an audit, kill it after N files, then resume — assert remaining files complete and final `findings.json` contains both halves. This exercises `senex.checkpoint` + `senex.runlock` + `Lifecycle.acquire_for_resume`.

### 5.3 SGLang container start/stop test

**Priority: nice — Effort: L**

Docker-gated. Uses a tiny model (or a stub OpenAI-compatible server in a container) so the test runs in <2 min.

```python
@pytest.mark.integration
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker required")
async def test_managed_container_lifecycle(tmp_path, sglang_compose_fixture):
    # 1. config with manage_container=true
    # 2. _ensure_managed_container_up returns True
    # 3. GET /v1/models returns >= 1 entry
    # 4. backend.unload calls down, container stops
    # 5. GET /v1/models now connection-refused
```

---

## 6. Property-based tests (Hypothesis)

**Priority: should — Effort: M**

`hypothesis` is already in dev deps. Three high-value targets, all with deterministic invariants.

### 6.1 `SecretRedactor`

```python
@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=2000))
def test_redactor_idempotent(s):
    r = SecretRedactor()
    once = r.redact(s)
    twice = r.redact(once)
    assert once == twice  # post-redaction text never re-triggers a pattern

@given(st.sampled_from(KNOWN_SECRET_CORPUS))
def test_redactor_known_secrets_always_masked(secret):
    r = SecretRedactor()
    out = r.redact(f"prefix {secret} suffix")
    assert "[REDACTED:" in out
    assert secret not in out

@given(st.text(min_size=1, max_size=200))
def test_redactor_never_introduces_unredacted_marker(s):
    out = SecretRedactor().redact(s)
    # no half-mask like "[REDACTED:" without closing "]"
    assert out.count("[REDACTED:") == out.count("]") - max(0, out.count("]") - out.count("[REDACTED:"))
```

`KNOWN_SECRET_CORPUS` should include real-shaped (but synthetic) examples for each pattern: PEM, JWT, AWS access key, GitHub PAT, OpenAI/Anthropic key, generic env style.

### 6.2 `validate_repo_path`

```python
TRAVERSAL_CORPUS = [
    "../etc/passwd", "../../etc/passwd", "..\\..\\Windows\\System32",
    "/etc/passwd", "C:\\Windows", "subdir/../../../etc/passwd",
    "%2e%2e/etc/passwd",  # already-decoded by pathlib? — verify behavior
    "subdir/legit/../../../escape", "....//etc/passwd",
    "\\\\?\\C:\\Windows", "\\\\server\\share\\file",
    "subdir/.\\..\\..\\escape",
]

@pytest.mark.parametrize("p", TRAVERSAL_CORPUS)
def test_validate_repo_path_rejects_traversal(tmp_path, p):
    with pytest.raises(PathOutsideRepo):
        validate_repo_path(p, tmp_path)

@given(path_strategy)  # st.text() with path-like alphabet
def test_validate_repo_path_never_returns_outside(tmp_path, p):
    assume(p)
    try:
        out = validate_repo_path(p, tmp_path)
    except (PathOutsideRepo, SymlinkRefused):
        return
    assert out.resolve().is_relative_to(tmp_path.resolve())
```

### 6.3 `MemoryBuffer`

```python
finding_strategy = st.fixed_dictionaries({
    "id": st.text(min_size=1, max_size=20),
    "priority": st.sampled_from(["high", "medium", "low", "healthy"]),
    "title": st.text(max_size=80),
    "file": st.text(min_size=1, max_size=40),
    "location": st.fixed_dictionaries({"line_start": st.integers(min_value=1, max_value=10000)}),
})

@given(st.lists(finding_strategy, min_size=0, max_size=50))
def test_memory_buffer_dedup_by_id(findings, tmp_path):
    # Idempotency invariant: update twice = update once.
    buf = MemoryBuffer()
    partial = tmp_path / "findings.partial.jsonl"
    partial.write_text("\n".join(json.dumps(f) for f in findings), encoding="utf-8")
    buf.update(tmp_path)
    n1 = buf.finding_count
    buf.update(tmp_path)
    assert buf.finding_count == n1

@given(st.lists(finding_strategy, min_size=1, max_size=200), st.integers(min_value=10, max_value=2000))
def test_memory_buffer_token_cap_respected(findings, max_tokens, tmp_path):
    buf = MemoryBuffer(max_tokens=max_tokens, min_priority="low")
    partial = tmp_path / "findings.partial.jsonl"
    partial.write_text("\n".join(json.dumps(f) for f in findings), encoding="utf-8")
    buf.update(tmp_path)
    out = buf.format_injection()
    if out is not None:
        assert len(out) <= max_tokens * 4 + 50  # +50 for omission marker slack
```

---

## 7. Mutation testing

**Priority: nice — Effort: L**

**Recommendation: defer to v1.1.x. Do not adopt mutmut/cosmic-ray on day 1.**

Rationale:
- The codebase has 912 tests with strong assertions but heavy mock-coverage. Mutation testing on async + mocked code surfaces a high false-positive rate (mutants survive inside skipped or mocked branches that the test legitimately doesn't exercise end-to-end).
- Run time. `mutmut` against a 3.6k-symbol repo with 900+ tests is in the 12-24 hour range on a single core. CI cost is non-trivial.
- The high-leverage hardening modules — `secret_redactor`, `tools/safety`, `lmstudio_lifecycle::validate_model_id` — are better served by the property tests in §6.

When to revisit:
- After coverage hits 90%+ line / 80%+ branch.
- Pick `cosmic-ray` over `mutmut` for senex: it has better Python 3.11+ support and parallel execution.
- Scope to `senex/secret_redactor.py`, `senex/tools/safety.py`, `senex/runlock.py`, `senex/checkpoint.py` — the security-critical surfaces — not the whole repo.

---

## 8. Performance regression tests

**Priority: nice — Effort: M**

**Recommendation: add `pytest-benchmark` as a dev dep, gate behind `pytest.mark.benchmark`, run only on a dedicated nightly job (not on PR).**

What to measure:

| Benchmark | Why | Target |
|---|---|---|
| `walker.walk()` over `tests/fixtures/repos/tiny_python/` | M0/M1 hot path, gitignore + walking | <50ms |
| `SecretRedactor.redact()` on 100KB log | called on every event payload | <5ms |
| `tools.safety.strip_ansi()` on 100KB | called on every tool result | <5ms |
| `tools.list_symbols._extract_python()` on senex/auditor.py (~30KB) | symbol extraction | <20ms |
| `tools.list_symbols._extract_by_patterns()` on TS+Go large samples | regex line-scan | <30ms |
| `compaction.compact_history()` with a 200-message history | LLM context shaping | <100ms |
| `events.EventBus` publish 10k events to 5 subscribers | bus throughput | >50k events/sec |
| `findings_aggregator` with 1000 partial findings | aggregation | <500ms |
| `secret_redactor` on each of the 6 pattern types | per-pattern timing | <1ms each |

Why nightly, not per-PR:
- pytest-benchmark times are noisy on shared CI runners. A 2x regression is the floor for actionable signal — sub-2x noise is normal.
- Trend tracking (`--benchmark-storage`) needs a stable host; GitHub-hosted runners drift.

When the project moves to a self-hosted runner, fold these into PR CI.

---

## 9. Schema-pinning verification

**Priority: must — Effort: S**

Two halves: prompts (already pinned) and events.schema.json (recommended).

### 9.1 Prompts — already exists

`tests/fixtures/expected_prompt_hashes.json` pins SHA-256 for all 10 prompts in `senex/prompts/*.md`. The pre-commit hook in §2 (`prompt-hash-verify`) is the missing enforcement piece. No schema work needed here.

### 9.2 Events schema — recommend adding

The repo has `senex/schema/events.schema.json` and `senex.events.ALL_EVENT_TYPES` (44 event classes per the source). Recommendation: add a verification test that asserts:

1. Every class in `ALL_EVENT_TYPES` has a matching `$defs/<ClassName>` entry in `events.schema.json`.
2. Every `$defs/<ClassName>` entry has at least the union of `BaseEvent` fields (`v`, `type`, `ts`, `seq`, `run_id`).
3. Every `Literal["..."]` discriminator in the python class matches the `properties.type.const` in the schema.
4. The schema's top-level `oneOf` enumerates all `ALL_EVENT_TYPES` (no extras, no omissions).

Skeleton:

```python
# tests/unit/test_events_schema_pinning.py
def test_all_event_types_have_schema_def():
    schema = json.loads((REPO / "senex/schema/events.schema.json").read_text())
    defs = set(schema.get("$defs", {}).keys())
    py_types = {cls.__name__ for cls in ALL_EVENT_TYPES}
    missing_from_schema = py_types - defs
    extra_in_schema = defs - py_types
    assert not missing_from_schema, f"events missing from schema: {missing_from_schema}"
    assert not extra_in_schema, f"schema defs not in ALL_EVENT_TYPES: {extra_in_schema}"

def test_event_discriminators_match():
    # for each cls in ALL_EVENT_TYPES, parse its 'type' Literal and assert
    # schema['$defs'][cls.__name__]['properties']['type']['const'] == that Literal
```

This is **the single most valuable test to add today**. The discriminator/`type` field is checked at runtime in `BaseEvent._validate_type_discriminator` but if a new event class is added without a schema update, the schema becomes silently stale.

---

## 10. Smoke test script

**Priority: nice — Effort: S**

Recommended path: `scripts/smoke.sh` (also `scripts/smoke.ps1` for Windows / pre-tag).

Outline:

```bash
#!/usr/bin/env bash
# scripts/smoke.sh — pre-release smoke audit.
# Exits 0 on success; non-zero with descriptive message on any failure.
set -euo pipefail

REPO_TO_AUDIT="${1:-tests/fixtures/repos/known_findings}"
CONFIG="${SENEX_CONFIG:-senex.config.toml}"
COMPOSE="${SENEX_COMPOSE:-infra/sglang/compose.yml}"
ARTIFACTS_DIR="$(mktemp -d)"
echo "[smoke] artifacts -> $ARTIFACTS_DIR"

cleanup() {
  echo "[smoke] bringing SGLang container down..."
  docker compose -f "$COMPOSE" down || true
}
trap cleanup EXIT

# 1. Bring SGLang up.
echo "[smoke] starting SGLang container..."
docker compose -f "$COMPOSE" up -d
# wait for /v1/models
for i in {1..60}; do
  if curl -fsS http://localhost:30000/v1/models >/dev/null 2>&1; then break; fi
  sleep 3
done
curl -fsS http://localhost:30000/v1/models >/dev/null

# 2. Run audit.
echo "[smoke] running senex audit..."
senex audit "$REPO_TO_AUDIT" --config "$CONFIG" --output "$ARTIFACTS_DIR" --headless

# 3. Assert findings > 0.
FINDINGS_JSON="$ARTIFACTS_DIR/findings.json"
test -s "$FINDINGS_JSON" || { echo "[smoke] FAIL: findings.json missing/empty"; exit 1; }
COUNT=$(python -c "import json,sys; print(len(json.load(open('$FINDINGS_JSON'))['findings']))")
if [ "$COUNT" -lt 1 ]; then echo "[smoke] FAIL: zero findings"; exit 1; fi
echo "[smoke] OK — $COUNT findings"

# 4. Cleanup runs in trap.
```

Use cases:
- Hand-run before tagging a release (`git tag v1.0.3 && bash scripts/smoke.sh && git push --tags`).
- The Linux-only `.sh` is fine — Windows release engineering can use `scripts/smoke.ps1` (mirror script).
- Do NOT wire to CI on day 1 — needs a self-hosted runner with GPU + Docker.

---

## 11. Phased rollout

| Wave | Items | Priority | Total effort |
|---|---|---|---|
| **Wave 1 (this week)** | §9.2 events-schema test, §4.1 skills tests, §4.2 memory tests | must | 1.5 days |
| **Wave 2 (next week)** | §1 CI workflow, §2 pre-commit, §4.3-4.6 tool + HTTPBackend tests | must / should | 4 days |
| **Wave 3 (sprint)** | §4.7 unit + integration test, §5.1 e2e fixture repo, §6 property tests | should | 5 days |
| **Wave 4 (post-v1.1)** | §3 coverage gates ratchet, §5.2-5.3 resume + Docker integration, §10 smoke script | should / nice | 5 days |
| **Wave 5 (deferred)** | §7 mutation testing, §8 performance benchmarks, self-hosted runner | nice | 10+ days |

---

## 12. Self-check (per AGENTS protocol)

Read-only review — no symbols modified. No `gitnexus_impact` runs needed for this deliverable. The recommendations in §1, §2, §3, §9 do edit `pyproject.toml` and add new files; those edits happen in the *next wave* and will follow the standard `gitnexus_impact` → edit → `gitnexus_detect_changes` discipline.

Files surveyed (absolute paths):
- `E:\senex\pyproject.toml`
- `E:\senex\tests\conftest.py`
- `E:\senex\senex\skills.py`
- `E:\senex\senex\memory.py`
- `E:\senex\senex\tools\list_dir.py`
- `E:\senex\senex\tools\list_symbols.py`
- `E:\senex\senex\tools\run_semgrep.py`
- `E:\senex\senex\tools\safety.py`
- `E:\senex\senex\lmstudio_lifecycle.py` (HTTPBackend at lines 361-543)
- `E:\senex\senex\cli_audit.py` (`_ensure_managed_container_up` at lines 191-248)
- `E:\senex\senex\events.py` (ALL_EVENT_TYPES at lines 400-418)
- `E:\senex\senex\secret_redactor.py`
- `E:\senex\tests\fixtures\expected_prompt_hashes.json`
- `E:\senex\tests\fixtures\repos\tiny_python\`
- `E:\senex\tests\recorded\test_run_audit_e2e.py`
