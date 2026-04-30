"""senex.cli_audit — ``cmd_audit`` composition root (M10 Task 10.2).

Builds config + lens + bus + subscribers, then dispatches to either:
  * ``senex.tui.app.SenexApp`` (when --no-tui not set), OR
  * ``asyncio.run(run_audit(...))`` directly (when --no-tui).

For ``--nightly``: iterates ``config.repos``; each repo is independent;
exceptions are caught and counted as exit code 3; the final exit code
is the *worst* (numeric max) across all repos.

Per spec §10 + conventions §3 (cancellation), §4 (named exceptions).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from senex.auditor import run_audit
from senex.config import (
    SenexConfig,
    UnknownConfigKey,
    load_config,
    resolve_config,
)
from senex.events import CommandBus, EventBus
from senex.lens import Lens, LensNotFound, LensValidationError
from senex.subscribers import (
    HeadlessSubscriber,
    MetricsCollectorSubscriber,
)


log = logging.getLogger(__name__)


def _resolve_config_path(explicit: str | None) -> Path | None:
    """Locate ``senex.config.toml`` per spec §6.

    Lookup order (first hit wins):
      1. ``--config <path>`` if provided
      2. ``$SENEX_CONFIG`` env var
      3. ``./senex.config.toml`` in cwd
      4. ``~/.senex/senex.config.toml`` in user home

    Returns the resolved Path if found, or None if no candidate exists.
    Explicit paths from --config or $SENEX_CONFIG are returned even when
    they don't exist (the caller surfaces a precise error for them).
    """
    import os
    if explicit:
        return Path(explicit)
    env = os.environ.get("SENEX_CONFIG")
    if env:
        return Path(env)
    cwd_candidate = Path.cwd() / "senex.config.toml"
    if cwd_candidate.exists():
        return cwd_candidate
    home_candidate = Path.home() / ".senex" / "senex.config.toml"
    if home_candidate.exists():
        return home_candidate
    return None


# Internal exit-code constants (mirror senex.auditor).
_EXIT_OK = 0
_EXIT_PARTIAL = 1
_EXIT_CONFIG = 2
_EXIT_EXTERNAL = 3
_EXIT_INTERRUPT = 130


def cmd_audit(args: argparse.Namespace) -> int:
    """``senex audit`` dispatcher. Returns process exit code.

    Reads ``args.config`` (or ``./senex.config.toml`` fallback), loads the
    lens, builds the dependency graph, and either spawns ``SenexApp.run()``
    or calls ``asyncio.run(run_audit(...))`` directly.

    When ``args.repo_path`` is ``None`` and ``--no-wizard`` is NOT set, the
    interactive launcher wizard is invoked (M11). Its return value is a
    ``RuntimeConfig`` which short-circuits ``_run_single`` and runs the
    audit directly (TUI or headless per ``--no-tui``).
    """
    cfg_path = _resolve_config_path(args.config)
    if cfg_path is None:
        searched = [str(Path.cwd() / "senex.config.toml"), str(Path.home() / ".senex" / "senex.config.toml")]
        sys.stderr.write(
            "senex audit: config not found. Searched:\n  - "
            + "\n  - ".join(searched)
            + "\nPass --config <path> or place senex.config.toml in one of these locations.\n"
        )
        return _EXIT_CONFIG
    try:
        config = load_config(cfg_path)
    except FileNotFoundError:
        sys.stderr.write(f"senex audit: config not found: {cfg_path}\n")
        return _EXIT_CONFIG
    except (UnknownConfigKey, ValueError) as exc:
        sys.stderr.write(f"senex audit: config error: {exc}\n")
        return _EXIT_CONFIG
    except Exception as exc:  # noqa: BLE001 — surface any TOML/parse error
        sys.stderr.write(f"senex audit: config parse failed: {exc}\n")
        return _EXIT_CONFIG

    # Pre-flight: when [lmstudio.sglang].manage_container=true, the wizard
    # and TUI launcher would otherwise hit "no models loaded" because they
    # probe /v1/models before lifecycle.acquire gets a chance to start the
    # container. Bring it up here so every entry path sees a healthy server.
    if not _ensure_managed_container_up(config):
        return _EXIT_EXTERNAL

    if args.nightly:
        return _run_nightly(args, config, cfg_path)

    if not args.repo_path:
        if getattr(args, "no_wizard", False):
            sys.stderr.write(
                "senex audit: either give a <repo-path> or omit --no-wizard "
                "to launch the interactive launcher.\n"
            )
            return _EXIT_CONFIG
        return _run_wizard(args, config, cfg_path)

    repo = Path(args.repo_path)
    return _run_single(args, config, cfg_path, repo)


def _run_wizard(
    args: argparse.Namespace,
    config: SenexConfig,
    config_path: Path,
) -> int:
    """Launch the interactive wizard, then dispatch TUI / headless.

    Builds an LM Studio client (lazily — the wizard tolerates ``None`` if
    the import or HTTP setup fails) and hands it to
    ``interactive_audit_setup``. The returned ``RuntimeConfig`` is then
    passed to either the TUI app or the headless flow per ``--no-tui``.
    """
    # Local import: keeps `senex.cli_audit` import-light when the wizard
    # is not used (e.g., the test suite patches `cmd_audit` directly).
    from senex.cli_wizard import (
        WizardCancelled,
        WizardError,
        interactive_audit_setup,
    )

    client = _build_wizard_client(config)

    try:
        runtime = interactive_audit_setup(config, client)
    except WizardCancelled:
        sys.stderr.write("senex audit: wizard cancelled.\n")
        return _EXIT_INTERRUPT
    except WizardError as exc:
        sys.stderr.write(f"senex audit: {exc}\n")
        return _EXIT_EXTERNAL
    except KeyboardInterrupt:
        return _EXIT_INTERRUPT

    repo = runtime.repo
    output_root = (
        runtime.output_root
        if runtime.output_root is not None
        else Path(runtime.config.output.root)
    )

    if args.no_tui:
        return _run_headless(
            args=args,
            config=runtime.config,
            lens=runtime.lens,
            config_path=config_path,
            repo=repo,
            output_root=output_root,
        )
    return _run_tui(
        args=args,
        config=runtime.config,
        lens=runtime.lens,
        config_path=config_path,
        repo=repo,
        output_root=output_root,
    )


def _ensure_managed_container_up(config: SenexConfig) -> bool:
    """Bring the SGLang container up when ``manage_container=true``.

    Idempotent — ``docker compose up -d`` is a no-op when the container is
    already running. Polls /v1/models until healthy or the configured
    ``startup_timeout_seconds`` elapses.

    Returns ``True`` on success or when container management is disabled
    (the existing manual workflow). Returns ``False`` only when management
    is on AND we failed to bring it up; caller should exit with
    ``_EXIT_EXTERNAL`` so the user gets a clear failure rather than a
    cryptic wizard probe error later.
    """
    sglang_cfg = getattr(config.lmstudio, "sglang", None)
    if sglang_cfg is None or not sglang_cfg.manage_container:
        return True

    import asyncio

    from senex.lmstudio_lifecycle import HTTPBackend, ModelLoadFailed

    backend = HTTPBackend(
        base_url=config.lmstudio.base_url,
        api_key=config.lmstudio.api_key,
        manage_container=True,
        compose_file=sglang_cfg.compose_file,
        env_file=sglang_cfg.env_file,
        startup_timeout_seconds=sglang_cfg.startup_timeout_seconds,
        via_wsl=sglang_cfg.via_wsl,
        wsl_distro=sglang_cfg.wsl_distro,
    )

    async def _start() -> None:
        await backend._run_docker(
            "up -d", timeout=sglang_cfg.startup_timeout_seconds
        )
        await backend._wait_for_health(
            timeout=sglang_cfg.startup_timeout_seconds
        )

    sys.stdout.write(
        "[sglang] Starting container (compose up -d) — "
        "this may take a moment on first run...\n"
    )
    sys.stdout.flush()
    try:
        asyncio.run(_start())
    except ModelLoadFailed as exc:
        sys.stderr.write(f"senex audit: SGLang container start failed: {exc}\n")
        return False
    except Exception as exc:  # noqa: BLE001 — surface any unexpected error
        sys.stderr.write(
            f"senex audit: unexpected error starting SGLang container: {exc}\n"
        )
        return False
    sys.stdout.write("[sglang] Container ready.\n")
    sys.stdout.flush()
    return True


def _build_wizard_client(config: SenexConfig) -> Any:
    """Construct a best-effort LM Studio client for the wizard.

    The wizard tolerates a ``None`` client (skips model probing). This
    helper isolates the import + construction so a missing optional dep
    or a transient error never aborts the wizard with a stack trace —
    the wizard surfaces the connect failure as ``WizardError`` instead.
    """
    try:
        from senex.events import EventBus
        from senex.lmstudio_client import LMStudioClient
        from senex.secret_redactor import SecretRedactor
    except ImportError:
        return None
    try:
        return LMStudioClient(
            config=config.lmstudio,
            bus=EventBus(),
            redactor=SecretRedactor(),
        )
    except Exception:  # noqa: BLE001 — defer the connect error to the wizard probe.
        return None


def _run_single(
    args: argparse.Namespace,
    config: SenexConfig,
    config_path: Path,
    repo: Path,
) -> int:
    """Run one audit. Returns the exit code from ``run_audit``."""
    cli_overrides = _cli_overrides(args)
    try:
        resolved = resolve_config(
            base=config,
            repo_path=repo,
            cli_overrides=cli_overrides,
            tui_overrides={},
        )
    except (UnknownConfigKey, ValueError) as exc:
        sys.stderr.write(f"senex audit: config resolution failed: {exc}\n")
        return _EXIT_CONFIG

    lens_name = args.lens or resolved.lens.name
    try:
        lens = Lens.load(lens_name)
    except (LensNotFound, LensValidationError) as exc:
        sys.stderr.write(f"senex audit: lens error: {exc}\n")
        return _EXIT_CONFIG

    output_root = Path(resolved.output.root)

    if args.no_tui:
        return _run_headless(
            args=args,
            config=resolved,
            lens=lens,
            config_path=config_path,
            repo=repo,
            output_root=output_root,
        )
    return _run_tui(
        args=args,
        config=resolved,
        lens=lens,
        config_path=config_path,
        repo=repo,
        output_root=output_root,
    )


def _run_headless(
    *,
    args: argparse.Namespace,
    config: SenexConfig,
    lens: Lens,
    config_path: Path,
    repo: Path,
    output_root: Path,
) -> int:
    """Headless path: build bus + subscribers, ``asyncio.run(run_audit)``.

    Subscribers are wired via ``EventBus.subscribe_local`` (the canonical
    in-process pattern used by M9 ``MonitorScreen``). DiskWriter is wired
    inside ``run_audit`` once the audit dir is known; here we only wire
    the headless stdout subscriber + the metrics collector for end-of-run
    summary access.
    """
    from senex.events import BaseEvent

    bus = EventBus()
    command_bus = CommandBus()

    metrics = MetricsCollectorSubscriber()
    headless = HeadlessSubscriber()

    async def _orchestrate() -> int:
        async def _to_metrics(ev: BaseEvent) -> None:
            try:
                await metrics.consume(ev)
            except Exception as exc:  # noqa: BLE001 — never propagate
                log.error("metrics consume failed: %s", exc)

        async def _to_headless(ev: BaseEvent) -> None:
            try:
                await headless.consume(ev)
            except Exception as exc:  # noqa: BLE001
                log.error("headless consume failed: %s", exc)

        bus.subscribe_local("Metrics", BaseEvent, _to_metrics)
        bus.subscribe_local("Headless", BaseEvent, _to_headless)
        try:
            return await run_audit(
                repo=repo,
                config=config,
                lens=lens,
                bus=bus,
                command_bus=command_bus,
                config_path=config_path,
                output_root=output_root,
                resume=bool(args.resume),
                allow_mixed_resume=bool(args.allow_mixed_resume),
            )
        finally:
            try:
                await headless.shutdown()
            except Exception as exc:  # noqa: BLE001
                log.warning("headless shutdown: %s", exc)
            try:
                await metrics.shutdown()
            except Exception as exc:  # noqa: BLE001
                log.warning("metrics shutdown: %s", exc)

    try:
        return asyncio.run(_orchestrate())
    except KeyboardInterrupt:
        return _EXIT_INTERRUPT


def _run_tui(
    *,
    args: argparse.Namespace,
    config: SenexConfig,
    lens: Lens,
    config_path: Path,
    repo: Path,
    output_root: Path,
) -> int:
    """TUI path: launch ``SenexApp`` with a pre-built ``RuntimeConfig``."""
    from senex.tui.app import SenexApp
    from senex.tui.runtime import RuntimeConfig

    app = SenexApp(config_path=config_path, repo_path_default=repo)
    runtime = RuntimeConfig(
        repo=repo,
        config=config,
        lens=lens,
        config_path=config_path,
        output_root=output_root,
        resume=bool(args.resume),
        allow_mixed_resume=bool(args.allow_mixed_resume),
    )
    app.start_audit(runtime)
    try:
        # SenexApp.run() returns the exit code (App[int]).
        rc = app.run()
    except KeyboardInterrupt:
        return _EXIT_INTERRUPT
    if rc is None:
        return _EXIT_OK
    try:
        return int(rc)
    except (TypeError, ValueError):
        return _EXIT_OK


def _run_nightly(
    args: argparse.Namespace,
    config: SenexConfig,
    config_path: Path,
) -> int:
    """Iterate ``config.repos``; return max-numeric exit code."""
    if not config.repos:
        sys.stderr.write(
            "senex audit --nightly: no [[repos]] entries in config\n"
        )
        return _EXIT_CONFIG

    worst = _EXIT_OK
    for entry in config.repos:
        repo_path = Path(entry.path)
        log.info("nightly: starting %s (%s)", entry.name, repo_path)
        try:
            rc = _run_single(args, config, config_path, repo_path)
        except KeyboardInterrupt:
            return _EXIT_INTERRUPT
        except Exception as exc:  # noqa: BLE001 — per-repo isolation
            log.error("nightly: repo %s failed: %s", entry.name, exc)
            sys.stderr.write(
                f"senex audit --nightly: repo {entry.name!r} failed: {exc}\n"
            )
            rc = _EXIT_EXTERNAL
        worst = max(worst, rc)
    return worst


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Translate CLI flags into a deep-mergeable override dict.

    Only flags that map cleanly to ``SenexConfig`` fields are surfaced here;
    flags that affect runtime behavior (--no-tui, --resume, --allow-mixed-resume)
    are consumed by the dispatcher and do NOT appear in the merged config.
    """
    overrides: dict[str, Any] = {}
    if args.include_tests:
        overrides.setdefault("walker", {})["include_tests"] = True
        overrides.setdefault("lens", {})["include_tests"] = True
    if args.lens:
        overrides.setdefault("lens", {})["name"] = args.lens
    if args.min_priority:
        overrides.setdefault("lens", {})["min_priority"] = args.min_priority
    if args.model:
        overrides.setdefault("lmstudio", {})["model"] = args.model
    if args.no_load:
        overrides.setdefault("lmstudio", {}).setdefault("lifecycle", {})[
            "auto_load"
        ] = False
    if args.no_unload:
        overrides.setdefault("lmstudio", {}).setdefault("lifecycle", {})[
            "auto_unload"
        ] = False
    return overrides


__all__ = ["cmd_audit"]
