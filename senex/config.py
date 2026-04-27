"""senex.config — TOML config loader + strict pydantic validation + deep-merge.

Implements spec §6 (configuration) and §6.1 (resolution semantics).
Per conventions §10, every model has ``extra="forbid"``.
"""
from __future__ import annotations

import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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
    min_priority: Literal["all", "low", "medium", "high"] = "all"
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
    allow_mixed: bool = False  # resume: tolerate fingerprint mismatch (spec §5.5.2.7)
    load_timeout_seconds: int = Field(default=120, gt=0)
    # Manual-load wait loop (M11). When ``auto_load`` is enabled and the
    # backend's ``load()`` call fails (e.g. LM Studio's resource guardrail
    # rejects the autoload), or when ``auto_load=False`` and the model is
    # not yet loaded, ``Lifecycle.acquire`` falls back to polling
    # ``backend.is_loaded`` until the user manually loads the model
    # (LM Studio GUI or ``lms load <model_id>``) or this timeout expires.
    # Set to 0 to disable the wait loop and preserve v1.0.0-rc1 behavior
    # (immediate failure on auto_load error).
    load_wait_timeout_seconds: int = Field(default=600, ge=0)
    load_wait_poll_interval_seconds: float = Field(default=5.0, gt=0)
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
    # Token budget enforcement (M3 §3.4): pre-call check fails the request when
    # count_tokens(messages) > token_budget_pct * context_window. M4 sets these
    # per-model after probing /v1/models. The defaults are conservative starting
    # points for a 32K-context model; real values are populated by the lifecycle
    # layer at runlock acquisition.
    context_window: int = Field(default=32768, gt=0)
    token_budget_pct: float = Field(default=0.9, gt=0.0, le=1.0)
    # Fingerprint cache TTL (M3 §3.7). Re-probing /v1/models on every chat() is
    # wasteful under tool-loop iteration; cache the observed fingerprint for
    # this many seconds before re-verifying.
    fingerprint_recheck_interval_s: float = Field(default=60.0, gt=0.0)
    sampling: SamplingCfg = Field(default_factory=SamplingCfg)
    thinking: ThinkingCfg = Field(default_factory=ThinkingCfg)
    tasks: TasksCfg = Field(default_factory=TasksCfg)
    tools: ToolsCfg = Field(default_factory=ToolsCfg)
    compaction: CompactionCfg = Field(default_factory=CompactionCfg)
    lifecycle: LifecycleCfg = Field(default_factory=LifecycleCfg)


class UICfg(_StrictModel):
    """[ui] — Launcher / TUI surface settings (M11).

    Currently used by the "Scan disk for repos" Launcher button. ``scan_root``
    is resolved to ``Path.home()`` at runtime when ``None`` so users can
    override the scan root from the config without hard-coding a path that
    might not exist on every machine.
    """

    scan_root: str | None = None
    scan_max_depth: int = Field(default=6, gt=0)


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
    ui: UICfg = Field(default_factory=UICfg)
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


def load_config(path: Path) -> SenexConfig:
    """Parse TOML at ``path`` into a validated SenexConfig.

    Raises:
        UnknownConfigKey: TOML contained a key not declared on the schema.
        ValueError: A value was out of range / invalid type (pydantic ValidationError).
    """
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return SenexConfig.model_validate(data)
    except ValidationError as exc:
        # Re-raise as UnknownConfigKey when the cause is extra_forbidden.
        for err in exc.errors():
            if err["type"] == "extra_forbidden":
                bad_key = err["loc"][-1]
                parent_loc = err["loc"][:-1]
                parent_model: type[BaseModel] = SenexConfig
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
    """Apply spec §6.1 layering: defaults -> file -> per-repo -> CLI -> TUI.

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
