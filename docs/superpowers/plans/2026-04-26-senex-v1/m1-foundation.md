# Milestone 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

> **Bulletproof contract:** This file is sized for an implementer who has only this file in context. Every test body is shown verbatim; every signature is shown; every exception class is named; every commit message is shown literally. If a step says "implement X", that step also shows the parameter names, types, return type, and the 2-5 lines of load-bearing logic. If you find yourself making a judgment call, stop and re-read — the answer is in the spec citation on the same step.

## Context

M1 delivers Layer 0 of the senex architecture — the foundational primitives (config, events, redaction, lens, runlock, checkpoint, schemas) that every later milestone imports. These modules have **no internal senex dependencies** other than each other; they are the bedrock of the system. Get them right; everything builds on them.

**Architectural intent:** Layer 0 modules are pure data + protocol definitions. They do not perform I/O against LM Studio, do not subprocess to GitNexus, and do not orchestrate phases. They define the *shapes* (pydantic models, JSON schemas, dataclasses) and *primitives* (event bus, redactor regexes, file-based locks) the rest of the codebase consumes.

## Prerequisites

- **Completed milestones:** None — this is the first milestone.
- **Required modules from prior work:** None.
- **Required tools/state:**
  - Python 3.11+ on PATH (`python --version` ≥ 3.11; uses `tomllib` from stdlib)
  - `pip` and `venv` available
  - Spec file at `E:/senex/docs/superpowers/specs/2026-04-26-senex-audit-tool-design.md` (referenced for verbatim copy of schemas, system prompt, sample config)
  - Conventions file at `E:/senex/docs/superpowers/conventions.md`

## Deliverable

This milestone creates the following files:

- `pyproject.toml`, `requirements.txt` — package metadata + dependency pins
- `senex/__init__.py` — version + package init
- `senex/config.py` — `SenexConfig` pydantic v2 model + `load_config()` + `resolve_config()` (deep-merge precedence)
- `senex.config.toml.example` — annotated sample config
- `senex/events.py` — 35+ event pydantic models + `EventBus` + `CommandBus`
- `senex/secret_redactor.py` — `SecretRedactor` with named patterns + `redact_dict()`
- `senex/lens.py` + `senex/lens/correctness/{lens.toml,tools.toml}` — `Lens` dataclass + `Lens.load(name)`
- `senex/runlock.py` — `RunLock` interprocess refcount file lock with stale-PID prune
- `senex/checkpoint.py` + `senex/schema/checkpoint.schema.json` — `Checkpoint` state machine
- `senex/schema/{audit_response,crosscut_response,compaction_response,findings_index,events}.schema.json` — 5 JSON Schemas

## Downstream consumers

- **M2** needs `senex/walker.py` to import `events` (FileSkipped/SymlinkSkipped emission); `graph_awareness.py` needs `events`.
- **M3** needs `events`, `secret_redactor`, `config` (LmStudioCfg), `runlock` (fingerprint comparison).
- **M4** needs `runlock`, `events` (lifecycle event types), `config` (LifecycleCfg).
- **M5** needs `lens` (tool-pack resolution), `config` (ToolsCfg), `events` (ToolCall/Result/Error).
- **M6** needs `events` (CompactionTriggered/Complete/Error), `config` (CompactionCfg).
- **M7** needs the JSON schemas (renderer validates against `audit_response.schema.json`).
- **M8** needs `checkpoint` (phase tracking), `events` (RunStart/RunComplete), all of Layer 0.
- **M9** needs `events` (subscribers consume the event types).
- **M10** needs `config` (CLI flags merge into resolved config).

## Spec sections referenced

- §3.1 File Layout — verifies module names match spec
- §3.2 Module Boundaries — Layer 0 isolation
- §5.0 Lens Abstraction — `Lens` shape, `lens.toml` + `tools.toml` separation
- §5.4 Audit response schema — copied verbatim into `senex/schema/audit_response.schema.json`
- §5.4.1 Crosscut response schema — copied verbatim
- §5.5.1 Compaction response schema — copied verbatim
- §5.5.2.2 Runlock semantics — refcount file format, stale-PID prune
- §5.6 Events table — 35+ event types
- §5.6.1 Event bus semantics — per-subscriber backpressure (DiskWriter blocks, TUI drops Tick)
- §5.6.2 Command bus — TUI → auditor channel
- §5.10 Secret Redactor — pattern set
- §6 Configuration — TOML schema, `extra="forbid"`
- §6.1 Configuration Resolution — defaults → file → per-repo → CLI → TUI deep-merge order
- §7.3 findings.json — schema basis for `findings_index.schema.json`
- §8.5 Reproducibility — required hash field set on checkpoint
- §11.1 Threat model rows for redactor

## Conventions referenced

- §1 code style — module docstrings, pathlib, `from __future__ import annotations`
- §2 type discipline — `mypy --strict`, no `Any` in public signatures
- §3 async — bounded queues with explicit `maxsize`, no sync I/O in async paths
- §4 error handling — named exceptions at module top-level
- §5 security — path safety, regex hardening, secret redaction, no hardcoded creds
- §6 testing — TDD strictly, `test_<unit>_<scenario>_<expected_outcome>` naming, no mocks of internal code
- §8 persistence — atomic `tmp + fsync + rename`, UTF-8 no BOM, LF line endings
- §10 pydantic — `extra="forbid"` everywhere
- §11 git — commit per task; `feat(M1): <imperative summary>` format
- §13 docs — module docstring on every `.py` file

## Key contracts (functions/classes you will write)

- **`SenexConfig`** — root pydantic v2 model with `model_config = ConfigDict(extra="forbid")`. Nested: `OutputCfg`, `LensCfg`, `WalkerCfg`, `CrosscutCfg`, `LmStudioCfg` (with `SamplingCfg`, `ThinkingCfg`, `TasksCfg`, `ToolsCfg`, `CompactionCfg`, `LifecycleCfg`), `RepoCfg`.
- **`load_config(path: Path) -> SenexConfig`** — TOML → `SenexConfig`. Unknown key → `UnknownConfigKey` (subclass of `ValueError`) with Levenshtein hint.
- **`resolve_config(base: SenexConfig, repo_path: Path | None, cli_overrides: dict, tui_overrides: dict) -> SenexConfig`** — deep-merge in spec §6.1 order.
- **`BaseEvent`** + 35 subclasses with `Field(default="<Literal>")` discriminator.
- **`EventBus.subscribe(name: str, capacity: int | None = None) -> asyncio.Queue[BaseEvent]`** — bounded.
- **`EventBus.publish(event: BaseEvent) -> None`** — async; per-subscriber slow policy.
- **`CommandBus`** — separate channel; `Command` pydantic model.
- **`SecretRedactor.redact(text: str) -> str`** — applies named patterns in priority order.
- **`SecretRedactor.redact_dict(d: dict, keys: tuple[str, ...] = (...))`** — for config snapshotting.
- **`Lens.load(name: str) -> Lens`** — reads `senex/lens/<name>/{lens.toml,tools.toml}`, validates, computes `fingerprint`.
- **`RunLock.acquire(fingerprint: str, run_id: str, pid: int, loaded_by_us: bool) -> int`** — atomic file refcount.
- **`RunLock.release(fingerprint: str, run_id: str) -> int`** — returns remaining count.
- **`Checkpoint.create(audit_dir: Path, ...)`** / **`Checkpoint.mark_done(audit_dir: Path, file: str)`** / **`Checkpoint.set_phase(audit_dir: Path, name: str, status: str)`** / **`Checkpoint.is_compatible(other: dict) -> bool`** — atomic write-rename.

## Watch-outs (file-wide)

- **`extra="forbid"` is non-negotiable.** Drift between config schema and example will fail tests; the agent who edits one must edit the other.
- **Event seq must be monotonic per run.** A single `_seq_counter` lives on the bus; subclasses MUST NOT generate their own seq. Test: publish 100 events sequentially, assert `seq` values are exactly `[0, 1, ..., 99]`.
- **Per-subscriber backpressure policy is asymmetric:** DiskWriter blocks the publisher (write-ahead is critical for crash recovery); TUI drops `ThinkingTick`/`OutputTick` only; Metrics drops oldest tick. Get this wrong and the TUI hangs the auditor under load.
- **Runlock atomicity:** read+modify+write must hold the `portalocker` advisory lock for the entire critical section. Tmp+rename is the only safe pattern for the data file write.
- **Redactor priority:** longer/more-specific patterns FIRST (PEM block, JWT, AWS key, GitHub PAT) before generic `KEY=value` env-style. Otherwise the generic pattern eats the specifics. The test ordering enforces this.
- **Schema files are authoritative.** Spec §5.4 is verbatim source; do not paraphrase. `additionalProperties: false` on every object; `location` uses `oneOf` per §5.4.

## Patterns to follow

- **TDD throughout** — every task starts with a failing test before implementation (conventions §6).
- **Atomic writes** — every file the system writes (lockfile, checkpoint, partial findings) uses `tmp + fsync + rename`. No exceptions (conventions §8).
- **Pydantic v2** — `model_validate` for parsing, `model_dump` for serialization, `Field(default=...)` for discriminators, `Discriminator` from `pydantic` for tagged unions (conventions §10).
- **Spec-verbatim copies:** schemas in §5.4 / §5.4.1 / §5.5.1 are copied byte-for-byte; sample config in §6 is copied as `senex.config.toml.example`.
- **`from __future__ import annotations`** on every `.py` file (conventions §2).
- **Module-level docstring** on every `.py` file: 1-2 lines on purpose + spec section it implements (conventions §13). A test asserts this is non-empty.

---

## Tasks

### Task 1.1: Project scaffolding (pyproject.toml, package init, requirements.txt)

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `senex/__init__.py`
- Create: `tests/unit/test_package.py`

**Conventions:** §1 (code style), §11 (git commit format), §13 (docs).

- [x] **Step 1.1.1: Write `pyproject.toml`** with the following content (copy verbatim — version pins per spec §5.5):

  ```toml
  [build-system]
  requires = ["setuptools>=68", "wheel"]
  build-backend = "setuptools.build_meta"

  [project]
  name = "senex"
  version = "0.1.0"
  description = "Local-LLM code audit tool"
  requires-python = ">=3.11"
  dependencies = [
    "openai>=1.50,<2.0",
    "pydantic>=2.5,<3",
    "textual>=0.50",
    "tiktoken",
    "portalocker",
    "detect-secrets",
    "regex",
    "httpx",
    "jsonschema>=4.20",
    "tomli-w",
    "ulid-py",
  ]

  [project.optional-dependencies]
  dev = ["pytest>=7", "pytest-asyncio>=0.21", "pytest-cov", "hypothesis", "mypy", "ruff", "respx"]

  [project.scripts]
  senex = "senex.cli:main"

  [tool.setuptools.packages.find]
  include = ["senex*"]

  [tool.setuptools.package-data]
  senex = ["schema/*.json", "lens/**/*.toml", "lens/**/*.md", "prompts/*.md"]

  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  testpaths = ["tests"]

  [tool.ruff]
  line-length = 100
  target-version = "py311"

  [tool.mypy]
  strict = true
  python_version = "3.11"
  ```

- [x] **Step 1.1.2: Write `requirements.txt`** mirroring the dependency list (one per line, no version comments):
  ```
  openai>=1.50,<2.0
  pydantic>=2.5,<3
  textual>=0.50
  tiktoken
  portalocker
  detect-secrets
  regex
  httpx
  jsonschema>=4.20
  tomli-w
  ulid-py
  ```

- [x] **Step 1.1.3: Write `senex/__init__.py`** with module docstring + `__version__`:

  ```python
  """senex — Local-LLM code audit tool. See spec §1, §3.1.

  Public re-exports are intentionally empty in M1; downstream milestones
  add to ``__all__`` as the public surface stabilizes.
  """
  from __future__ import annotations

  __version__ = "0.1.0"
  __all__: list[str] = []
  ```

- [x] **Step 1.1.4: Failing test** at `tests/unit/test_package.py`:

  ```python
  """Smoke tests for the senex package init."""
  from __future__ import annotations

  import importlib

  import senex


  def test_package_version_is_pep440_string() -> None:
      assert senex.__version__ == "0.1.0"


  def test_package_module_has_nonempty_docstring() -> None:
      # conventions §13: every module gets a top-of-file docstring.
      assert senex.__doc__ is not None
      assert senex.__doc__.strip() != ""


  def test_package_imports_clean() -> None:
      # Re-import to catch import-time side effects.
      importlib.reload(senex)
      assert hasattr(senex, "__version__")
  ```

- [x] **Step 1.1.5: Verify install** by running:
  ```bash
  python -m pip install -e .
  python -c "import senex; print(senex.__version__)"
  ```
  Expected stdout: `0.1.0`.

- [x] **Step 1.1.6: Run tests:**
  ```bash
  pytest tests/unit/test_package.py -v
  ```
  Expected output lines (literal):
  ```
  PASSED tests/unit/test_package.py::test_package_version_is_pep440_string
  PASSED tests/unit/test_package.py::test_package_module_has_nonempty_docstring
  PASSED tests/unit/test_package.py::test_package_imports_clean
  ```

- [x] **Step 1.1.7: Lint + type check:**
  ```bash
  ruff check senex/ tests/
  mypy senex/
  ```
  Both must exit 0.

- [x] **Step 1.1.8: Commit** (conventions §11):
  ```bash
  git add pyproject.toml requirements.txt senex/__init__.py tests/unit/test_package.py
  git commit -m "feat(M1): project scaffolding (pyproject, requirements, package init)"
  ```

**Pitfalls (Task 1.1):**
- `setuptools.package-data` is REQUIRED to ship `schema/*.json` and `lens/**/*.toml` inside the wheel. Without it, `Lens.load()` and schema validators break in installed mode (`pip install .`) but pass in editable mode (`pip install -e .`) — silent regression.
- `requires-python = ">=3.11"` is load-bearing: `tomllib` is stdlib only on 3.11+. Lower Python versions silently bypass the strict-pydantic config validation path.
- `asyncio_mode = "auto"` in `[tool.pytest.ini_options]` is required for Task 1.3 — without it, `async def` tests are skipped silently.

**Definition of done (Task 1.1):**
- `pip install -e .` exits 0.
- `python -c "import senex; print(senex.__version__)"` prints `0.1.0`.
- `pytest tests/unit/test_package.py -v` shows 3 PASSED.
- `ruff check senex/ tests/` exits 0.
- `mypy senex/` exits 0.
- `git status` after commit shows clean working tree.

---

### Task 1.2: Config models (pydantic v2)

**Files:**
- Create: `senex/config.py`
- Create: `senex.config.toml.example`
- Create: `tests/unit/test_config.py`

**Conventions:** §1 (pathlib, f-strings), §2 (type hints), §4 (named exceptions), §10 (`extra="forbid"`), §11 (commit format), §13 (docstrings).

- [x] **Step 1.2.1: Write `senex.config.toml.example`** verbatim from spec §6 (lines 1151–1217). Includes `[output]`, `[lens]`, `[walker]`, `[crosscut]`, `[lmstudio]` (with `[lmstudio.sampling]`, `[lmstudio.thinking]`, `[lmstudio.tasks.file_audit]`, `[lmstudio.tasks.cross_cutting]`, `[lmstudio.tools]`, `[lmstudio.compaction]`, `[lmstudio.lifecycle]`), and `[[repos]]` for `pensiv`. Use the inline-table form: `lmstudio = { tasks = { file_audit = { temperature = 0.5 } } }`.

- [x] **Step 1.2.2: Write the failing test file** at `tests/unit/test_config.py`:

  ```python
  """Tests for senex.config — TOML loader + strict pydantic validation + deep-merge."""
  from __future__ import annotations

  from pathlib import Path

  import pytest

  from senex.config import (
      SenexConfig,
      UnknownConfigKey,
      load_config,
      resolve_config,
  )

  EXAMPLE = Path("senex.config.toml.example")


  def test_load_config_example_returns_senexconfig() -> None:
      cfg = load_config(EXAMPLE)
      assert isinstance(cfg, SenexConfig)
      assert cfg.lens.name == "correctness"
      assert cfg.lmstudio.model == "google/gemma-4-26b-a4b"


  def test_load_config_unknown_key_raises_with_hint(tmp_path: Path) -> None:
      bad = tmp_path / "bad.toml"
      bad.write_text("[lens]\nnaem = 'correctness'\n", encoding="utf-8")
      with pytest.raises(UnknownConfigKey) as exc:
          load_config(bad)
      msg = str(exc.value)
      assert "naem" in msg
      assert "name" in msg  # Levenshtein hint suggests the closest valid key.


  def test_load_config_temperature_out_of_range_raises(tmp_path: Path) -> None:
      bad = tmp_path / "bad.toml"
      bad.write_text(
          "[lmstudio.sampling]\ntemperature = 3.0\n",
          encoding="utf-8",
      )
      with pytest.raises(ValueError):
          load_config(bad)


  def test_load_config_seed_random_default_true_when_omitted(tmp_path: Path) -> None:
      f = tmp_path / "ok.toml"
      f.write_text("", encoding="utf-8")
      cfg = load_config(f)
      assert cfg.lmstudio.sampling.seed_random is True


  def test_load_config_extra_top_level_key_raises(tmp_path: Path) -> None:
      bad = tmp_path / "bad.toml"
      bad.write_text("foobar = 1\n", encoding="utf-8")
      with pytest.raises(UnknownConfigKey):
          load_config(bad)


  def test_resolve_config_per_repo_overrides_shadow_global() -> None:
      base = load_config(EXAMPLE)
      repo_cfg = resolve_config(
          base,
          repo_path=Path("E:/pensiv"),
          cli_overrides={},
          tui_overrides={},
      )
      # The example puts temperature=0.5 in the pensiv repo override.
      assert repo_cfg.lmstudio.tasks.file_audit.temperature == 0.5


  def test_resolve_config_cli_overrides_shadow_repo() -> None:
      base = load_config(EXAMPLE)
      cli = {"lmstudio": {"sampling": {"temperature": 0.9}}}
      out = resolve_config(base, repo_path=None, cli_overrides=cli, tui_overrides={})
      assert out.lmstudio.sampling.temperature == 0.9


  def test_resolve_config_tui_overrides_shadow_cli() -> None:
      base = load_config(EXAMPLE)
      cli = {"lmstudio": {"sampling": {"temperature": 0.9}}}
      tui = {"lmstudio": {"sampling": {"temperature": 0.7}}}
      out = resolve_config(base, repo_path=None, cli_overrides=cli, tui_overrides=tui)
      assert out.lmstudio.sampling.temperature == 0.7


  def test_resolve_config_explicit_empty_replaces_inherited() -> None:
      # Per §6.1: null/""/[] REPLACE; only omission inherits.
      base = load_config(EXAMPLE)
      cli = {"walker": {"extensions": []}}
      out = resolve_config(base, repo_path=None, cli_overrides=cli, tui_overrides={})
      assert out.walker.extensions == []


  def test_config_module_has_nonempty_docstring() -> None:
      from senex import config as config_mod
      assert config_mod.__doc__ is not None and config_mod.__doc__.strip() != ""


  def test_walker_cfg_defaults_match_spec_section_6() -> None:
      # Per R8: WalkerCfg defaults are part of the spec §6 contract.
      from senex.config import WalkerCfg
      cfg = WalkerCfg()
      assert cfg.max_size_bytes == 524_288
      assert cfg.respect_gitignore is True
      assert cfg.include_tests is False
      assert cfg.extensions == [
          ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
          ".java", ".kt", ".cs", ".cpp", ".c", ".h", ".hpp",
          ".swift", ".rb", ".php", ".sh", ".ps1", ".scala",
          ".ex", ".exs", ".dart", ".lua", ".zig", ".nim",
      ]
      assert cfg.default_excludes == [
          "node_modules", ".venv", "venv", "dist", "build",
          "__pycache__", "*.min.*", ".git", "vendor",
      ]
  ```

- [x] **Step 1.2.3: Implement `senex/config.py`.** Top-of-file docstring + named exception + nested models + loaders. Skeleton (fill in fields per spec §6 / §5.5):

  ```python
  """senex.config — TOML config loader + strict pydantic validation + deep-merge.

  Implements spec §6 (configuration) and §6.1 (resolution semantics).
  Per conventions §10, every model has ``extra="forbid"``.
  """
  from __future__ import annotations

  import tomllib
  from copy import deepcopy
  from pathlib import Path
  from typing import Annotated, Any, Literal

  from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


  class UnknownConfigKey(ValueError):
      """Raised when senex.config.toml contains a key not declared on the schema."""


  class _StrictModel(BaseModel):
      model_config = ConfigDict(extra="forbid", validate_assignment=True)


  class OutputCfg(_StrictModel):
      root: str = "E:/senex-audits"
      filename_template: str = "{repo}/{date}-{run_id_short}"
      redact_secrets: bool = True
      retention_days: int = Field(default=0, ge=0)


  class LensCfg(_StrictModel):
      name: str = "correctness"
      # Matches CLI --min-priority (spec §10, §API-4). Order: "all" < "low" < "medium" < "high".
      min_priority: Annotated[
          Literal["all", "low", "medium", "high"], Field(default="all")
      ]
      include_tests: bool = False


  class WalkerCfg(_StrictModel):
      # Defaults per spec §6 (must match the senex.config.toml.example values).
      max_size_bytes: int = Field(default=524_288, gt=0)
      extensions: list[str] = Field(
          default_factory=lambda: [
              ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
              ".java", ".kt", ".cs", ".cpp", ".c", ".h", ".hpp",
              ".swift", ".rb", ".php", ".sh", ".ps1", ".scala",
              ".ex", ".exs", ".dart", ".lua", ".zig", ".nim",
          ]
      )
      default_excludes: list[str] = Field(
          default_factory=lambda: [
              "node_modules", ".venv", "venv", "dist", "build",
              "__pycache__", "*.min.*", ".git", "vendor",
          ]
      )
      respect_gitignore: bool = True
      include_tests: bool = False


  class CrosscutCfg(_StrictModel):
      top_n_high: int = 50
      top_n_medium: int = 100
      top_n_low: int = 100


  class SamplingCfg(_StrictModel):
      temperature: float = Field(default=0.6, ge=0.0, le=2.0)
      top_p: float = Field(default=0.95, ge=0.0, le=1.0)
      top_k: int = Field(default=40, ge=0, le=200)
      min_p: float = Field(default=0.0, ge=0.0, le=1.0)
      repeat_penalty: float = Field(default=1.0, ge=0.0, le=2.0)
      frequency_penalty: float = 0.0
      presence_penalty: float = 0.0
      seed: int = 42
      seed_random: bool = True
      max_tokens: int = Field(default=8192, gt=0)
      stop: list[str] = Field(default_factory=list)


  class ThinkingCfg(_StrictModel):
      enabled: bool = True
      effort: str = "high"  # "low" | "medium" | "high"
      max_thinking_tokens: int = Field(default=32768, gt=0)
      save_traces: bool = True
      include_in_report: bool = False


  class TaskCfg(_StrictModel):
      temperature: float | None = None
      max_tokens: int | None = None


  class TasksCfg(_StrictModel):
      file_audit: TaskCfg = Field(default_factory=TaskCfg)
      cross_cutting: TaskCfg = Field(default_factory=TaskCfg)


  class ToolsCfg(_StrictModel):
      enabled: bool = True
      max_calls_per_file: int = Field(default=5, ge=0)
      max_result_tokens: int = Field(default=2048, gt=0)
      tool_timeout_seconds: int = Field(default=30, gt=0)
      enabled_tools: list[str] | None = None  # subset of lens tools when set


  class CompactionCfg(_StrictModel):
      enabled: bool = True
      trigger_pct: float = Field(default=0.80, gt=0.0, le=1.0)
      target_pct: float = Field(default=0.50, gt=0.0, le=1.0)
      preserve_recent_turns: int = Field(default=2, ge=0)
      max_compactions_per_file: int = Field(default=3, ge=0)


  class LifecycleCfg(_StrictModel):
      auto_load: bool = True
      auto_unload: bool = True
      load_timeout_seconds: int = Field(default=120, gt=0)
      runlock_dir: str = ""


  class LmStudioCfg(_StrictModel):
      base_url: str = "http://localhost:1234/v1"
      api_key: str = "lm-studio"
      connect_timeout: int = 10
      read_timeout: int = 600
      http_retries: int = 3
      backoff_seconds: list[int] = Field(default_factory=lambda: [5, 15, 45])
      model: str = "google/gemma-4-26b-a4b"
      preset: str = ""
      allow_non_loopback: bool = False
      sampling: SamplingCfg = Field(default_factory=SamplingCfg)
      thinking: ThinkingCfg = Field(default_factory=ThinkingCfg)
      tasks: TasksCfg = Field(default_factory=TasksCfg)
      tools: ToolsCfg = Field(default_factory=ToolsCfg)
      compaction: CompactionCfg = Field(default_factory=CompactionCfg)
      lifecycle: LifecycleCfg = Field(default_factory=LifecycleCfg)


  class RepoCfg(_StrictModel):
      name: str
      path: str  # MUST be absolute; preflight enforces (spec §6.1).
      system_prompt_addendum: str | None = None
      include_extensions: list[str] = Field(default_factory=list)
      exclude_globs: list[str] = Field(default_factory=list)
      include_tests: bool = False
      lmstudio: dict[str, Any] | None = None  # nested override; merged at resolve time.


  class SenexConfig(_StrictModel):
      output: OutputCfg = Field(default_factory=OutputCfg)
      lens: LensCfg = Field(default_factory=LensCfg)
      walker: WalkerCfg = Field(default_factory=WalkerCfg)
      crosscut: CrosscutCfg = Field(default_factory=CrosscutCfg)
      lmstudio: LmStudioCfg = Field(default_factory=LmStudioCfg)
      repos: list[RepoCfg] = Field(default_factory=list)


  def _levenshtein(a: str, b: str) -> int:
      # Tiny iterative DP. Caller passes short strings (config keys).
      if a == b:
          return 0
      if not a:
          return len(b)
      if not b:
          return len(a)
      prev = list(range(len(b) + 1))
      for i, ca in enumerate(a, 1):
          curr = [i] + [0] * len(b)
          for j, cb in enumerate(b, 1):
              cost = 0 if ca == cb else 1
              curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
          prev = curr
      return prev[-1]


  def _closest(unknown: str, candidates: list[str]) -> str:
      return min(candidates, key=lambda c: _levenshtein(unknown, c)) if candidates else ""


  def _convert_validation_error(exc: ValidationError) -> Exception:
      for err in exc.errors():
          if err["type"] == "extra_forbidden":
              loc = ".".join(str(p) for p in err["loc"])
              # Best-effort: ask the parent model what it accepts.
              hint = "(no candidates available)"
              return UnknownConfigKey(f"unknown config key: {loc!r}; closest valid: {hint}")
      return exc


  def load_config(path: Path) -> SenexConfig:
      """Parse TOML at ``path`` into a validated SenexConfig.

      Raises:
          UnknownConfigKey: TOML contained a key not declared on the schema.
          ValueError: A value was out of range / invalid type (pydantic ValidationError).
      """
      data = tomllib.loads(path.read_text(encoding="utf-8"))
      try:
          return SenexConfig.model_validate(data)
      except ValidationError as exc:
          # Re-raise as UnknownConfigKey when the cause is extra_forbidden.
          for err in exc.errors():
              if err["type"] == "extra_forbidden":
                  bad_key = err["loc"][-1]
                  parent_loc = err["loc"][:-1]
                  parent_model = SenexConfig
                  for seg in parent_loc:
                      field = parent_model.model_fields.get(str(seg))
                      if field and isinstance(field.annotation, type) and issubclass(
                          field.annotation, BaseModel
                      ):
                          parent_model = field.annotation
                  candidates = list(parent_model.model_fields.keys())
                  hint = _closest(str(bad_key), candidates)
                  raise UnknownConfigKey(
                      f"unknown config key: {'.'.join(str(p) for p in err['loc'])!r}; "
                      f"did you mean {hint!r}?"
                  ) from exc
          raise


  def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
      """Per spec §6.1: deep, per-key. Empty list/string/None REPLACE; omission inherits."""
      out = deepcopy(base)
      for k, v in over.items():
          if isinstance(v, dict) and isinstance(out.get(k), dict):
              out[k] = _deep_merge(out[k], v)
          else:
              out[k] = deepcopy(v)
      return out


  def resolve_config(
      base: SenexConfig,
      repo_path: Path | None,
      cli_overrides: dict[str, Any],
      tui_overrides: dict[str, Any],
  ) -> SenexConfig:
      """Apply spec §6.1 layering: defaults → file → per-repo → CLI → TUI.

      ``base`` already contains layers 1+2 (defaults from pydantic + the parsed
      ``senex.config.toml``). This function applies layers 3-5.
      """
      merged = base.model_dump()
      if repo_path is not None:
          for repo in base.repos:
              if Path(repo.path) == repo_path and repo.lmstudio:
                  merged = _deep_merge(merged, {"lmstudio": repo.lmstudio})
                  break
      merged = _deep_merge(merged, cli_overrides)
      merged = _deep_merge(merged, tui_overrides)
      return SenexConfig.model_validate(merged)
  ```

- [x] **Step 1.2.4: Run targeted tests:**
  ```bash
  pytest tests/unit/test_config.py -v
  ```
  Expected (literal lines):
  ```
  PASSED tests/unit/test_config.py::test_load_config_example_returns_senexconfig
  PASSED tests/unit/test_config.py::test_load_config_unknown_key_raises_with_hint
  PASSED tests/unit/test_config.py::test_load_config_temperature_out_of_range_raises
  PASSED tests/unit/test_config.py::test_load_config_seed_random_default_true_when_omitted
  PASSED tests/unit/test_config.py::test_load_config_extra_top_level_key_raises
  PASSED tests/unit/test_config.py::test_resolve_config_per_repo_overrides_shadow_global
  PASSED tests/unit/test_config.py::test_resolve_config_cli_overrides_shadow_repo
  PASSED tests/unit/test_config.py::test_resolve_config_tui_overrides_shadow_cli
  PASSED tests/unit/test_config.py::test_resolve_config_explicit_empty_replaces_inherited
  PASSED tests/unit/test_config.py::test_config_module_has_nonempty_docstring
  ```

- [x] **Step 1.2.5: Lint + type check:**
  ```bash
  ruff check senex/ tests/
  mypy senex/
  ```
  Both exit 0.

- [x] **Step 1.2.6: Commit:**
  ```bash
  git add senex/config.py senex.config.toml.example tests/unit/test_config.py
  git commit -m "feat(M1): config loader with strict pydantic validation"
  ```

**Pitfalls (Task 1.2):**
- `extra="forbid"` MUST be on every nested model, not just the root. Pydantic v2 does NOT inherit `model_config` through `Field(default_factory=...)` — each `_StrictModel` subclass picks it up only because it inherits from `_StrictModel`. Adding a model that inherits directly from `BaseModel` silently re-enables extra keys.
- The Levenshtein hint logic walks `model_fields` of the deepest matching model. If you flatten the parent traversal, you'll suggest top-level keys when the typo was nested — the test catches this.
- TOML parsing returns `dict[str, Any]`; `SenexConfig.model_validate(data)` is the SINGLE validation entry. Do not try to validate piecewise; pydantic emits richer errors when given the full tree.
- `_deep_merge` MUST `deepcopy` values; otherwise nested list mutations leak across resolved configs (race in tests run in parallel).
- Empty-replaces-inherited is a §6.1 requirement: `walker.extensions = []` does NOT inherit the file's list; it produces an empty list. The test `test_resolve_config_explicit_empty_replaces_inherited` enforces this.

**Definition of done (Task 1.2):**
- `pytest tests/unit/test_config.py -v` shows 10 PASSED.
- `ruff check senex/ tests/` exits 0.
- `mypy senex/` exits 0; no `Any` in public function signatures except `cli_overrides: dict[str, Any]` / `tui_overrides: dict[str, Any]` which are intentionally untyped at the boundary.
- `python -c "from senex.config import load_config; print(load_config('senex.config.toml.example').lens.name)"` prints `correctness`.
- `git diff --stat HEAD~1` lists exactly: `senex/config.py`, `senex.config.toml.example`, `tests/unit/test_config.py`.

---

### Task 1.3: Event types + EventBus + CommandBus

**Files:**
- Create: `senex/events.py`
- Create: `tests/unit/test_events.py`

**Conventions:** §3 (async, bounded queues with explicit `maxsize`), §4 (named exceptions), §10 (pydantic), §11, §13.

- [x] **Step 1.3.1: Failing test file** at `tests/unit/test_events.py`:

  ```python
  """Tests for senex.events — event taxonomy + bounded EventBus + CommandBus."""
  from __future__ import annotations

  import asyncio
  from datetime import datetime, timezone

  import pytest
  from pydantic import ValidationError

  from senex.events import (
      ALL_EVENT_TYPES,
      BaseEvent,
      Command,
      CommandBus,
      EventBus,
      FileComplete,
      FileError,
      OutputTick,
      RunStart,
      ThinkingTick,
      ToolCall,
      UnknownEventType,
  )


  def _now() -> datetime:
      return datetime.now(tz=timezone.utc)


  def test_event_count_is_at_least_36() -> None:
      # Spec §5.6 declares 36+ event types (35 baseline + GraphContextUnavailable
      # added per R7 reconciliation).
      assert len(ALL_EVENT_TYPES) >= 36


  def test_event_runstart_serialization_roundtrip() -> None:
      ev = RunStart(
          v=1, type="RunStart", ts=_now(), seq=0, run_id="01jz3k7b",
          repo="pensiv", audit_dir="/tmp/x", model="g/g", lens="correctness",
          lens_version="1.0.0", config_hash="sha256:abc", prompt_hash="sha256:def",
          model_fingerprint="sha256:ghi", started_at=_now(),
      )
      data = ev.model_dump_json()
      parsed = RunStart.model_validate_json(data)
      assert parsed.run_id == "01jz3k7b"


  @pytest.mark.parametrize("event_cls", ALL_EVENT_TYPES)
  def test_every_event_subclass_has_type_literal(event_cls: type[BaseEvent]) -> None:
      # Discriminator field MUST be a literal default per §5.6 / conventions §10.
      field = event_cls.model_fields["type"]
      assert field.default == event_cls.__name__


  def test_event_unknown_type_raises_validation_error() -> None:
      with pytest.raises((ValidationError, UnknownEventType)):
          BaseEvent.model_validate(
              {"v": 1, "type": "NotARealEvent", "ts": _now().isoformat(),
               "seq": 0, "run_id": "x"}
          )


  @pytest.mark.asyncio
  async def test_eventbus_subscribe_returns_bounded_queue() -> None:
      bus = EventBus(default_capacity=4)
      q = bus.subscribe("test", capacity=4)
      # Conventions §3: every queue MUST declare an explicit maxsize.
      assert q.maxsize == 4


  @pytest.mark.asyncio
  async def test_eventbus_publish_assigns_monotonic_seq() -> None:
      bus = EventBus(default_capacity=128)
      q = bus.subscribe("test", capacity=128)
      for _ in range(10):
          await bus.publish(
              FileError(ts=_now(), seq=0, run_id="x", path="a.py",
                        phase="audit", error_kind="boom", error_message="m")
          )
      seqs = [(await q.get()).seq for _ in range(10)]
      assert seqs == list(range(10))


  @pytest.mark.asyncio
  async def test_eventbus_diskwriter_blocks_publisher_when_full() -> None:
      bus = EventBus(default_capacity=2)
      q = bus.subscribe("DiskWriter", capacity=2)
      # Fill the queue.
      for _ in range(2):
          await bus.publish(
              FileComplete(ts=_now(), seq=0, run_id="x", path="a.py",
                           finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0})
          )
      # Third publish must block. Race it against a 50ms timeout to assert blocking.
      with pytest.raises(asyncio.TimeoutError):
          await asyncio.wait_for(
              bus.publish(
                  FileComplete(ts=_now(), seq=0, run_id="x", path="b.py",
                               finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0})
              ),
              timeout=0.05,
          )
      # Drain to unblock.
      _ = await q.get()


  @pytest.mark.asyncio
  async def test_eventbus_tui_drops_thinkingtick_when_full() -> None:
      bus = EventBus(default_capacity=2)
      q = bus.subscribe("Tui", capacity=2)
      # Fill the queue with ticks.
      for _ in range(2):
          await bus.publish(ThinkingTick(ts=_now(), seq=0, run_id="x",
                                         path="a.py", tokens_so_far=1, delta_since_last_tick=1))
      # Third tick must be dropped (NOT block).
      await asyncio.wait_for(
          bus.publish(ThinkingTick(ts=_now(), seq=0, run_id="x",
                                   path="a.py", tokens_so_far=2, delta_since_last_tick=1)),
          timeout=0.05,
      )
      # Queue still has only 2 entries.
      assert q.qsize() == 2


  @pytest.mark.asyncio
  async def test_eventbus_tui_never_drops_runstart_filecomplete_etc() -> None:
      bus = EventBus(default_capacity=1)
      q = bus.subscribe("Tui", capacity=1)
      await bus.publish(ThinkingTick(ts=_now(), seq=0, run_id="x",
                                     path="a.py", tokens_so_far=1, delta_since_last_tick=1))
      # Publishing a non-coalesce-safe event when full MUST block, not drop.
      with pytest.raises(asyncio.TimeoutError):
          await asyncio.wait_for(
              bus.publish(FileComplete(ts=_now(), seq=0, run_id="x", path="a.py",
                                       finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0})),
              timeout=0.05,
          )
      _ = await q.get()


  @pytest.mark.asyncio
  async def test_eventbus_metrics_drops_oldest_tick_when_full() -> None:
      bus = EventBus(default_capacity=2)
      q = bus.subscribe("Metrics", capacity=2)
      await bus.publish(OutputTick(ts=_now(), seq=0, run_id="x",
                                   path="a.py", tokens_so_far=1, delta_since_last_tick=1))
      await bus.publish(OutputTick(ts=_now(), seq=0, run_id="x",
                                   path="a.py", tokens_so_far=2, delta_since_last_tick=1))
      await bus.publish(OutputTick(ts=_now(), seq=0, run_id="x",
                                   path="a.py", tokens_so_far=3, delta_since_last_tick=1))
      remaining = [(await q.get()).tokens_so_far for _ in range(q.qsize())]
      # Oldest (1) was dropped.
      assert 1 not in remaining


  @pytest.mark.asyncio
  async def test_commandbus_publishes_command_to_subscribers() -> None:
      cb = CommandBus(capacity=8)
      q = cb.subscribe()
      assert q.maxsize == 8
      await cb.publish(Command(type="Pause", target=None, ts=_now()))
      cmd = await q.get()
      assert cmd.type == "Pause"


  @pytest.mark.asyncio
  async def test_eventbus_subscribe_local_only_fires_on_matching_types() -> None:
      # R9: subscribe_local fires only when the published event matches one
      # of the registered types.
      bus = EventBus(default_capacity=8)
      seen: list[BaseEvent] = []

      async def cb(ev: BaseEvent) -> None:
          seen.append(ev)

      handle = bus.subscribe_local(
          "test", (FileError,), cb,
      )
      # Matching type fires.
      await bus.publish(
          FileError(ts=_now(), seq=0, run_id="x", path="a.py",
                    phase="audit", error_kind="boom", error_message="m")
      )
      # Non-matching type does NOT fire.
      await bus.publish(
          FileComplete(ts=_now(), seq=0, run_id="x", path="a.py",
                       finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0})
      )
      handle.unsubscribe()
      assert len(seen) == 1
      assert isinstance(seen[0], FileError)


  def test_events_module_has_nonempty_docstring() -> None:
      from senex import events as ev_mod
      assert ev_mod.__doc__ and ev_mod.__doc__.strip() != ""
  ```

- [x] **Step 1.3.2: Implement `senex/events.py`** with the full event taxonomy + EventBus + CommandBus. Skeleton (you fill in the remaining 25 event subclasses by copying the §5.6 table; all follow the same `Field(default="<ClassName>")` pattern):

  ```python
  """senex.events — Event taxonomy + bounded async EventBus + CommandBus.

  Implements spec §5.6 (event types), §5.6.1 (bus semantics), §5.6.2 (command bus).
  Conventions §3 (bounded queues), §10 (pydantic v2 with extra=forbid).
  """
  from __future__ import annotations

  import asyncio
  from collections.abc import Awaitable, Callable
  from datetime import datetime
  from typing import Any, Literal

  from pydantic import BaseModel, ConfigDict, Field

  DEFAULT_CAPACITY = 1024
  COALESCE_SAFE_TYPES: frozenset[str] = frozenset({"ThinkingTick", "OutputTick"})


  class UnknownEventType(ValueError):
      """Raised when a serialized event has an unknown ``type`` discriminator."""


  class _StrictModel(BaseModel):
      model_config = ConfigDict(extra="forbid")


  class BaseEvent(_StrictModel):
      v: int = 1
      type: str
      ts: datetime
      seq: int = 0
      run_id: str


  class RunStart(BaseEvent):
      type: Literal["RunStart"] = Field(default="RunStart")
      repo: str
      audit_dir: str
      model: str
      lens: str
      lens_version: str
      config_hash: str
      prompt_hash: str
      model_fingerprint: str
      started_at: datetime


  class DiscoveryComplete(BaseEvent):
      type: Literal["DiscoveryComplete"] = Field(default="DiscoveryComplete")
      file_count: int
      skipped: list[tuple[str, str]] = Field(default_factory=list)


  class SymlinkSkipped(BaseEvent):
      type: Literal["SymlinkSkipped"] = Field(default="SymlinkSkipped")
      path: str
      target: str
      reason: str


  class SuspiciousEmptyFinding(BaseEvent):
      type: Literal["SuspiciousEmptyFinding"] = Field(default="SuspiciousEmptyFinding")
      path: str
      loc: str
      reason: str


  class FileStart(BaseEvent):
      type: Literal["FileStart"] = Field(default="FileStart")
      path: str
      idx: int
      total: int


  class FileContextBuilt(BaseEvent):
      type: Literal["FileContextBuilt"] = Field(default="FileContextBuilt")
      path: str
      graph_context_tokens: int


  class GraphContextUnavailable(BaseEvent):
      # Emitted by GitNexusCLIProvider (M2) when the gitnexus CLI fails or
      # returns no data; the auditor proceeds with the sentinel awareness
      # block per §5.8. Non-coalesce-safe (per §5.6.1).
      type: Literal["GraphContextUnavailable"] = Field(default="GraphContextUnavailable")
      path: str
      reason: str


  class FileLLMCall(BaseEvent):
      type: Literal["FileLLMCall"] = Field(default="FileLLMCall")
      path: str
      prompt_tokens: int


  class ThinkingStarted(BaseEvent):
      type: Literal["ThinkingStarted"] = Field(default="ThinkingStarted")
      path: str


  class ThinkingTick(BaseEvent):
      type: Literal["ThinkingTick"] = Field(default="ThinkingTick")
      path: str
      tokens_so_far: int
      delta_since_last_tick: int


  class ThinkingComplete(BaseEvent):
      type: Literal["ThinkingComplete"] = Field(default="ThinkingComplete")
      path: str
      total_thinking_tokens: int
      latency_ms: int


  class OutputStarted(BaseEvent):
      type: Literal["OutputStarted"] = Field(default="OutputStarted")
      path: str


  class OutputTick(BaseEvent):
      type: Literal["OutputTick"] = Field(default="OutputTick")
      path: str
      tokens_so_far: int
      delta_since_last_tick: int


  class OutputComplete(BaseEvent):
      type: Literal["OutputComplete"] = Field(default="OutputComplete")
      path: str
      total_output_tokens: int
      latency_ms: int


  class FindingSummary(_StrictModel):
      """Minimal subset of a finding carried on FileComplete for the TUI's
      findings panel (M9 Task 9.6c) — see R12."""
      priority: Literal["high", "medium", "low", "healthy"]
      title: str
      location: str | None = None


  class FileComplete(BaseEvent):
      type: Literal["FileComplete"] = Field(default="FileComplete")
      path: str
      finding_counts: dict[str, int]
      # Per R12: most-recent finding summary for the file (TUI consumes via M9 Task 9.6c).
      last_finding_summary: FindingSummary | None = None


  class FileError(BaseEvent):
      type: Literal["FileError"] = Field(default="FileError")
      path: str
      phase: str
      error_kind: str
      error_message: str


  class ToolCall(BaseEvent):
      type: Literal["ToolCall"] = Field(default="ToolCall")
      path: str
      tool_name: str
      tool_input: str  # truncated to 512 chars at emission per §5.6.
      call_id: str


  class ToolResult(BaseEvent):
      type: Literal["ToolResult"] = Field(default="ToolResult")
      path: str
      tool_name: str
      call_id: str
      result_tokens: int
      latency_ms: int
      truncated: bool


  class ToolError(BaseEvent):
      type: Literal["ToolError"] = Field(default="ToolError")
      path: str
      tool_name: str
      call_id: str
      kind: Literal["timeout", "path_rejected", "schema_invalid", "subprocess",
                    "unavailable", "internal"]
      error_message: str


  class ToolBudgetExhausted(BaseEvent):
      type: Literal["ToolBudgetExhausted"] = Field(default="ToolBudgetExhausted")
      path: str
      calls_made: int


  class CompactionTriggered(BaseEvent):
      type: Literal["CompactionTriggered"] = Field(default="CompactionTriggered")
      path: str
      message_tokens_before: int
      threshold: int


  class CompactionComplete(BaseEvent):
      type: Literal["CompactionComplete"] = Field(default="CompactionComplete")
      path: str
      message_tokens_after: int
      kept_findings: int


  class CompactionError(BaseEvent):
      type: Literal["CompactionError"] = Field(default="CompactionError")
      path: str
      error_kind: str


  class CrosscutStart(BaseEvent):
      type: Literal["CrosscutStart"] = Field(default="CrosscutStart")


  class CrosscutComplete(BaseEvent):
      type: Literal["CrosscutComplete"] = Field(default="CrosscutComplete")
      theme_count: int


  class RunComplete(BaseEvent):
      type: Literal["RunComplete"] = Field(default="RunComplete")
      duration_seconds: float
      totals: dict[str, int]
      exit_status: int


  class ModelLoadRequested(BaseEvent):
      type: Literal["ModelLoadRequested"] = Field(default="ModelLoadRequested")
      model_id: str
      target_fingerprint: str


  class ModelLoadStarted(BaseEvent):
      type: Literal["ModelLoadStarted"] = Field(default="ModelLoadStarted")
      model_id: str


  class ModelLoadComplete(BaseEvent):
      type: Literal["ModelLoadComplete"] = Field(default="ModelLoadComplete")
      model_id: str
      duration_seconds: float
      fingerprint: str


  class ModelLoadFailed(BaseEvent):
      type: Literal["ModelLoadFailed"] = Field(default="ModelLoadFailed")
      model_id: str
      error_kind: str
      error_message: str


  class ModelUnloadStarted(BaseEvent):
      type: Literal["ModelUnloadStarted"] = Field(default="ModelUnloadStarted")
      model_id: str


  class ModelUnloadComplete(BaseEvent):
      type: Literal["ModelUnloadComplete"] = Field(default="ModelUnloadComplete")
      model_id: str
      duration_seconds: float


  class ModelUnloadSkipped(BaseEvent):
      type: Literal["ModelUnloadSkipped"] = Field(default="ModelUnloadSkipped")
      model_id: str
      reason: Literal["concurrent_holders", "not_loaded_by_us",
                      "auto_unload_disabled", "resumed_run_does_not_own_load"]


  class ModelUnloadFailed(BaseEvent):
      type: Literal["ModelUnloadFailed"] = Field(default="ModelUnloadFailed")
      model_id: str
      error_kind: str
      error_message: str


  class ModelFingerprintChanged(BaseEvent):
      type: Literal["ModelFingerprintChanged"] = Field(default="ModelFingerprintChanged")
      path: str
      expected_fingerprint: str
      observed_fingerprint: str


  class RunLockAcquired(BaseEvent):
      type: Literal["RunLockAcquired"] = Field(default="RunLockAcquired")
      model_fingerprint: str
      holder_count: int


  class RunLockReleased(BaseEvent):
      type: Literal["RunLockReleased"] = Field(default="RunLockReleased")
      model_fingerprint: str
      remaining_holders: int


  ALL_EVENT_TYPES: tuple[type[BaseEvent], ...] = (
      RunStart, DiscoveryComplete, SymlinkSkipped, SuspiciousEmptyFinding,
      FileStart, FileContextBuilt, GraphContextUnavailable, FileLLMCall,
      ThinkingStarted, ThinkingTick, ThinkingComplete,
      OutputStarted, OutputTick, OutputComplete,
      FileComplete, FileError,
      ToolCall, ToolResult, ToolError, ToolBudgetExhausted,
      CompactionTriggered, CompactionComplete, CompactionError,
      CrosscutStart, CrosscutComplete, RunComplete,
      ModelLoadRequested, ModelLoadStarted, ModelLoadComplete, ModelLoadFailed,
      ModelUnloadStarted, ModelUnloadComplete, ModelUnloadSkipped, ModelUnloadFailed,
      ModelFingerprintChanged,
      RunLockAcquired, RunLockReleased,
  )


  class Command(_StrictModel):
      type: Literal["Pause", "Resume", "Skip", "Rerun", "Quit"]
      target: str | None = None
      ts: datetime


  class SubscriptionHandle:
      """Returned by ``EventBus.subscribe_local``; opaque cancel token (R9)."""

      def __init__(self, bus: "EventBus", token: int) -> None:
          self._bus = bus
          self._token = token

      def unsubscribe(self) -> None:
          self._bus._cancel_local(self._token)


  class EventBus:
      """Per-subscriber bounded asyncio queue fan-out (spec §5.6.1).

      Conventions §3: every queue declares an explicit ``maxsize``.
      """

      def __init__(self, default_capacity: int = DEFAULT_CAPACITY) -> None:
          self._default_capacity = default_capacity
          self._subs: dict[str, tuple[asyncio.Queue[BaseEvent], int]] = {}
          self._local_subs: dict[
              int, tuple[tuple[type[BaseEvent], ...], "Callable[[BaseEvent], Awaitable[None]]"]
          ] = {}
          self._next_local_token = 0
          self._seq_counter = 0

      def subscribe(self, name: str, capacity: int | None = None) -> asyncio.Queue[BaseEvent]:
          cap = capacity if capacity is not None else self._default_capacity
          q: asyncio.Queue[BaseEvent] = asyncio.Queue(maxsize=cap)
          self._subs[name] = (q, cap)
          return q

      def subscribe_local(
          self,
          name: str,
          event_types: type[BaseEvent] | tuple[type[BaseEvent], ...],
          callback: "Callable[[BaseEvent], Awaitable[None]]",
          capacity: int = 1024,
      ) -> SubscriptionHandle:
          """Subscribe a callback to specific event types.

          Internal helper for tests and TUI widgets that don't need a Queue
          (R9). Only fires for events whose ``type(event)`` matches one of
          the provided ``event_types``. Returns a handle for unsubscription.
          ``capacity`` is reserved for future buffering — v1 invokes the
          callback synchronously during ``publish``.
          """
          types_tuple = (
              (event_types,) if isinstance(event_types, type) else tuple(event_types)
          )
          token = self._next_local_token
          self._next_local_token += 1
          self._local_subs[token] = (types_tuple, callback)
          return SubscriptionHandle(self, token)

      def _cancel_local(self, token: int) -> None:
          self._local_subs.pop(token, None)

      async def publish(self, event: BaseEvent) -> None:
          # Stamp seq monotonically. Subclasses MUST NOT generate their own.
          event.seq = self._seq_counter
          self._seq_counter += 1
          for name, (q, _cap) in self._subs.items():
              await self._dispatch(name, q, event)
          # R9: fire any locally-registered callbacks for matching event types.
          for types_tuple, cb in list(self._local_subs.values()):
              if isinstance(event, types_tuple):
                  await cb(event)

      async def _dispatch(
          self, name: str, q: asyncio.Queue[BaseEvent], event: BaseEvent
      ) -> None:
          # Slow-subscriber policy per spec §5.6.1.
          if name == "DiskWriter":
              await q.put(event)  # blocks the publisher.
              return
          if name == "Tui":
              if event.type in COALESCE_SAFE_TYPES:
                  try:
                      q.put_nowait(event)
                  except asyncio.QueueFull:
                      return  # drop this tick.
              else:
                  await q.put(event)  # block on non-coalesce-safe.
              return
          if name == "Metrics":
              if event.type in COALESCE_SAFE_TYPES:
                  if q.full():
                      try:
                          _ = q.get_nowait()  # drop oldest tick.
                      except asyncio.QueueEmpty:
                          pass
                  try:
                      q.put_nowait(event)
                  except asyncio.QueueFull:
                      return
              else:
                  await q.put(event)
              return
          # Unknown subscriber: default to block (data integrity > liveness).
          await q.put(event)


  class CommandBus:
      """TUI → auditor command channel (spec §5.6.2)."""

      def __init__(self, capacity: int = 64) -> None:
          self._capacity = capacity
          self._queues: list[asyncio.Queue[Command]] = []

      def subscribe(self) -> asyncio.Queue[Command]:
          q: asyncio.Queue[Command] = asyncio.Queue(maxsize=self._capacity)
          self._queues.append(q)
          return q

      async def publish(self, command: Command) -> None:
          for q in self._queues:
              await q.put(command)
  ```

- [x] **Step 1.3.3: Run targeted tests:**
  ```bash
  pytest tests/unit/test_events.py -v
  ```
  Expected (literal lines, abbreviated for the parametrized one):
  ```
  PASSED tests/unit/test_events.py::test_event_count_is_at_least_35
  PASSED tests/unit/test_events.py::test_event_runstart_serialization_roundtrip
  PASSED tests/unit/test_events.py::test_every_event_subclass_has_type_literal[RunStart]
  ... (one per event class)
  PASSED tests/unit/test_events.py::test_event_unknown_type_raises_validation_error
  PASSED tests/unit/test_events.py::test_eventbus_subscribe_returns_bounded_queue
  PASSED tests/unit/test_events.py::test_eventbus_publish_assigns_monotonic_seq
  PASSED tests/unit/test_events.py::test_eventbus_diskwriter_blocks_publisher_when_full
  PASSED tests/unit/test_events.py::test_eventbus_tui_drops_thinkingtick_when_full
  PASSED tests/unit/test_events.py::test_eventbus_tui_never_drops_runstart_filecomplete_etc
  PASSED tests/unit/test_events.py::test_eventbus_metrics_drops_oldest_tick_when_full
  PASSED tests/unit/test_events.py::test_commandbus_publishes_command_to_subscribers
  PASSED tests/unit/test_events.py::test_events_module_has_nonempty_docstring
  ```

- [x] **Step 1.3.4: Lint + type:**
  ```bash
  ruff check senex/ tests/
  mypy senex/
  ```

- [x] **Step 1.3.5: Commit:**
  ```bash
  git add senex/events.py tests/unit/test_events.py
  git commit -m "feat(M1): event taxonomy + bounded async event bus"
  ```

**Pitfalls (Task 1.3):**
- The seq counter MUST live on the bus, not on `BaseEvent`. Subclasses default `seq=0`; the bus stamps the real value at `publish()`. The `test_eventbus_publish_assigns_monotonic_seq` test catches the wrong placement.
- `asyncio.Queue` without `maxsize` is unbounded and silently violates conventions §3. The `test_eventbus_subscribe_returns_bounded_queue` test asserts `q.maxsize == 4` — this fails loudly if you pass `maxsize=0` (which means unbounded in asyncio).
- The TUI policy is `ThinkingTick`/`OutputTick` only — NOT all tick-shaped events. `RunStart`, `FileComplete`, `FileError`, `RunComplete` MUST never be dropped. `test_eventbus_tui_never_drops_runstart_filecomplete_etc` enforces this.
- The Metrics policy "drop oldest" requires a `get_nowait()` BEFORE `put_nowait()` when the queue is full. If you reverse the order (`put` first, catch QueueFull), you'll drop the NEW event instead of the oldest — and miss the test.
- Pydantic v2 `Literal["X"] = Field(default="X")` is the only pattern that satisfies both the type-narrowing for the discriminator AND the runtime default. Using just `type: Literal["X"]` requires the caller to pass `type="X"` always.

**Definition of done (Task 1.3):**
- `pytest tests/unit/test_events.py -v` shows all PASSED (≥ 48 tests including parametrized).
- `len(ALL_EVENT_TYPES) == 37` (36+ per spec; this implementation includes `GraphContextUnavailable` per R7).
- `ruff check senex/ tests/` and `mypy senex/` exit 0.
- `git diff --stat HEAD~1` lists exactly: `senex/events.py`, `tests/unit/test_events.py`.

---

### Task 1.4: Secret redactor

**Files:**
- Create: `senex/secret_redactor.py`
- Create: `tests/unit/test_secret_redactor.py`

**Conventions:** §5 (security — no hardcoded creds even in tests; placeholders only), §10, §11, §13.

- [x] **Step 1.4.1: Failing test file** at `tests/unit/test_secret_redactor.py` (parametrized — every pattern class has a literal test case):

  ```python
  """Tests for senex.secret_redactor — pattern set + redact_dict.

  Conventions §5: secret-shaped strings in tests are placeholders, never real keys.
  """
  from __future__ import annotations

  import pytest

  from senex.secret_redactor import SecretRedactor


  @pytest.fixture(scope="module")
  def redactor() -> SecretRedactor:
      return SecretRedactor()


  @pytest.mark.parametrize(
      "raw, expected_marker",
      [
          # AWS access key (16-char IAM access-key ID): AKIA + 16 alphanum.
          ("AKIAIOSFODNN7EXAMPLE", "[REDACTED:aws_access_key]"),
          # GitHub PAT classic: ghp_ + 36 alphanum.
          ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "[REDACTED:github_pat]"),
          # GitHub OAuth: gho_ + 36 alphanum.
          ("gho_abcdefghijklmnopqrstuvwxyz0123456789", "[REDACTED:github_pat]"),
          # OpenAI key.
          ("sk-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE12", "[REDACTED:llm_api_key]"),
          # Anthropic key.
          ("sk-ant-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE12", "[REDACTED:llm_api_key]"),
          # JWT (three base64 segments — fixture is built at runtime so the literal
          # bytes do not appear as a single token in this source file).
          (
              "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0In0" + "."
              + "Sf" + "lKxwRJSMeKKF" + "2QT4fwpMeJf36",
              "[REDACTED:jwt]",
          ),
          # PEM block.
          (
              "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
              "[REDACTED:pem]",
          ),
          # env-style.
          ("DATABASE_PASSWORD=hunter2-with-letters", "[REDACTED:env_secret]"),
          ("API_TOKEN=abcdef0123456789", "[REDACTED:env_secret]"),
      ],
  )
  def test_redactor_pattern_class_redacts(
      redactor: SecretRedactor, raw: str, expected_marker: str
  ) -> None:
      out = redactor.redact(raw)
      assert expected_marker in out
      # The literal sensitive substring must be gone.
      assert "FAKEFAKEFAKEFAKE" not in out  # OpenAI body removed.
      assert "AKIAIOSFODNN7EXAMPLE" not in out
      assert "MIIEowIBAAKCAQEA" not in out


  def test_redactor_priority_pem_before_env(redactor: SecretRedactor) -> None:
      # Generic env pattern MUST NOT eat a PEM block: PEM matches first.
      mixed = "PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\nbody\n-----END RSA PRIVATE KEY-----"
      out = redactor.redact(mixed)
      assert "[REDACTED:pem]" in out
      # Body content gone.
      assert "BEGIN RSA" not in out


  def test_redactor_redact_dict_redacts_known_key_names(redactor: SecretRedactor) -> None:
      d = {
          "api_key": "sk-realkey-NOT-REAL-1234567890",
          "username": "alice",
          "nested": {"github_token": "ghp_NotRealTokenButShaped0000000000000000"},
          "auth_secret": "shouldredact",
          "password": "hunter2",
      }
      out = redactor.redact_dict(d)
      assert out["api_key"] == "[REDACTED]"
      assert out["nested"]["github_token"] == "[REDACTED]"
      assert out["auth_secret"] == "[REDACTED]"
      assert out["password"] == "[REDACTED]"
      assert out["username"] == "alice"  # not a sensitive key.


  def test_redactor_redact_dict_does_not_mutate_input(redactor: SecretRedactor) -> None:
      d = {"api_key": "sk-FAKE"}
      _ = redactor.redact_dict(d)
      assert d["api_key"] == "sk-FAKE"  # caller's dict untouched.


  def test_redactor_handles_empty_and_none(redactor: SecretRedactor) -> None:
      assert redactor.redact("") == ""
      assert redactor.redact("plain text without secrets") == "plain text without secrets"


  def test_redactor_module_has_nonempty_docstring() -> None:
      from senex import secret_redactor as sr
      assert sr.__doc__ and sr.__doc__.strip() != ""
  ```

- [x] **Step 1.4.2: Implement `senex/secret_redactor.py`.** Use the `regex` library (NOT stdlib `re`) per conventions §5 (timeout-capable). Patterns ordered most-specific-first:

  ```python
  """senex.secret_redactor — regex-based redaction for persisted strings.

  Implements spec §5.10 and threat-model row §11.1 (secret leakage).
  Patterns are ordered most-specific-first so PEM blocks win over generic
  env-style matches (see test_redactor_priority_pem_before_env).
  """
  from __future__ import annotations

  from copy import deepcopy
  from fnmatch import fnmatchcase
  from typing import Any

  import regex as re  # `regex` library supports timeouts; conventions §5.

  _PATTERNS: tuple[tuple[str, str], ...] = (
      # PEM block (most specific — multi-line).
      ("pem", r"-----BEGIN [A-Z ]+-----[\s\S]*?-----END [A-Z ]+-----"),
      # JWT: three base64url segments.
      ("jwt", r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]{20,}\b"),
      # AWS access key id.
      ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
      # GitHub PAT (classic + OAuth).
      ("github_pat", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
      # OpenAI / Anthropic style llm api key.
      ("llm_api_key", r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b"),
      # Generic env-style: KEY_WITH_SECRET=value.
      (
          "env_secret",
          r"\b(?P<key>[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|KEY|PASS))\s*=\s*[^\s\"'`]+",
      ),
  )

  _SENSITIVE_KEY_GLOBS: tuple[str, ...] = (
      "api_key", "*_token", "*_secret", "password*", "*_key",
  )


  class SecretRedactor:
      """Apply named regex patterns to redact secret-shaped strings.

      Pattern compile is done once at init (conventions §14: cache compiled regexes).
      """

      def __init__(self, sensitive_key_globs: tuple[str, ...] = _SENSITIVE_KEY_GLOBS) -> None:
          self._compiled: list[tuple[str, re.Pattern[str]]] = [
              (name, re.compile(pat, flags=re.MULTILINE))
              for name, pat in _PATTERNS
          ]
          self._sensitive_globs = sensitive_key_globs

      def redact(self, text: str) -> str:
          if not text:
              return text
          out = text
          for name, pat in self._compiled:
              out = pat.sub(f"[REDACTED:{name}]", out)
          return out

      def redact_dict(
          self, d: dict[str, Any], keys: tuple[str, ...] | None = None
      ) -> dict[str, Any]:
          """Return a deep-copied dict with sensitive-named keys replaced by ``[REDACTED]``.

          Args:
              d: input dict (untouched; deep-copied first).
              keys: glob list of sensitive key names (defaults to module-level globs).

          Returns:
              new dict with sensitive values replaced; non-sensitive entries pass through.
          """
          globs = keys if keys is not None else self._sensitive_globs
          out = deepcopy(d)
          self._scrub(out, globs)
          return out

      def _scrub(self, node: Any, globs: tuple[str, ...]) -> None:
          if isinstance(node, dict):
              for k, v in list(node.items()):
                  if isinstance(k, str) and any(fnmatchcase(k, g) for g in globs):
                      node[k] = "[REDACTED]"
                  else:
                      self._scrub(v, globs)
          elif isinstance(node, list):
              for item in node:
                  self._scrub(item, globs)
  ```

- [x] **Step 1.4.3: Run tests:**
  ```bash
  pytest tests/unit/test_secret_redactor.py -v
  ```
  Expected: every parametrized case PASSED, plus the priority/dict/empty/docstring tests.

- [x] **Step 1.4.4: Lint + type:**
  ```bash
  ruff check senex/ tests/
  mypy senex/
  ```

- [x] **Step 1.4.5: Commit:**
  ```bash
  git add senex/secret_redactor.py tests/unit/test_secret_redactor.py
  git commit -m "feat(M1): secret redactor with named pattern set"
  ```

**Pitfalls (Task 1.4):**
- Pattern order is load-bearing. Generic `env_secret` would otherwise eat the value half of `PRIVATE_KEY=-----BEGIN ...` (since `=` is followed by non-whitespace). The PEM pattern MUST be first; the test `test_redactor_priority_pem_before_env` enforces this.
- The `regex` library (third-party) is required, not stdlib `re`, because conventions §5 mandates timeout-capable regex on user-supplied / external-source patterns. The redactor patterns are not user-supplied today, but using `regex` keeps the API consistent with `tools/safety.py` later.
- `redact_dict` MUST NOT mutate the caller's dict — `test_redactor_redact_dict_does_not_mutate_input` enforces this. `deepcopy` first.
- The JWT pattern's third-segment minimum length (`{20,}`) prevents false positives on `a.b.c`. Real JWT signatures are far longer.
- `_SENSITIVE_KEY_GLOBS` includes `*_key` which catches `api_key`, `auth_key`, etc. but NOT bare `key` (which would be too aggressive). The test asserts `username` survives.

**Definition of done (Task 1.4):**
- All 9 parametrized + 5 standalone tests PASSED.
- `ruff check senex/ tests/` exits 0.
- `mypy senex/` exits 0.
- `git diff --stat HEAD~1` lists exactly: `senex/secret_redactor.py`, `tests/unit/test_secret_redactor.py`.

---

### Task 1.5: Lens loader

**Files:**
- Create: `senex/lens.py`
- Create: `senex/lens/correctness/lens.toml`
- Create: `senex/lens/correctness/tools.toml`
- Create: `senex/lens/correctness/system_senior_dev.md` (placeholder; full prompt copied verbatim from spec §5.1 in M3)
- Create: `senex/lens/correctness/crosscut.md` (placeholder)
- Create: `senex/lens/correctness/renderer.md` (placeholder)
- Create: `tests/unit/test_lens.py`

**Conventions:** §1 (pathlib), §4 (named exceptions), §10 (pydantic for validation), §11, §13.

- [x] **Step 1.5.1: Write `senex/lens/correctness/lens.toml`:**

  ```toml
  schema_version = 1
  name = "correctness"
  version = "1.0.0"
  description = "Senior-developer correctness review lens"
  system_prompt = "system_senior_dev.md"
  response_schema = "audit_response.schema.json"
  crosscut_prompt = "crosscut.md"
  crosscut_schema = "crosscut_response.schema.json"
  renderer_template = "renderer.md"
  category_taxonomy = [
    "Correctness",
    "Robustness & Error Handling",
    "Security",
    "Maintainability",
    "Testability",
  ]
  ```

- [x] **Step 1.5.2: Write `senex/lens/correctness/tools.toml`:**

  ```toml
  enabled_tools = [
    "gitnexus_query",
    "gitnexus_context",
    "gitnexus_impact",
    "read_file",
    "grep",
    "search_code",
  ]
  ```

- [x] **Step 1.5.3: Write the three placeholder `.md` files** (M3 fills them with the verbatim prompts from spec §5.1, §5.4.1 prompt, §7.1 renderer template). For M1 the content only needs to make the fingerprint stable:

  ```
  # senex/lens/correctness/system_senior_dev.md
  PLACEHOLDER — replaced verbatim from spec §5.1 in M3.
  ```
  ```
  # senex/lens/correctness/crosscut.md
  PLACEHOLDER — cross-cutting prompt; replaced in M3.
  ```
  ```
  # senex/lens/correctness/renderer.md
  PLACEHOLDER — per-file markdown template; replaced in M7.
  ```

- [x] **Step 1.5.4: Failing test file** at `tests/unit/test_lens.py`:

  ```python
  """Tests for senex.lens — Lens dataclass + Lens.load(name)."""
  from __future__ import annotations

  import pytest

  from senex.lens import Lens, LensNotFound, LensValidationError


  def test_lens_load_correctness_returns_lens_with_six_tools() -> None:
      lens = Lens.load("correctness")
      assert lens.name == "correctness"
      assert lens.version == "1.0.0"
      assert lens.tools == [
          "gitnexus_query", "gitnexus_context", "gitnexus_impact",
          "read_file", "grep", "search_code",
      ]


  def test_lens_load_resolves_prompt_paths() -> None:
      lens = Lens.load("correctness")
      assert lens.system_prompt_path.name == "system_senior_dev.md"
      assert lens.system_prompt_path.exists()
      assert lens.crosscut_prompt_path.exists()
      assert lens.renderer_template_path.exists()


  def test_lens_load_computes_stable_fingerprint() -> None:
      a = Lens.load("correctness")
      b = Lens.load("correctness")
      assert a.fingerprint == b.fingerprint
      # Sanity: must look like a sha256 hex digest.
      assert len(a.fingerprint) == 64


  def test_lens_load_unknown_name_raises_lensnotfound() -> None:
      with pytest.raises(LensNotFound):
          Lens.load("does_not_exist")


  def test_lens_load_invalid_lens_toml_raises_validation_error(
      tmp_path, monkeypatch
  ) -> None:
      bad = tmp_path / "bad" / "lens.toml"
      bad.parent.mkdir()
      bad.write_text("schema_version = 99\n", encoding="utf-8")
      (tmp_path / "bad" / "tools.toml").write_text("enabled_tools = []\n", encoding="utf-8")

      from senex import lens as lens_mod
      monkeypatch.setattr(lens_mod, "_lens_root", lambda: tmp_path)
      with pytest.raises(LensValidationError):
          Lens.load("bad")


  def test_lens_module_has_nonempty_docstring() -> None:
      from senex import lens as lens_mod
      assert lens_mod.__doc__ and lens_mod.__doc__.strip() != ""
  ```

- [x] **Step 1.5.5: Implement `senex/lens.py`:**

  ```python
  """senex.lens — Lens dataclass + loader for ``senex/lens/<name>/``.

  Implements spec §5.0 (Lens abstraction) and §5.11.2 (per-lens tool packs).
  Each Lens is loaded from a directory containing ``lens.toml`` and ``tools.toml``;
  the lens fingerprint is sha256 over the concatenated bytes of all files referenced.
  """
  from __future__ import annotations

  import hashlib
  import tomllib
  from dataclasses import dataclass, field
  from pathlib import Path
  from typing import Any

  from pydantic import BaseModel, ConfigDict, Field, ValidationError


  class LensNotFound(FileNotFoundError):
      """Raised when ``Lens.load(name)`` cannot find ``senex/lens/<name>/lens.toml``."""


  class LensValidationError(ValueError):
      """Raised when a lens.toml or tools.toml fails schema validation."""


  class _LensTomlSchema(BaseModel):
      model_config = ConfigDict(extra="forbid")
      schema_version: int = Field(ge=1, le=1)  # v1 only.
      name: str
      version: str
      description: str
      system_prompt: str
      response_schema: str
      crosscut_prompt: str
      crosscut_schema: str
      renderer_template: str
      category_taxonomy: list[str] = Field(min_length=1)


  class _ToolsTomlSchema(BaseModel):
      model_config = ConfigDict(extra="forbid")
      enabled_tools: list[str]


  @dataclass(frozen=True)
  class Lens:
      """A loaded Lens (spec §5.0)."""

      name: str
      version: str
      description: str
      system_prompt_path: Path
      response_schema_path: Path
      crosscut_prompt_path: Path
      crosscut_schema_path: Path
      renderer_template_path: Path
      category_taxonomy: tuple[str, ...]
      tools: list[str] = field(default_factory=list)
      fingerprint: str = ""

      @classmethod
      def load(cls, name: str) -> Lens:
          """Load + validate the lens directory at ``senex/lens/<name>/``.

          Raises:
              LensNotFound: directory or lens.toml missing.
              LensValidationError: schema mismatch on lens.toml or tools.toml.
          """
          root = _lens_root() / name
          lens_toml = root / "lens.toml"
          tools_toml = root / "tools.toml"
          if not lens_toml.exists():
              raise LensNotFound(f"lens.toml not found at {lens_toml}")
          try:
              ldata: dict[str, Any] = tomllib.loads(lens_toml.read_text(encoding="utf-8"))
              tdata: dict[str, Any] = tomllib.loads(tools_toml.read_text(encoding="utf-8"))
              ls = _LensTomlSchema.model_validate(ldata)
              ts = _ToolsTomlSchema.model_validate(tdata)
          except ValidationError as exc:
              raise LensValidationError(str(exc)) from exc

          system_prompt_path = root / ls.system_prompt
          crosscut_prompt_path = root / ls.crosscut_prompt
          renderer_template_path = root / ls.renderer_template
          # Schemas live in senex/schema/, not in the lens dir.
          schema_root = _schema_root()
          response_schema_path = schema_root / ls.response_schema
          crosscut_schema_path = schema_root / ls.crosscut_schema

          fingerprint = _hash_paths(
              [
                  lens_toml, tools_toml,
                  system_prompt_path, crosscut_prompt_path, renderer_template_path,
              ]
          )

          return cls(
              name=ls.name,
              version=ls.version,
              description=ls.description,
              system_prompt_path=system_prompt_path,
              response_schema_path=response_schema_path,
              crosscut_prompt_path=crosscut_prompt_path,
              crosscut_schema_path=crosscut_schema_path,
              renderer_template_path=renderer_template_path,
              category_taxonomy=tuple(ls.category_taxonomy),
              tools=list(ts.enabled_tools),
              fingerprint=fingerprint,
          )


  def _lens_root() -> Path:
      # Resolved relative to the senex package install location.
      return Path(__file__).parent / "lens"


  def _schema_root() -> Path:
      return Path(__file__).parent / "schema"


  def _hash_paths(paths: list[Path]) -> str:
      h = hashlib.sha256()
      for p in paths:
          if p.exists():
              h.update(p.read_bytes())
          # Missing files contribute nothing; their absence is encoded by the schema test.
      return h.hexdigest()
  ```

- [x] **Step 1.5.6: Run tests:**
  ```bash
  pytest tests/unit/test_lens.py -v
  ```
  Expected: 6 PASSED.

- [x] **Step 1.5.7: Lint + type:**
  ```bash
  ruff check senex/ tests/
  mypy senex/
  ```

- [x] **Step 1.5.8: Commit:**
  ```bash
  git add senex/lens.py senex/lens/correctness/ tests/unit/test_lens.py
  git commit -m "feat(M1): Lens loader with versioned prompts + tool packs"
  ```

**Pitfalls (Task 1.5):**
- The fingerprint MUST be stable across reloads. If you walk a directory and hash by `Path.glob("*")`, file ordering varies by filesystem — sort the path list explicitly or, as in the implementation, hash a fixed enumerated list.
- Schema files (`audit_response.schema.json`, etc.) live in `senex/schema/`, NOT inside the lens directory. The lens.toml `response_schema` field is just a filename; `Lens.load()` resolves it against `_schema_root()`. Putting them in the lens directory would force every lens to ship copies — refactor blocker.
- Placeholders for `system_senior_dev.md` / `crosscut.md` / `renderer.md` are intentional in M1: their content is filled in M3/M7 with the verbatim spec prompts. The fingerprint will change at that point — downstream code (M3+) must NOT pin to the M1 fingerprint.
- `Lens` is a frozen dataclass (immutable). Do not add mutable defaults except via `field(default_factory=...)` — the linter catches this.

**Definition of done (Task 1.5):**
- `pytest tests/unit/test_lens.py -v` shows 6 PASSED.
- `python -c "from senex.lens import Lens; l = Lens.load('correctness'); print(l.tools)"` prints exactly the 6-tool list.
- `ruff check senex/ tests/` and `mypy senex/` exit 0.
- `git diff --stat HEAD~1` lists exactly the new files in `senex/lens/correctness/` + `senex/lens.py` + `tests/unit/test_lens.py`.

---

### Task 1.6: Runlock (file-based interprocess refcount)

**Files:**
- Create: `senex/runlock.py`
- Create: `tests/unit/test_runlock.py`

**Conventions:** §3 (no locks held across `await` — the lockfile critical section is sync-only by design), §4 (named exceptions), §5 (path safety), §8 (atomic writes), §11, §13.

- [x] **Step 1.6.1: Failing test file** at `tests/unit/test_runlock.py`:

  ```python
  """Tests for senex.runlock — interprocess refcount file lock with stale-PID prune."""
  from __future__ import annotations

  import json
  import os
  from pathlib import Path

  import pytest

  from senex.runlock import (
      CorruptRunLock,
      RunLock,
      _is_pid_alive,
  )


  def test_runlock_acquire_creates_lockfile_with_one_holder(tmp_path: Path) -> None:
      n = RunLock.acquire(
          fingerprint="sha256:abc",
          run_id="run1",
          pid=os.getpid(),
          loaded_by_us=True,
          root=tmp_path,
      )
      assert n == 1
      lock = tmp_path / "sha256_abc.lock"
      assert lock.exists()
      data = json.loads(lock.read_text(encoding="utf-8"))
      assert len(data["holders"]) == 1
      assert data["holders"][0]["run_id"] == "run1"


  def test_runlock_acquire_twice_returns_count_two(tmp_path: Path) -> None:
      RunLock.acquire("fp1", "run1", os.getpid(), True, root=tmp_path)
      n = RunLock.acquire("fp1", "run2", os.getpid(), False, root=tmp_path)
      assert n == 2


  def test_runlock_release_removes_holder(tmp_path: Path) -> None:
      RunLock.acquire("fp1", "run1", os.getpid(), True, root=tmp_path)
      RunLock.acquire("fp1", "run2", os.getpid(), False, root=tmp_path)
      remaining = RunLock.release("fp1", "run1", root=tmp_path)
      assert remaining == 1


  def test_runlock_release_last_holder_deletes_file(tmp_path: Path) -> None:
      RunLock.acquire("fp1", "run1", os.getpid(), True, root=tmp_path)
      remaining = RunLock.release("fp1", "run1", root=tmp_path)
      assert remaining == 0
      assert not (tmp_path / "fp1.lock").exists()


  def test_runlock_acquire_prunes_stale_pid_entry(tmp_path: Path) -> None:
      # Pre-seed a stale entry with a PID that does not exist (use INT_MAX as a sentinel).
      stale_pid = 2**31 - 1
      lock = tmp_path / "fp1.lock"
      lock.write_text(
          json.dumps(
              {
                  "schema_version": 1,
                  "model_id": "x",
                  "model_fingerprint": "fp1",
                  "holders": [
                      {"run_id": "dead", "pid": stale_pid,
                       "started_at": "2026-01-01T00:00:00Z", "loaded_by_us": True}
                  ],
              }
          ),
          encoding="utf-8",
      )
      assert not _is_pid_alive(stale_pid)
      n = RunLock.acquire("fp1", "live", os.getpid(), False, root=tmp_path)
      assert n == 1  # stale dropped before append.


  def test_runlock_corrupt_json_renamed_and_recreated(tmp_path: Path) -> None:
      lock = tmp_path / "fp1.lock"
      lock.write_text("{ this is not json", encoding="utf-8")
      # Acquire MUST salvage by renaming corrupt and starting over.
      n = RunLock.acquire("fp1", "live", os.getpid(), False, root=tmp_path)
      assert n == 1
      corrupt = list(tmp_path.glob("fp1.lock.corrupt-*"))
      assert len(corrupt) == 1


  def test_runlock_release_unknown_run_id_raises_corruptrunlock(tmp_path: Path) -> None:
      RunLock.acquire("fp1", "run1", os.getpid(), True, root=tmp_path)
      with pytest.raises(CorruptRunLock):
          RunLock.release("fp1", "run-never-acquired", root=tmp_path)


  def test_runlock_module_has_nonempty_docstring() -> None:
      from senex import runlock as rl
      assert rl.__doc__ and rl.__doc__.strip() != ""
  ```

- [x] **Step 1.6.2: Implement `senex/runlock.py`:**

  ```python
  """senex.runlock — Interprocess refcount file lock with stale-PID prune.

  Implements spec §5.5.2.2 (runlock semantics) and §5.5.2.4 (failure modes).
  Conventions §8: atomic write via tmp + fsync + rename.
  """
  from __future__ import annotations

  import json
  import os
  import re
  import time
  from datetime import datetime, timezone
  from pathlib import Path
  from typing import Any

  import portalocker


  class CorruptRunLock(RuntimeError):
      """Raised when the lockfile is structurally invalid in a way the caller must surface."""


  _SAFE_FP = re.compile(r"[^A-Za-z0-9_.-]")


  def _lock_path(fingerprint: str, root: Path) -> Path:
      # Sanitize fingerprint into a filesystem-safe name.
      safe = _SAFE_FP.sub("_", fingerprint)
      return root / f"{safe}.lock"


  def _is_pid_alive(pid: int) -> bool:
      """Return True if ``pid`` is alive on the current OS."""
      if pid <= 0:
          return False
      if os.name == "nt":
          # Windows: use OpenProcess via ctypes.
          import ctypes  # local import: platform-conditional per conventions §1.

          PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
          h = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
              PROCESS_QUERY_LIMITED_INFORMATION, False, pid
          )
          if not h:
              return False
          ctypes.windll.kernel32.CloseHandle(h)  # type: ignore[attr-defined]
          return True
      try:
          os.kill(pid, 0)
      except ProcessLookupError:
          return False
      except PermissionError:
          return True  # exists but we don't own it.
      return True


  def _atomic_write(path: Path, data: str) -> None:
      tmp = path.with_suffix(path.suffix + ".tmp")
      with tmp.open("w", encoding="utf-8", newline="\n") as f:
          f.write(data)
          f.flush()
          os.fsync(f.fileno())
      os.replace(tmp, path)  # atomic on POSIX + Windows when same volume.


  def _prune_stale(holders: list[dict[str, Any]]) -> list[dict[str, Any]]:
      return [h for h in holders if _is_pid_alive(int(h["pid"]))]


  class RunLock:
      """File-based refcount lock under ``~/.senex/locks/`` (§5.5.2.2)."""

      @classmethod
      def acquire(
          cls,
          fingerprint: str,
          run_id: str,
          pid: int,
          loaded_by_us: bool,
          root: Path | None = None,
      ) -> int:
          """Append the (run_id, pid) holder; return new holder count."""
          root = root or _default_root()
          root.mkdir(parents=True, exist_ok=True)
          lock = _lock_path(fingerprint, root)

          with portalocker.Lock(
              str(lock) + ".portalock", mode="w", timeout=10
          ):
              data = cls._read_or_recreate(lock, fingerprint)
              data["holders"] = _prune_stale(data.get("holders", []))
              data["holders"].append(
                  {
                      "run_id": run_id,
                      "pid": pid,
                      "started_at": datetime.now(tz=timezone.utc).isoformat(),
                      "loaded_by_us": loaded_by_us,
                  }
              )
              _atomic_write(lock, json.dumps(data, indent=2))
              return len(data["holders"])

      @classmethod
      def release(
          cls,
          fingerprint: str,
          run_id: str,
          root: Path | None = None,
      ) -> int:
          root = root or _default_root()
          lock = _lock_path(fingerprint, root)
          if not lock.exists():
              raise CorruptRunLock(f"release on nonexistent lock: {lock}")
          with portalocker.Lock(
              str(lock) + ".portalock", mode="w", timeout=10
          ):
              data = cls._read_or_recreate(lock, fingerprint)
              before = len(data["holders"])
              data["holders"] = [h for h in data["holders"] if h["run_id"] != run_id]
              if len(data["holders"]) == before:
                  raise CorruptRunLock(
                      f"release({run_id}) but no matching holder in {lock}"
                  )
              if not data["holders"]:
                  lock.unlink(missing_ok=True)
                  return 0
              _atomic_write(lock, json.dumps(data, indent=2))
              return len(data["holders"])

      @staticmethod
      def _read_or_recreate(lock: Path, fingerprint: str) -> dict[str, Any]:
          if not lock.exists():
              return {
                  "schema_version": 1,
                  "model_id": "",
                  "model_fingerprint": fingerprint,
                  "holders": [],
              }
          try:
              return json.loads(lock.read_text(encoding="utf-8"))
          except json.JSONDecodeError:
              ts = int(time.time())
              corrupt = lock.with_suffix(lock.suffix + f".corrupt-{ts}")
              os.replace(lock, corrupt)
              return {
                  "schema_version": 1,
                  "model_id": "",
                  "model_fingerprint": fingerprint,
                  "holders": [],
              }


  def _default_root() -> Path:
      return Path.home() / ".senex" / "locks"
  ```

- [x] **Step 1.6.3: Run tests:**
  ```bash
  pytest tests/unit/test_runlock.py -v
  ```
  Expected: 8 PASSED.

- [x] **Step 1.6.4: Lint + type:**
  ```bash
  ruff check senex/ tests/
  mypy senex/
  ```

- [x] **Step 1.6.5: Commit:**
  ```bash
  git add senex/runlock.py tests/unit/test_runlock.py
  git commit -m "feat(M1): runlock interprocess refcount with stale-PID prune"
  ```

**Pitfalls (Task 1.6):**
- The `portalocker.Lock` MUST wrap the read+modify+write critical section in its entirety. If you release the advisory lock between read and write, two acquires can race and one's holder entry is lost. The test for two-holder count would still pass spuriously if the writes happen to interleave correctly — but a stress test (manually run with N=20 threads) catches the regression.
- `os.replace()` is atomic on Windows ONLY when source and destination are on the same volume. The implementation uses `path.with_suffix(suffix + ".tmp")` which is always same-directory — keep it that way.
- The `_is_pid_alive` check must NOT use `psutil` (extra dep); the Windows branch uses ctypes via `kernel32.OpenProcess`. Conventions §15 (dependency hygiene) blocks new deps without explicit user approval.
- The "corrupt JSON salvage" path silently renames the bad file. Logging is added in M3 (the redactor + logging stack arrives there). For now the rename leaves a forensic artifact (`.corrupt-<ts>`) — `test_runlock_corrupt_json_renamed_and_recreated` asserts its presence.
- `release()` on an unknown run_id raises `CorruptRunLock` rather than silently no-op — fail closed per conventions §4. The test enforces this.
- Sanitizing `fingerprint` for the filesystem (replacing `:` etc. via `_SAFE_FP`) is required because real fingerprints look like `sha256:abc...`. Without sanitization, Windows refuses the filename. The tests use both colon-bearing (`sha256:abc`) and plain (`fp1`) fingerprints.

**Definition of done (Task 1.6):**
- `pytest tests/unit/test_runlock.py -v` shows 8 PASSED.
- Manual concurrency check: spawn two threads each calling `RunLock.acquire("same_fp", "run-N", os.getpid(), True, root=tmp)` 100 times; final holder count == 200.
- `ruff check senex/ tests/` and `mypy senex/` exit 0.
- `git diff --stat HEAD~1` lists exactly: `senex/runlock.py`, `tests/unit/test_runlock.py`.

---

### Task 1.7: Checkpoint state machine

**Files:**
- Create: `senex/checkpoint.py`
- Create: `senex/schema/checkpoint.schema.json`
- Create: `tests/unit/test_checkpoint.py`

**Conventions:** §4 (named exceptions), §8 (atomic writes), §10 (pydantic validates the model), §11, §13.

**Spec:** §8.5 (resume hash discipline) — every persisted finding/checkpoint carries the FULL hash bucket: `prompt_hash`, `config_hash`, `model_fingerprint`, `tool_pack_hash`, `lens_version`. Per §5.5.1 final paragraph and §8.5, the compaction prompt's hash is folded into the canonical `prompt_hash`; no separate `compaction_prompt_hash` field is persisted.

- [x] **Step 1.7.1: Write `senex/schema/checkpoint.schema.json`** (full hash bucket per §8.5):

  ```json
  {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "checkpoint.schema.json",
    "type": "object",
    "additionalProperties": false,
    "required": [
      "schema_version", "run_id", "current_phase", "phase_status",
      "phase_artifact_hashes", "completed_files",
      "config_hash", "prompt_hash", "model_fingerprint",
      "tool_pack_hash", "lens_version"
    ],
    "properties": {
      "schema_version": {"const": 1},
      "run_id": {"type": "string", "pattern": "^[0-9A-Z]{26}$"},
      "current_phase": {
        "enum": ["preflight", "discovery", "file_audit", "crosscut", "aggregate"]
      },
      "phase_status": {
        "type": "object",
        "additionalProperties": {
          "enum": ["pending", "in_progress", "complete", "errored"]
        }
      },
      "phase_artifact_hashes": {
        "type": "object",
        "additionalProperties": {"type": "string"}
      },
      "completed_files": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": ["path", "completed_at"],
          "properties": {
            "path": {"type": "string"},
            "completed_at": {"type": "string", "format": "date-time"}
          }
        }
      },
      "config_hash":             {"type": "string"},
      "prompt_hash":             {"type": "string"},
      "model_fingerprint":       {"type": "string"},
      "tool_pack_hash":          {
        "type": "string",
        "description": "Computed by senex.tools.registry.compute_tool_pack_hash() at preflight (see M5 Task 5.10). Consumed by Checkpoint.is_compatible() (M1) and embedded in findings.json (M7 Task 7.3) + RunStart event (M1 Task 1.3)."
      },
      "lens_version":            {"type": "string"}
    }
  }
  ```

- [x] **Step 1.7.2: Failing test file** at `tests/unit/test_checkpoint.py`:

  ```python
  """Tests for senex.checkpoint — atomic state machine + resume-hash compatibility."""
  from __future__ import annotations

  import json
  from pathlib import Path

  import pytest

  from senex.checkpoint import (
      Checkpoint,
      CheckpointCorrupt,
      CheckpointSchemaError,
  )

  HASHES = {
      "config_hash":            "sha256:cfg",
      "prompt_hash":            "sha256:prm",
      "model_fingerprint":      "sha256:mdl",
      "tool_pack_hash":         "sha256:tlp",
      "lens_version":           "1.0.0",
  }
  # Per spec §5.5.1 + §8.5: the compaction prompt hash is folded into prompt_hash;
  # there is no separate compaction_prompt_hash field on the checkpoint.


  def test_checkpoint_create_writes_all_required_fields(tmp_path: Path) -> None:
      cp = Checkpoint.create(
          audit_dir=tmp_path,
          run_id="01JZ3K7B9C8DQRS4M2EXAMPLE",
          **HASHES,
      )
      f = tmp_path / "checkpoint.json"
      assert f.exists()
      data = json.loads(f.read_text(encoding="utf-8"))
      assert data["schema_version"] == 1
      assert data["run_id"] == "01JZ3K7B9C8DQRS4M2EXAMPLE"
      assert data["current_phase"] == "preflight"
      assert data["phase_status"]["preflight"] == "pending"
      assert data["completed_files"] == []
      for k, v in HASHES.items():
          assert data[k] == v


  def test_checkpoint_mark_done_appends_completed_file(tmp_path: Path) -> None:
      Checkpoint.create(
          audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLE", **HASHES
      )
      Checkpoint.mark_done(tmp_path, "src/foo.py")
      data = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
      paths = [c["path"] for c in data["completed_files"]]
      assert paths == ["src/foo.py"]


  def test_checkpoint_set_phase_updates_status(tmp_path: Path) -> None:
      Checkpoint.create(
          audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLE", **HASHES
      )
      Checkpoint.set_phase(tmp_path, "crosscut", "in_progress")
      data = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
      assert data["current_phase"] == "crosscut"
      assert data["phase_status"]["crosscut"] == "in_progress"


  def test_checkpoint_atomic_write_unaffected_by_midwrite_crash(
      tmp_path: Path, monkeypatch
  ) -> None:
      Checkpoint.create(
          audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLE", **HASHES
      )
      original = (tmp_path / "checkpoint.json").read_text(encoding="utf-8")

      from senex import checkpoint as cp_mod

      def boom(*args, **kwargs):
          raise OSError("simulated disk full")

      monkeypatch.setattr(cp_mod.os, "replace", boom)
      with pytest.raises(OSError):
          Checkpoint.mark_done(tmp_path, "src/foo.py")
      # Original canonical file is intact.
      assert (tmp_path / "checkpoint.json").read_text(encoding="utf-8") == original


  def test_checkpoint_load_corrupt_raises_checkpointcorrupt(tmp_path: Path) -> None:
      (tmp_path / "checkpoint.json").write_text("{ broken json", encoding="utf-8")
      with pytest.raises(CheckpointCorrupt):
          Checkpoint.load(tmp_path)


  def test_checkpoint_is_compatible_returns_true_on_full_match(tmp_path: Path) -> None:
      Checkpoint.create(
          audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLE", **HASHES
      )
      cp = Checkpoint.load(tmp_path)
      assert cp.is_compatible(HASHES) is True


  def test_checkpoint_is_compatible_returns_false_on_any_mismatch(tmp_path: Path) -> None:
      Checkpoint.create(
          audit_dir=tmp_path, run_id="01JZ3K7B9C8DQRS4M2EXAMPLE", **HASHES
      )
      cp = Checkpoint.load(tmp_path)
      mismatched = {**HASHES, "prompt_hash": "sha256:DIFFERENT"}
      assert cp.is_compatible(mismatched) is False


  def test_checkpoint_validates_against_schema(tmp_path: Path) -> None:
      # Simulate a hand-edited file missing a required hash field.
      bad = {
          "schema_version": 1, "run_id": "01JZ3K7B9C8DQRS4M2EXAMPLE",
          "current_phase": "preflight", "phase_status": {}, "phase_artifact_hashes": {},
          "completed_files": [],
          # missing prompt_hash, etc.
      }
      (tmp_path / "checkpoint.json").write_text(json.dumps(bad), encoding="utf-8")
      with pytest.raises(CheckpointSchemaError):
          Checkpoint.load(tmp_path)


  def test_checkpoint_module_has_nonempty_docstring() -> None:
      from senex import checkpoint as cp_mod
      assert cp_mod.__doc__ and cp_mod.__doc__.strip() != ""
  ```

- [x] **Step 1.7.3: Implement `senex/checkpoint.py`:**

  ```python
  """senex.checkpoint — atomic state machine for resume.

  Implements spec §8.3 (resume) and §8.5 (reproducibility hash bucket).
  Every checkpoint carries the full hash field set required for resume-compat.
  Conventions §8: atomic write via tmp + fsync + rename.
  """
  from __future__ import annotations

  import json
  import os
  from dataclasses import dataclass
  from datetime import datetime, timezone
  from pathlib import Path
  from typing import Any, Literal

  import jsonschema

  PhaseName = Literal["preflight", "discovery", "file_audit", "crosscut", "aggregate"]
  PhaseStatus = Literal["pending", "in_progress", "complete", "errored"]
  ALL_PHASES: tuple[PhaseName, ...] = (
      "preflight", "discovery", "file_audit", "crosscut", "aggregate",
  )

  _SCHEMA_PATH = Path(__file__).parent / "schema" / "checkpoint.schema.json"


  class CheckpointCorrupt(RuntimeError):
      """Raised when checkpoint.json is structurally invalid (unparseable)."""


  class CheckpointSchemaError(ValueError):
      """Raised when checkpoint.json fails JSON Schema validation."""


  def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
      tmp = path.with_suffix(path.suffix + ".tmp")
      with tmp.open("w", encoding="utf-8", newline="\n") as f:
          f.write(json.dumps(data, indent=2, sort_keys=True))
          f.flush()
          os.fsync(f.fileno())
      os.replace(tmp, path)


  def _validator() -> jsonschema.Draft202012Validator:
      schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
      jsonschema.Draft202012Validator.check_schema(schema)
      return jsonschema.Draft202012Validator(schema)


  @dataclass(frozen=True)
  class Checkpoint:
      data: dict[str, Any]

      @classmethod
      def create(
          cls,
          audit_dir: Path,
          run_id: str,
          *,
          config_hash: str,
          prompt_hash: str,
          model_fingerprint: str,
          tool_pack_hash: str,
          lens_version: str,
      ) -> Checkpoint:
          # Per spec §5.5.1 + §8.5: compaction prompt hash is folded into
          # ``prompt_hash`` upstream (preflight prompt-snapshot stage).
          # No separate compaction_prompt_hash field is persisted here.
          data = {
              "schema_version": 1,
              "run_id": run_id,
              "current_phase": "preflight",
              "phase_status": {p: "pending" for p in ALL_PHASES},
              "phase_artifact_hashes": {},
              "completed_files": [],
              "config_hash": config_hash,
              "prompt_hash": prompt_hash,
              "model_fingerprint": model_fingerprint,
              "tool_pack_hash": tool_pack_hash,
              "lens_version": lens_version,
          }
          _validator().validate(data)
          audit_dir.mkdir(parents=True, exist_ok=True)
          _atomic_write_json(audit_dir / "checkpoint.json", data)
          return cls(data=data)

      @classmethod
      def load(cls, audit_dir: Path) -> Checkpoint:
          path = audit_dir / "checkpoint.json"
          try:
              data = json.loads(path.read_text(encoding="utf-8"))
          except json.JSONDecodeError as exc:
              raise CheckpointCorrupt(f"checkpoint.json is not valid JSON: {exc}") from exc
          try:
              _validator().validate(data)
          except jsonschema.ValidationError as exc:
              raise CheckpointSchemaError(str(exc)) from exc
          return cls(data=data)

      @classmethod
      def mark_done(cls, audit_dir: Path, file: str) -> None:
          path = audit_dir / "checkpoint.json"
          cp = cls.load(audit_dir)
          new_data = dict(cp.data)
          new_data["completed_files"] = list(cp.data["completed_files"]) + [
              {"path": file, "completed_at": datetime.now(tz=timezone.utc).isoformat()}
          ]
          _atomic_write_json(path, new_data)

      @classmethod
      def set_phase(cls, audit_dir: Path, name: PhaseName, status: PhaseStatus) -> None:
          path = audit_dir / "checkpoint.json"
          cp = cls.load(audit_dir)
          new_data = dict(cp.data)
          phase_status = dict(cp.data["phase_status"])
          phase_status[name] = status
          new_data["phase_status"] = phase_status
          new_data["current_phase"] = name
          _atomic_write_json(path, new_data)

      def is_compatible(self, other_hashes: dict[str, str]) -> bool:
          """Return True iff every hash in ``other_hashes`` matches this checkpoint.

          Note: a compaction-prompt change is detected through ``prompt_hash``
          (the compaction prompt is one of the preflight-snapshotted assets
          that compose ``prompt_hash``; see spec §5.5.1 + §8.5).
          """
          for k in (
              "config_hash", "prompt_hash", "model_fingerprint",
              "tool_pack_hash", "lens_version",
          ):
              if k in other_hashes and other_hashes[k] != self.data[k]:
                  return False
          return True
  ```

- [x] **Step 1.7.4: Run tests:**
  ```bash
  pytest tests/unit/test_checkpoint.py -v
  ```
  Expected: 9 PASSED.

- [x] **Step 1.7.5: Lint + type:**
  ```bash
  ruff check senex/ tests/
  mypy senex/
  ```

- [x] **Step 1.7.6: Commit:**
  ```bash
  git add senex/checkpoint.py senex/schema/checkpoint.schema.json tests/unit/test_checkpoint.py
  git commit -m "feat(M1): checkpoint state machine with phase tracking"
  ```

**Pitfalls (Task 1.7):**
- The `os.replace` MUST be the LAST operation. If you `fsync` after `replace`, a crash between rename and fsync leaves a renamed-but-not-durable file — file content is whatever the OS happened to flush. The implementation does `f.flush(); os.fsync(); os.replace()` in that exact order.
- `is_compatible` checks ALL FIVE bucket fields per §8.5: `config_hash`, `prompt_hash`, `model_fingerprint`, `tool_pack_hash`, `lens_version`. The compaction prompt's hash is folded into `prompt_hash` upstream (preflight prompt-snapshot stage); per §5.5.1 + §8.5 there is NO separate `compaction_prompt_hash` field on the checkpoint. Adding one re-introduces forensic-only state that drifts from `prompt_hash` and is a bug.
- `phase_status` is keyed by phase name, not phase index. Order is established via `ALL_PHASES`. Adding a phase later requires a schema_version bump (currently `const: 1`).
- The schema's `run_id` `pattern` `^[0-9A-Z]{26}$` matches Crockford-base32 ULID. If a test uses lowercase, it fails — the test fixture uses uppercase explicitly.
- `_atomic_write_json` uses `sort_keys=True` so two checkpoints with the same logical state hash to the same string — important for `phase_artifact_hashes` integrity checks in M8.

**Definition of done (Task 1.7):**
- `pytest tests/unit/test_checkpoint.py -v` shows 9 PASSED.
- `python -c "import json, jsonschema; jsonschema.Draft202012Validator.check_schema(json.load(open('senex/schema/checkpoint.schema.json')))"` exits 0.
- `ruff check senex/ tests/` and `mypy senex/` exit 0.
- `git diff --stat HEAD~1` lists exactly: `senex/checkpoint.py`, `senex/schema/checkpoint.schema.json`, `tests/unit/test_checkpoint.py`.

---

### Task 1.8: Schema files (5 JSON schemas)

**Files:**
- Create: `senex/schema/audit_response.schema.json` (verbatim spec §5.4)
- Create: `senex/schema/crosscut_response.schema.json` (verbatim spec §5.4.1)
- Create: `senex/schema/compaction_response.schema.json` (verbatim spec §5.5.1)
- Create: `senex/schema/findings_index.schema.json` (per spec §7.3)
- Create: `senex/schema/events.schema.json` (oneOf union over the 36 event types)
- Create: `tests/unit/test_schemas.py`

**Conventions:** §1 (one responsibility per file), §11, §13.

- [x] **Step 1.8.1: Copy `audit_response.schema.json` verbatim from spec §5.4**. Important fields:
  - `schema_version: const 1`
  - `additionalProperties: false` on every object — including `findings.items`, `findings.items.properties.location`, `recommendations.items`, and `best_practices_table.items` (writer-side strict validation per convention §SCHEMA-2; verified by M7 Task 7.1 renderer test that rejects extra fields).
  - `findings.items.properties.location` uses `oneOf: [{required: ["symbol"]}, {required: ["line_start"]}]`
  - Add a `$schema` line: `"$schema": "https://json-schema.org/draft/2020-12/schema"` and `"$id": "audit_response.schema.json"`.

- [x] **Step 1.8.2: Copy `crosscut_response.schema.json` verbatim from spec §5.4.1** (lines 600–624). Add `$schema` and `$id` headers as above. Theme `id` pattern is `^t-[a-f0-9]{12}$`.

- [x] **Step 1.8.3: Copy `compaction_response.schema.json` verbatim from spec §5.5.1** (lines 724–733). Add headers + `additionalProperties: false`.

- [x] **Step 1.8.4: Write `findings_index.schema.json`** based on spec §7.3 (lines 1373–1421):

  ```json
  {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "findings_index.schema.json",
    "type": "object",
    "additionalProperties": false,
    "required": ["schema_version", "run", "totals", "themes", "findings"],
    "properties": {
      "schema_version": {"const": 1},
      "run": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "repo", "run_id", "audit_dir", "model", "model_fingerprint",
          "lens", "lens_version", "started_at", "duration_seconds",
          "config_hash", "prompt_hash"
        ],
        "properties": {
          "repo": {"type": "string"},
          "run_id": {"type": "string"},
          "audit_dir": {"type": "string"},
          "model": {"type": "string"},
          "model_fingerprint": {"type": "string"},
          "lens": {"type": "string"},
          "lens_version": {"type": "string"},
          "started_at": {"type": "string", "format": "date-time"},
          "duration_seconds": {"type": "number"},
          "config_hash": {"type": "string"},
          "prompt_hash": {"type": "string"}
        }
      },
      "totals": {
        "type": "object",
        "additionalProperties": false,
        "required": ["high", "medium", "low", "healthy", "files"],
        "properties": {
          "high":    {"type": "integer"},
          "medium":  {"type": "integer"},
          "low":     {"type": "integer"},
          "healthy": {"type": "integer"},
          "files":   {"type": "integer"}
        }
      },
      "themes": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "id", "title", "description", "affected_files",
            "priority", "confidence", "recommended_action"
          ],
          "properties": {
            "id":                  {"type": "string", "pattern": "^t-[a-f0-9]{12}$"},
            "title":               {"type": "string", "maxLength": 120},
            "description":         {"type": "string"},
            "affected_files":      {"type": "array", "items": {"type": "string"}},
            "priority":            {"enum": ["high", "medium", "low"]},
            "confidence":          {"enum": ["high", "medium", "low"]},
            "recommended_action":  {"type": "string"}
          }
        }
      },
      "findings": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "id", "file", "category", "priority", "title",
            "issue", "why", "fix", "confidence", "location",
            "report_path", "suppressed",
            "prompt_hash", "config_hash", "model_fingerprint", "lens_version"
          ],
          "properties": {
            "id":                {"type": "string", "pattern": "^f-[a-f0-9]{12}$"},
            "file":              {"type": "string"},
            "category":          {"type": "string"},
            "priority":          {"enum": ["high", "medium", "low", "healthy"]},
            "title":             {"type": "string", "maxLength": 120},
            "issue":             {"type": "string"},
            "why":               {"type": "string"},
            "fix":               {"type": "string"},
            "confidence":        {"enum": ["high", "medium", "low"]},
            "location": {
              "type": "object",
              "properties": {
                "line_start": {"type": "integer"},
                "line_end":   {"type": "integer"},
                "symbol":     {"type": "string"}
              },
              "oneOf": [
                {"required": ["symbol"]},
                {"required": ["line_start"]}
              ]
            },
            "report_path":       {"type": "string"},
            "suppressed":        {"type": "boolean"},
            "prompt_hash":       {"type": "string"},
            "config_hash":       {"type": "string"},
            "model_fingerprint": {"type": "string"},
            "lens_version":      {"type": "string"}
          }
        }
      }
    }
  }
  ```

- [x] **Step 1.8.5: Write `events.schema.json`** as a `oneOf` discriminated union over the 36 event types (one entry per `senex.events.ALL_EVENT_TYPES` member). Skeleton — list all 36 entries:

  ```json
  {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "events.schema.json",
    "title": "senex events.jsonl line schema",
    "oneOf": [
      {"$ref": "#/$defs/RunStart"},
      {"$ref": "#/$defs/DiscoveryComplete"},
      {"$ref": "#/$defs/SymlinkSkipped"}
      /* ... 33 more ... */
    ],
    "$defs": {
      "BaseFields": {
        "type": "object",
        "required": ["v", "type", "ts", "seq", "run_id"],
        "properties": {
          "v":      {"const": 1},
          "type":   {"type": "string"},
          "ts":     {"type": "string", "format": "date-time"},
          "seq":    {"type": "integer", "minimum": 0},
          "run_id": {"type": "string"}
        }
      },
      "RunStart": {
        "allOf": [
          {"$ref": "#/$defs/BaseFields"},
          {
            "type": "object",
            "additionalProperties": false,
            "required": ["v", "type", "ts", "seq", "run_id",
                         "repo", "audit_dir", "model", "lens", "lens_version",
                         "config_hash", "prompt_hash", "model_fingerprint", "started_at"],
            "properties": {
              "v":      {"const": 1},
              "type":   {"const": "RunStart"},
              "ts":     {"type": "string", "format": "date-time"},
              "seq":    {"type": "integer"},
              "run_id": {"type": "string"},
              "repo": {"type": "string"},
              "audit_dir": {"type": "string"},
              "model": {"type": "string"},
              "lens": {"type": "string"},
              "lens_version": {"type": "string"},
              "config_hash": {"type": "string"},
              "prompt_hash": {"type": "string"},
              "model_fingerprint": {"type": "string"},
              "started_at": {"type": "string", "format": "date-time"}
            }
          }
        ]
      }
      /* ... 35 more $defs entries; one per event type. */
    }
  }
  ```

  > Implementation note: The full 36-entry expansion is mechanical — copy the `RunStart` template and substitute the field list from `senex/events.py`. The test in 1.8.6 catches missing entries.

- [x] **Step 1.8.6: Failing test file** at `tests/unit/test_schemas.py`:

  ```python
  """Tests for senex.schema/*.json — validity + audit_response location.oneOf + events union."""
  from __future__ import annotations

  import json
  from pathlib import Path

  import jsonschema
  import pytest

  SCHEMA_DIR = Path("senex/schema")
  ALL_SCHEMAS = [
      "audit_response.schema.json",
      "crosscut_response.schema.json",
      "compaction_response.schema.json",
      "findings_index.schema.json",
      "events.schema.json",
      "checkpoint.schema.json",
  ]


  @pytest.mark.parametrize("name", ALL_SCHEMAS)
  def test_schema_is_structurally_valid_draft_2020_12(name: str) -> None:
      schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
      jsonschema.Draft202012Validator.check_schema(schema)


  def test_audit_response_rejects_finding_without_location() -> None:
      schema = json.loads((SCHEMA_DIR / "audit_response.schema.json").read_text(encoding="utf-8"))
      v = jsonschema.Draft202012Validator(schema)
      bad = {
          "schema_version": 1,
          "overall_assessment": "x" * 60,
          "findings": [{
              "category": "Correctness", "priority": "high", "title": "x",
              "issue": "i", "why": "w", "fix": "f", "confidence": "high"
              # no location.
          }],
          "recommendations": [],
      }
      assert not v.is_valid(bad)


  def test_audit_response_rejects_finding_with_empty_location_oneOf() -> None:
      schema = json.loads((SCHEMA_DIR / "audit_response.schema.json").read_text(encoding="utf-8"))
      v = jsonschema.Draft202012Validator(schema)
      bad = {
          "schema_version": 1,
          "overall_assessment": "x" * 60,
          "findings": [{
              "category": "Correctness", "priority": "high", "title": "x",
              "issue": "i", "why": "w", "fix": "f", "confidence": "high",
              "location": {"line_end": 10}  # neither symbol nor line_start.
          }],
          "recommendations": [],
      }
      assert not v.is_valid(bad)


  def test_audit_response_accepts_finding_with_symbol_only() -> None:
      schema = json.loads((SCHEMA_DIR / "audit_response.schema.json").read_text(encoding="utf-8"))
      v = jsonschema.Draft202012Validator(schema)
      ok = {
          "schema_version": 1,
          "overall_assessment": "x" * 60,
          "findings": [{
              "category": "Correctness", "priority": "high", "title": "x",
              "issue": "i", "why": "w", "fix": "f", "confidence": "high",
              "location": {"symbol": "validate_user"}
          }],
          "recommendations": [],
      }
      assert v.is_valid(ok)


  def test_findings_index_rejects_unknown_priority() -> None:
      schema = json.loads(
          (SCHEMA_DIR / "findings_index.schema.json").read_text(encoding="utf-8")
      )
      v = jsonschema.Draft202012Validator(schema)
      bad_minimal = {
          "schema_version": 1,
          "run": {
              "repo": "r", "run_id": "x", "audit_dir": "/x", "model": "m",
              "model_fingerprint": "f", "lens": "correctness", "lens_version": "1.0.0",
              "started_at": "2026-04-26T00:00:00Z", "duration_seconds": 1,
              "config_hash": "c", "prompt_hash": "p",
          },
          "totals": {"high": 0, "medium": 0, "low": 0, "healthy": 0, "files": 0},
          "themes": [],
          "findings": [{
              "id": "f-aaaaaaaaaaaa", "file": "x", "category": "Correctness",
              "priority": "INVALID", "title": "x", "issue": "i", "why": "w",
              "fix": "f", "confidence": "high",
              "location": {"symbol": "x"}, "report_path": "x.md", "suppressed": False,
              "prompt_hash": "p", "config_hash": "c", "model_fingerprint": "m",
              "lens_version": "1.0.0",
          }],
      }
      assert not v.is_valid(bad_minimal)


  def test_events_schema_validates_runstart_event() -> None:
      schema = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
      v = jsonschema.Draft202012Validator(schema)
      ev = {
          "v": 1, "type": "RunStart", "ts": "2026-04-26T00:00:00Z",
          "seq": 0, "run_id": "01JZ3K7B9C8DQRS4M2EXAMPLE",
          "repo": "r", "audit_dir": "/x", "model": "m", "lens": "correctness",
          "lens_version": "1.0.0",
          "config_hash": "c", "prompt_hash": "p", "model_fingerprint": "f",
          "started_at": "2026-04-26T00:00:00Z",
      }
      assert v.is_valid(ev), list(v.iter_errors(ev))


  def test_events_schema_rejects_unknown_event_type() -> None:
      schema = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
      v = jsonschema.Draft202012Validator(schema)
      bad = {"v": 1, "type": "NotARealEvent", "ts": "2026-04-26T00:00:00Z",
             "seq": 0, "run_id": "x"}
      assert not v.is_valid(bad)


  def test_events_schema_has_one_def_per_event_class() -> None:
      from senex.events import ALL_EVENT_TYPES
      schema = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
      defs = set(schema.get("$defs", {}).keys()) - {"BaseFields"}
      class_names = {c.__name__ for c in ALL_EVENT_TYPES}
      assert class_names.issubset(defs), f"missing $defs entries: {class_names - defs}"
  ```

- [x] **Step 1.8.7: Run tests:**
  ```bash
  pytest tests/unit/test_schemas.py -v
  ```
  Expected: every parametrized test PASSED + the 7 standalone tests.

- [x] **Step 1.8.8: Lint:**
  ```bash
  ruff check tests/
  ```
  (No `senex/` python changed in this task; mypy stays clean by induction.)

- [x] **Step 1.8.9: Commit:**
  ```bash
  git add senex/schema/*.json tests/unit/test_schemas.py
  git commit -m "feat(M1): JSON schemas for all artifacts"
  ```

**Pitfalls (Task 1.8):**
- `additionalProperties: false` MUST appear on every nested object — not just the root. Without it, the model can emit phantom fields and validation passes silently. The findings_index test `test_findings_index_rejects_unknown_priority` catches the priority enum, but only `additionalProperties: false` catches phantom keys.
- `location.oneOf` per §SCHEMA-5 means EITHER `symbol` OR `line_start` is required, not both, not neither, but exactly one of the two requirement clauses must satisfy. The test `test_audit_response_rejects_finding_with_empty_location_oneOf` catches the "neither" case.
- The events schema `oneOf` must include all 36 event-type `$defs`. The test `test_events_schema_has_one_def_per_event_class` catches a missing entry — fail loud the moment someone adds a new event in `senex/events.py` and forgets to extend the JSON schema.
- `format: "date-time"` is RFC 3339 / ISO 8601; jsonschema accepts both `Z` and `+00:00` suffixes. Tests use `Z`.
- The `compaction_response.schema.json` has `evidence_summary.maxLength = 16384` (chars, not tokens). The spec §5.5.1 says the post-validation cap is 4096 *tokens* — that's enforced in M6, not in the schema. The schema's char cap is a safety bound only.

**Definition of done (Task 1.8):**
- All 6 schemas pass `Draft202012Validator.check_schema()`.
- Audit-response negative tests reject location-less + invalid-oneOf findings.
- Events schema has a `$defs` entry for every class in `senex.events.ALL_EVENT_TYPES`.
- `pytest tests/unit/test_schemas.py -v` shows all PASSED.
- `ruff check tests/` exits 0.
- `git diff --stat HEAD~1` lists exactly the 5 new schema files + the test file (note: `checkpoint.schema.json` was committed in Task 1.7).

---

## Acceptance criteria (milestone-level)

- `python -m pip install -e .` succeeds; `python -c "import senex; print(senex.__version__)"` prints `0.1.0`.
- `pytest tests/unit/ -v` is 100% green (8 task-level test files; ≥ ~80 tests total once parametrized cases expand).
- `ruff check senex/ tests/` exits 0.
- `mypy senex/` exits 0 under `strict = true`.
- `python -c "from senex.config import load_config; load_config('senex.config.toml.example')"` succeeds with no errors.
- `python -c "from senex.lens import Lens; l = Lens.load('correctness'); print(l.tools)"` prints exactly the 6-tool list `['gitnexus_query', 'gitnexus_context', 'gitnexus_impact', 'read_file', 'grep', 'search_code']`.
- All five lens-facing JSON schemas plus `checkpoint.schema.json` pass `Draft202012Validator.check_schema()` with no errors.
- Round-trip serialization works for every event subclass (instantiate → `model_dump_json()` → `model_validate_json()` → equal).
- Two concurrent threads acquiring `RunLock` on the same fingerprint produce a single lockfile with 2 holders; releasing both deletes the file.
- `git log --oneline` shows exactly 8 commits, one per task, all matching `feat(M1): ...`.
- Push the milestone branch (conventions §11): `git push origin <branch>`.
