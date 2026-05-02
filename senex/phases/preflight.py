"""senex.phases.preflight — PreflightPhase + spec §8.1 individual checks.

Each spec §8.1 row gets ITS OWN small function so it can be unit-tested in
isolation. ``PreflightPhase.do_work`` is a thin runner that invokes them in
order, shorts on the first FAIL (raising ``PreflightFailure`` with the
documented exit code), and accumulates WARNings into one ``PreflightWarning``
event each.

Exit codes (spec §8.1):
    0 — success
    1 — partial success
    2 — config / setup error (config parse, repo path, output dir, addendum
        safety, sampling ranges, runlock dir, language anchors error,
        compaction prompt missing)
    3 — external dependency error (LM Studio unreachable, non-loopback, model
        not loadable, lifecycle backend missing, schema negotiation hard fail,
        npx missing)
    130 — interrupted (SIGINT)

R11 collapse: every PreflightFailure exit_code is in {2, 3}.

SEC-1: ``check_addendum_safety`` resolves the addendum path, refuses
symlinks, refuses traversal / out-of-repo, caps at 64 KiB.
"""
from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from senex.config import SenexConfig, load_config
from senex.events import EventBus, PreflightWarning
from senex.inference_errors import LMSConnectionLost

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from senex.events import CommandBus
    from senex.lens import Lens
    from senex.inference_client import InferenceClient

from .base import Phase, PreflightFailure  # noqa: E402

log = logging.getLogger(__name__)

# 64 KiB cap on system-prompt addendum (SEC-1, spec §8.1).
_ADDENDUM_MAX_BYTES: int = 64 * 1024

# Map suffixes to the language anchor filenames (mirrors EXT_TO_ANCHOR
# in senex.prompts._anchor_loader).
_EXT_TO_ANCHOR_FILE: dict[str, str] = {
    ".py": "lang_python.md",
    ".ts": "lang_typescript.md",
    ".tsx": "lang_typescript.md",
    ".rs": "lang_rust.md",
    ".go": "lang_go.md",
    ".cs": "lang_csharp.md",
}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class CheckStatus(Enum):
    """Outcome of a single preflight check."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    """One row in the preflight table. ``exit_code`` is required when ``status == FAIL``."""

    status: CheckStatus
    message: str = ""
    exit_code: int | None = None


def _ok(message: str = "") -> CheckResult:
    return CheckResult(status=CheckStatus.PASS, message=message)


def _warn(message: str) -> CheckResult:
    return CheckResult(status=CheckStatus.WARN, message=message)


def _fail(message: str, exit_code: int) -> CheckResult:
    return CheckResult(status=CheckStatus.FAIL, message=message, exit_code=exit_code)


# ---------------------------------------------------------------------------
# Sync checks (no network I/O)
# ---------------------------------------------------------------------------


def check_config_parses(path: Path) -> CheckResult:
    """senex.config.toml parses, no unknown keys, required fields present."""
    if not path.exists():
        return _fail(f"config file not found: {path}", exit_code=2)
    try:
        load_config(path)
    except Exception as exc:  # noqa: BLE001 — pydantic / tomllib raise multiple types
        return _fail(f"config parse failed: {exc}", exit_code=2)
    return _ok()


def check_repo_path(path: Path) -> CheckResult:
    """Repo path exists, is absolute, is a directory, has ``.git/``."""
    if not path.is_absolute():
        return _fail(f"repo path must be absolute: {path}", exit_code=2)
    if not path.exists():
        return _fail(f"repo path does not exist: {path}", exit_code=2)
    if not path.is_dir():
        return _fail(f"repo path is not a directory: {path}", exit_code=2)
    if not (path / ".git").exists():
        return _fail(f"repo path has no .git/ subdir: {path}", exit_code=2)
    return _ok()


def check_output_dir_writable(path: Path, min_bytes: int) -> CheckResult:
    """Output directory is writable and has ``min_bytes`` free space."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail(f"output dir not writable: {path} ({exc})", exit_code=2)
    # Free-space check: best-effort via shutil.disk_usage.
    try:
        usage = shutil.disk_usage(str(path))
    except OSError as exc:
        return _fail(f"could not stat output dir: {exc}", exit_code=2)
    if usage.free < min_bytes:
        return _fail(
            f"output dir has only {usage.free} bytes free; need {min_bytes}",
            exit_code=2,
        )
    return _ok()


def check_gitnexus_index(repo: Path) -> CheckResult:
    """``.gitnexus/`` directory present (warn-only when absent)."""
    gn = repo / ".gitnexus"
    if not gn.exists():
        return _warn("repo has no .gitnexus/ directory; graph context unavailable")
    meta = gn / "meta.json"
    if not meta.exists():
        return _warn(".gitnexus/meta.json missing; index may be incomplete")
    return _ok()


def check_addendum_safety(
    addendum_path: Path | None, repo_root: Path
) -> CheckResult:
    """SEC-1: resolve addendum, refuse symlinks / traversal / out-of-repo / oversized.

    Returns PASS when ``addendum_path`` is None (no addendum configured) or
    when all SEC-1 invariants hold. Returns FAIL exit_code=2 on any violation.
    """
    if addendum_path is None:
        return _ok("no addendum configured")
    repo_resolved = repo_root.resolve(strict=False)
    # Symlink rejection BEFORE resolve (resolve follows the symlink).
    if addendum_path.is_symlink():
        return _fail(
            f"addendum path is a symlink (refused per SEC-1): {addendum_path}",
            exit_code=2,
        )
    try:
        resolved = addendum_path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        return _fail(f"could not resolve addendum path: {exc}", exit_code=2)
    try:
        resolved.relative_to(repo_resolved)
    except ValueError:
        return _fail(
            f"addendum resolves outside repo root: {resolved} not under {repo_resolved}",
            exit_code=2,
        )
    if not resolved.exists():
        return _fail(f"addendum file does not exist: {resolved}", exit_code=2)
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return _fail(f"could not stat addendum: {exc}", exit_code=2)
    if size > _ADDENDUM_MAX_BYTES:
        return _fail(
            f"addendum exceeds {_ADDENDUM_MAX_BYTES} bytes: {size}",
            exit_code=2,
        )
    return _ok()


def check_language_anchors(
    extensions: list[str], anchors_dir: Path
) -> CheckResult:
    """Required language anchor files exist (warn-only per spec §8.1)."""
    missing: list[str] = []
    for ext in extensions:
        anchor_file = _EXT_TO_ANCHOR_FILE.get(ext.lower())
        if anchor_file is None:
            continue  # extensions without a known anchor are fine
        if not (anchors_dir / anchor_file).exists():
            missing.append(anchor_file)
    if missing:
        return _warn(f"missing language anchors: {missing}")
    return _ok()


def check_sampling_ranges(config: SenexConfig) -> CheckResult:
    """Sampling values within valid ranges (spec §8.1).

    The ranges are also enforced by pydantic at config-load time; this is a
    defense-in-depth check covering programmatic SenexConfig construction
    that bypassed validation.
    """
    s = config.inference.sampling
    bad: list[str] = []
    if not 0.0 <= s.temperature <= 2.0:
        bad.append(f"temperature={s.temperature}")
    if not 0.0 <= s.top_p <= 1.0:
        bad.append(f"top_p={s.top_p}")
    if not 0 <= s.top_k <= 200:
        bad.append(f"top_k={s.top_k}")
    if not 0.0 <= s.min_p <= 1.0:
        bad.append(f"min_p={s.min_p}")
    if not 0.0 <= s.repeat_penalty <= 2.0:
        bad.append(f"repeat_penalty={s.repeat_penalty}")
    if s.max_tokens <= 0:
        bad.append(f"max_tokens={s.max_tokens}")
    t = config.inference.thinking
    if t.max_thinking_tokens <= 0:
        bad.append(f"max_thinking_tokens={t.max_thinking_tokens}")
    if bad:
        return _fail(f"sampling values out of range: {bad}", exit_code=2)
    return _ok()


def check_lifecycle_backend(config: SenexConfig) -> CheckResult:
    """Lifecycle backend (HTTP, lmstudio SDK, or ``lms`` CLI) available when needed."""
    auto_load = bool(config.inference.lifecycle.auto_load)
    auto_unload = bool(config.inference.lifecycle.auto_unload)
    needs = auto_load or auto_unload

    # Ollama backend: probe the Ollama server directly, skip SGLang/SDK/CLI.
    if config.inference.backend == "ollama":
        ollama_url = config.inference.ollama.base_url
        try:
            import httpx as _httpx
            with _httpx.Client(timeout=3.0) as _cx:
                _r = _cx.get(ollama_url.rstrip("/") + "/")
                ollama_ok = _r.status_code == 200
        except Exception:
            ollama_ok = False
        if ollama_ok:
            return _ok(f"backend: ollama reachable at {ollama_url}")
        if not needs:
            return _warn(f"ollama unreachable at {ollama_url}; auto_load/auto_unload disabled")
        if config.inference.ollama.manage_process:
            return _ok(f"backend: ollama not running; manage_process=true will start it")
        return _fail(
            f"ollama unreachable at {ollama_url}; "
            "set [inference.ollama].manage_process=true to auto-start",
            exit_code=3,
        )

    # HTTP backend (SGLang / vLLM / OpenAI-compat servers): probe /v1/models.
    http_available = False
    base_url = config.inference.base_url
    if base_url:
        try:
            import httpx

            with httpx.Client(timeout=3.0) as cx:
                resp = cx.get(f"{base_url.rstrip('/')}/models")
                http_available = resp.status_code == 200
        except Exception:
            http_available = False

    sdk_available = False
    if "lmstudio" in sys.modules and sys.modules["lmstudio"] is not None:
        sdk_available = True
    else:
        try:
            __import__("lmstudio")
            sdk_available = True
        except ImportError:
            sdk_available = False
    cli_available = shutil.which("lms") is not None

    if http_available or sdk_available or cli_available:
        return _ok(
            f"backend: http={http_available} sdk={sdk_available} cli={cli_available}"
        )
    if not needs:
        return _warn(
            "no lifecycle backend available; auto_load/auto_unload disabled"
        )
    return _fail(
        "no lifecycle backend (HTTP, lmstudio SDK, lms CLI); "
        "verify SGLang/LM Studio is running or set lifecycle.auto_load=false",
        exit_code=3,
    )


def check_runlock_dir_writable(config: SenexConfig) -> CheckResult:
    """Runlock dir is writable; existing locks parseable.

    Default location: ``~/.senex/locks``. The subprocess that probes
    parseability is intentionally not run here — RunLock auto-recovers
    corrupt files at acquire time.
    """
    raw = config.inference.lifecycle.runlock_dir
    if raw:
        target = Path(raw)
    else:
        target = Path.home() / ".senex" / "locks"
    try:
        target.mkdir(parents=True, exist_ok=True)
        # Touch a probe file so we know writes work.
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(target), delete=True
        ) as probe:
            probe.write("ok")
            probe.flush()
    except OSError as exc:
        return _fail(f"runlock dir not writable: {target} ({exc})", exit_code=2)
    return _ok()


# ---------------------------------------------------------------------------
# Async checks (network I/O)
# ---------------------------------------------------------------------------


def _is_loopback(base_url: str) -> bool:
    """Return True iff ``base_url``'s host is a loopback alias (spec §8.1)."""
    # Cheap parse: avoid importing urllib for one host check.
    no_scheme = base_url.split("://", 1)[-1]
    host_port = no_scheme.split("/", 1)[0]
    host = host_port.rsplit(":", 1)[0] if ":" in host_port else host_port
    # Strip brackets for IPv6.
    host = host.strip("[]").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


async def check_lms_reachable(client: "InferenceClient") -> CheckResult:
    """Inference server (SGLang/LM Studio) reachable at ``/v1/models`` and bound to loopback."""
    cfg = client._config
    if not _is_loopback(cfg.base_url) and not cfg.allow_non_loopback:
        return _fail(
            f"non-loopback base_url refused (security): {cfg.base_url}; "
            f"set [lmstudio].allow_non_loopback = true to override",
            exit_code=3,
        )
    try:
        await client.list_loaded_models()
    except LMSConnectionLost as exc:
        return _fail(
            f"inference server unreachable at {cfg.base_url}: {exc}", exit_code=3
        )
    except Exception as exc:  # noqa: BLE001 — surface any backend misbehavior
        return _fail(f"inference-server probe failed: {exc}", exit_code=3)
    return _ok()


async def check_model_loaded_or_loadable(
    client: "InferenceClient", config: SenexConfig
) -> CheckResult:
    """Target model already loaded, or auto_load can load it (spec §8.1)."""
    target = config.inference.model
    auto_load = bool(config.inference.lifecycle.auto_load)
    try:
        loaded = await client.list_loaded_models()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not list loaded models: {exc}", exit_code=3)
    is_loaded = any(m.id == target for m in loaded)
    if is_loaded:
        return _ok()
    if auto_load:
        # auto_load handles the actual load at lifecycle.acquire time. Here
        # we only guard against the not-loaded + auto_load=False combination
        # which is documented FAIL; backend availability is checked separately
        # in ``check_lifecycle_backend``.
        return _ok("model not loaded; auto_load=true will load at start")
    return _fail(
        f"model {target!r} not loaded; set auto_load=true to auto-load",
        exit_code=3,
    )


async def check_schema_with_thinking(
    client: "InferenceClient", config: SenexConfig
) -> CheckResult:
    """``response_format=json_schema`` works WITH thinking — warn on fallback."""
    try:
        caps = await client.probe_capabilities(config.inference.model)
    except Exception as exc:  # noqa: BLE001
        return _warn(f"capability probe failed; assuming json_object fallback: {exc}")
    if not caps.supports_schema_with_tools:
        return _warn(
            "model does not support response_format=json_schema with tools; "
            "falling back to json_object"
        )
    return _ok()


async def check_ollama_reachable(base_url: str) -> CheckResult:
    """Ollama server responds at ``/`` (GET)."""
    import httpx

    url = base_url.rstrip("/") + "/"
    try:
        async with httpx.AsyncClient(timeout=5.0) as cx:
            resp = await cx.get(url)
        if resp.status_code == 200:
            return _ok(f"ollama reachable at {base_url}")
        return _fail(
            f"ollama returned HTTP {resp.status_code} at {url}", exit_code=3
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"ollama unreachable at {base_url}: {exc}", exit_code=3)


async def check_ollama_model_available(base_url: str, model: str) -> CheckResult:
    """Ollama ``/api/show`` knows about the configured model."""
    import httpx

    url = base_url.rstrip("/") + "/api/show"
    try:
        async with httpx.AsyncClient(timeout=5.0) as cx:
            resp = await cx.post(url, json={"name": model})
        if resp.status_code == 200:
            return _ok(f"model {model!r} available")
        return _fail(
            f"ollama model {model!r} not found (HTTP {resp.status_code})", exit_code=3
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"ollama model probe failed: {exc}", exit_code=3)


async def check_streaming(
    client: "InferenceClient", config: SenexConfig
) -> CheckResult:
    """Streaming works on the configured model (warn-only on failure)."""
    try:
        caps = await client.probe_capabilities(config.inference.model)
    except Exception as exc:  # noqa: BLE001
        return _warn(f"streaming probe failed: {exc}")
    if not caps.supports_streaming:
        return _warn("model does not support streaming; TUI experience degraded")
    return _ok()


# ---------------------------------------------------------------------------
# PreflightPhase
# ---------------------------------------------------------------------------


@dataclass
class PreflightInputs:
    """Bundle of paths the auditor wires to the preflight checks.

    The auditor is responsible for resolving these (e.g. ``config_path`` is
    the TOML file passed on the command line; ``addendum_path`` is the
    optional system-prompt addendum from per-repo config).
    """

    config_path: Path
    repo_path: Path
    output_dir: Path
    min_disk_bytes: int
    addendum_path: Path | None
    anchors_dir: Path


class PreflightPhase:
    """Runs every spec §8.1 check; raises ``PreflightFailure`` on first hard FAIL.

    Sync checks run first (cheap, deterministic). Async (network) checks
    run after to avoid wasting an HTTP call when local config is busted.

    All WARN results are collected and emitted as one ``PreflightWarning``
    event per warning. The phase's ``do_work`` returns the list of warnings
    so doctor / metrics can persist them.
    """

    name = "preflight"

    def __init__(
        self,
        client: "InferenceClient",
        inputs: PreflightInputs,
        run_id: str,
    ) -> None:
        self._client = client
        self._inputs = inputs
        self._run_id = run_id

    async def read_state(self, audit_dir: Path) -> Any:
        # Preflight is idempotent; no input state.
        return None

    async def write_state(self, audit_dir: Path, state: Any) -> None:
        # Optional: persist warnings to <audit_dir>/preflight.json for doctor reuse.
        if state is None:
            return
        warnings = state.get("warnings", []) if isinstance(state, dict) else []
        if not warnings:
            return
        import json

        from senex.atomic_io import write_text_atomic

        target = audit_dir / "preflight.json"
        body = json.dumps({"warnings": warnings}, indent=2) + "\n"
        write_text_atomic(target, body)

    async def do_work(
        self,
        state: Any,
        lens: "Lens",
        config: SenexConfig,
        bus: EventBus,
        command_bus: "CommandBus",
    ) -> dict[str, list[str]]:
        del state, command_bus  # preflight ignores prior state and the command bus
        inputs = self._inputs
        warnings: list[str] = []

        # --- Sync checks (Conventions: fail-fast on local errors) ---------
        sync_checks: list[tuple[str, CheckResult]] = [
            ("config_parses", check_config_parses(inputs.config_path)),
            ("repo_path", check_repo_path(inputs.repo_path)),
            (
                "output_dir_writable",
                check_output_dir_writable(inputs.output_dir, inputs.min_disk_bytes),
            ),
            ("gitnexus_index", check_gitnexus_index(inputs.repo_path)),
            (
                "addendum_safety",
                check_addendum_safety(inputs.addendum_path, inputs.repo_path),
            ),
            (
                "language_anchors",
                check_language_anchors(
                    list(config.walker.extensions), inputs.anchors_dir
                ),
            ),
            ("sampling_ranges", check_sampling_ranges(config)),
            ("lifecycle_backend", check_lifecycle_backend(config)),
            ("runlock_dir_writable", check_runlock_dir_writable(config)),
        ]
        for name, result in sync_checks:
            await self._handle_result(name, result, bus, warnings)

        # --- Async checks (backend-specific probes) -----------------------
        if config.inference.backend == "ollama":
            ollama_url = config.inference.ollama.base_url
            ollama_model = config.inference.ollama.model
            async_checks: list[tuple[str, CheckResult]] = [
                ("ollama_reachable", await check_ollama_reachable(ollama_url)),
                (
                    "ollama_model_available",
                    await check_ollama_model_available(ollama_url, ollama_model),
                ),
            ]
        else:
            async_checks = [
                ("lms_reachable", await check_lms_reachable(self._client)),
                (
                    "model_loaded",
                    await check_model_loaded_or_loadable(self._client, config),
                ),
                (
                    "schema_with_thinking",
                    await check_schema_with_thinking(self._client, config),
                ),
                ("streaming", await check_streaming(self._client, config)),
            ]
        for name, result in async_checks:
            await self._handle_result(name, result, bus, warnings)

        return {"warnings": warnings}

    async def _handle_result(
        self,
        name: str,
        result: CheckResult,
        bus: EventBus,
        warnings: list[str],
    ) -> None:
        if result.status is CheckStatus.FAIL:
            assert result.exit_code is not None, (
                f"preflight check {name!r} returned FAIL without exit_code"
            )
            raise PreflightFailure(
                exit_code=result.exit_code,
                message=result.message,
                check_name=name,
            )
        if result.status is CheckStatus.WARN:
            warnings.append(f"{name}: {result.message}")
            from datetime import datetime, timezone

            await bus.publish(
                PreflightWarning(
                    ts=datetime.now(tz=timezone.utc),
                    run_id=self._run_id,
                    check=name,
                    message=result.message,
                )
            )


# ``Phase`` protocol assertion (defense-in-depth): build a PreflightPhase via
# ``__init_subclass__``-free dummy attributes won't work for the Protocol
# check; tests cover this at runtime.
_ = Phase  # silence unused-import — referenced for future reflection.


__all__ = [
    "CheckResult",
    "CheckStatus",
    "PreflightInputs",
    "PreflightPhase",
    "check_addendum_safety",
    "check_config_parses",
    "check_gitnexus_index",
    "check_language_anchors",
    "check_lifecycle_backend",
    "check_lms_reachable",
    "check_model_loaded_or_loadable",
    "check_ollama_model_available",
    "check_ollama_reachable",
    "check_output_dir_writable",
    "check_repo_path",
    "check_runlock_dir_writable",
    "check_sampling_ranges",
    "check_schema_with_thinking",
    "check_streaming",
]
