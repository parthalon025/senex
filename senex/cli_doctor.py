"""senex.cli_doctor — ``cmd_doctor`` (M10 Task 10.3).

Runs every preflight check from ``senex.phases.preflight`` against either a
target repo path OR every ``[[repos]]`` entry. Prints a human table by
default; ``--json`` emits the documented schema. Aggregates exit code as
the worst (max numeric) over all checks across all repos.

Schema (single-repo --json output):

    {
      "version": 1,
      "repo": "/abs/path",
      "checks": [
        {"name": "config_parses", "status": "pass", "message": "...", "details": {}},
        ...
      ],
      "exit_code": 0
    }

Multi-repo --json output is a JSON list of these objects.

Per spec §11 (security): all string values are run through ``SecretRedactor``
before serialization.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from senex.config import (
    SenexConfig,
    UnknownConfigKey,
    load_config,
)
from senex.phases.preflight import (
    CheckResult,
    CheckStatus,
    check_addendum_safety,
    check_config_parses,
    check_gitnexus_index,
    check_language_anchors,
    check_lifecycle_backend,
    check_lms_reachable,
    check_model_loaded_or_loadable,
    check_ollama_model_available,
    check_ollama_reachable,
    check_output_dir_writable,
    check_repo_path,
    check_runlock_dir_writable,
    check_sampling_ranges,
    check_schema_with_thinking,
    check_streaming,
)
from senex.secret_redactor import SecretRedactor


log = logging.getLogger(__name__)


_EXIT_OK = 0
_EXIT_PARTIAL = 1
_EXIT_CONFIG = 2
_EXIT_EXTERNAL = 3


def cmd_doctor(args: argparse.Namespace) -> int:
    """``senex doctor`` dispatcher. Returns process exit code."""
    cfg_path = Path(args.config) if args.config else Path("senex.config.toml")
    config: SenexConfig | None = None
    config_check = check_config_parses(cfg_path)
    if config_check.status is CheckStatus.PASS:
        try:
            config = load_config(cfg_path)
        except (FileNotFoundError, UnknownConfigKey, ValueError) as exc:
            # Re-issue as a FAIL check so the report includes it.
            config_check = CheckResult(
                status=CheckStatus.FAIL,
                message=f"config load failed: {exc}",
                exit_code=2,
            )
            config = None

    if args.repo_path:
        repos: list[Path] = [Path(args.repo_path)]
    else:
        if config is None or not config.repos:
            sys.stderr.write(
                "senex doctor: no <repo-path> and no [[repos]] in config\n"
            )
            return _EXIT_CONFIG
        repos = [Path(r.path) for r in config.repos]

    redactor = SecretRedactor()
    reports: list[dict[str, Any]] = []
    worst = _EXIT_OK
    for repo in repos:
        report = _build_report(
            cfg_path=cfg_path,
            repo=repo,
            config=config,
            config_check=config_check,
            redactor=redactor,
        )
        reports.append(report)
        worst = max(worst, int(report["exit_code"]))

    as_json = bool(getattr(args, "as_json", False))
    if as_json:
        if args.repo_path:
            sys.stdout.write(json.dumps(reports[0], indent=2) + "\n")
        else:
            sys.stdout.write(json.dumps(reports, indent=2) + "\n")
    else:
        for report in reports:
            _print_human(report)

    return worst


# ---------------------------------------------------------------------------
# Per-repo report building
# ---------------------------------------------------------------------------


def _build_report(
    *,
    cfg_path: Path,
    repo: Path,
    config: SenexConfig | None,
    config_check: CheckResult,
    redactor: SecretRedactor,
) -> dict[str, Any]:
    """Run all checks against ``repo``; redact + return JSON-shaped dict."""
    rows: list[tuple[str, CheckResult]] = []
    rows.append(("config_parses", config_check))

    if config is None:
        # Config failed to load; further checks would error. Bail with the one row.
        return _finalize_report(repo, rows, redactor)

    rows.append(("repo_path", check_repo_path(repo)))
    output_dir = Path(config.output.root)
    rows.append(
        (
            "output_dir_writable",
            check_output_dir_writable(output_dir, 1024 * 1024),
        )
    )
    rows.append(("gitnexus_index", check_gitnexus_index(repo)))
    rows.append(("addendum_safety", check_addendum_safety(None, repo)))
    rows.append(
        (
            "language_anchors",
            check_language_anchors(
                list(config.walker.extensions),
                Path(__file__).parent / "prompts",
            ),
        )
    )
    rows.append(("sampling_ranges", check_sampling_ranges(config)))
    rows.append(("lifecycle_backend", check_lifecycle_backend(config)))
    rows.append(("runlock_dir_writable", check_runlock_dir_writable(config)))

    # Async checks — run via asyncio.run unless disabled (for testability).
    try:
        async_rows = _run_async_checks(config)
    except Exception as exc:  # noqa: BLE001
        log.warning("async preflight checks failed: %s", exc)
        async_rows = []
    rows.extend(async_rows)
    return _finalize_report(repo, rows, redactor)


def _run_async_checks(
    config: SenexConfig,
) -> list[tuple[str, CheckResult]]:
    """Run the four async preflight checks against a fresh LMSClient.

    Separated as a top-level function so tests can ``patch`` it cheaply
    without booting an LMS HTTP client.
    """
    from senex.events import EventBus
    from senex.inference_client import InferenceClient

    out: list[tuple[str, CheckResult]] = []

    async def _run() -> list[tuple[str, CheckResult]]:
        if config.inference.backend == "ollama":
            out.append((
                "ollama_reachable",
                await check_ollama_reachable(config.inference.ollama.base_url),
            ))
            out.append((
                "ollama_model_available",
                await check_ollama_model_available(
                    config.inference.ollama.base_url,
                    config.inference.ollama.model,
                ),
            ))
            return out

        bus = EventBus()
        redactor = SecretRedactor()
        client = InferenceClient(config.inference, bus=bus, redactor=redactor, backend_type="sglang")
        try:
            out.append(("lms_reachable", await check_lms_reachable(client)))
            out.append((
                "model_loaded",
                await check_model_loaded_or_loadable(client, config),
            ))
            out.append((
                "schema_with_thinking",
                await check_schema_with_thinking(client, config),
            ))
            out.append(("streaming", await check_streaming(client, config)))
            if config.inference.backend == "auto":
                out.append((
                    "ollama_reachable",
                    await check_ollama_reachable(config.inference.ollama.base_url),
                ))
                out.append((
                    "ollama_model_available",
                    await check_ollama_model_available(
                        config.inference.ollama.base_url,
                        config.inference.ollama.model,
                    ),
                ))
        finally:
            await client.aclose()
        return out

    return asyncio.run(_run())


def _finalize_report(
    repo: Path,
    rows: list[tuple[str, CheckResult]],
    redactor: SecretRedactor,
) -> dict[str, Any]:
    """Convert (name, CheckResult) pairs to the documented JSON shape."""
    checks: list[dict[str, Any]] = []
    worst = _EXIT_OK
    for name, result in rows:
        check_obj: dict[str, Any] = {
            "name": name,
            "status": result.status.value,
            "message": redactor.redact(result.message or ""),
            "details": {},
        }
        if result.status is CheckStatus.FAIL and result.exit_code is not None:
            worst = max(worst, int(result.exit_code))
            check_obj["exit_code"] = int(result.exit_code)
        checks.append(check_obj)
    return {
        "version": 1,
        "repo": str(repo),
        "checks": checks,
        "exit_code": worst,
    }


# ---------------------------------------------------------------------------
# Human output
# ---------------------------------------------------------------------------


def _print_human(report: dict[str, Any]) -> None:
    """Print a simple aligned table; exit code at the bottom."""
    out = sys.stdout
    out.write(f"\nDoctor report — {report['repo']}\n")
    out.write("-" * 60 + "\n")
    out.write(f"{'CHECK':32s} {'STATUS':6s} MESSAGE\n")
    for c in report["checks"]:
        msg = c.get("message", "") or ""
        out.write(f"{c['name']:32s} {c['status']:6s} {msg}\n")
    out.write(f"\nExit code: {report['exit_code']}\n")
    out.flush()


__all__ = ["cmd_doctor"]
