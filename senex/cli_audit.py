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
    """
    cfg_path = Path(args.config) if args.config else Path("senex.config.toml")
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

    if args.nightly:
        return _run_nightly(args, config, cfg_path)

    if not args.repo_path:
        sys.stderr.write(
            "senex audit: <repo-path> is required (or use --nightly)\n"
        )
        return _EXIT_CONFIG

    repo = Path(args.repo_path)
    return _run_single(args, config, cfg_path, repo)


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
