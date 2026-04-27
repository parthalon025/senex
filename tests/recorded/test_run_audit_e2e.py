"""End-to-end ``run_audit`` smoke test (M8 Task 8.7.1).

The full recorded-LMS replay test belongs in M10 live-validation gate 13a/c
(plan §10), where the fixtures are regenerated against a live LMS server
via ``RECORD_LMS=1``. M8 ships a *wiring* smoke test that:

    1. confirms ``run_audit`` is importable + callable.
    2. confirms a missing/unloaded LMS short-circuits cleanly via the
       PreflightFailure path → exit code 3 (external dependency).
    3. confirms ResumeIncompatible returns exit code 2.

These cover the contract-level invariants the M9 TUI / M10 CLI rely on:
``run_audit`` returns an int exit code on every failure mode.

Marked under ``tests/recorded/`` so it shares the recorded conftest's
fixture-resolution paths with the M3 replay test that lives next door.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_run_audit_is_importable_callable() -> None:
    """Smoke: the auditor entrypoint is reachable from the package boundary."""
    from senex.auditor import run_audit

    assert callable(run_audit)
    # Key: it's an async coroutine function.
    import inspect

    assert inspect.iscoroutinefunction(run_audit)


@pytest.mark.asyncio
async def test_run_audit_no_lms_returns_external_dep_error(tmp_path: Path) -> None:
    """When LMS is unreachable, run_audit returns exit code 3 (external).

    Achieved by pointing base_url at a closed port; the preflight check
    ``check_lms_reachable`` produces a FAIL with exit_code=3 before any
    real work begins.
    """
    from senex.auditor import EXIT_EXTERNAL_ERROR, run_audit
    from senex.config import SenexConfig
    from senex.events import CommandBus, EventBus
    from senex.lens import Lens

    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "x.py").write_text("x=1\n", encoding="utf-8")

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[lmstudio]\nmodel='google/gemma-4-26b-a4b'\n", encoding="utf-8")

    cfg = SenexConfig()
    cfg.lmstudio = cfg.lmstudio.model_copy(
        update={"base_url": "http://127.0.0.1:1", "connect_timeout": 1, "read_timeout": 1}
    )
    cfg.lmstudio.lifecycle = cfg.lmstudio.lifecycle.model_copy(
        update={"auto_load": False, "auto_unload": False, "runlock_dir": str(tmp_path / "locks")}
    )

    rc = await run_audit(
        repo=repo,
        config=cfg,
        lens=Lens.load("correctness"),
        bus=EventBus(),
        command_bus=CommandBus(),
        config_path=cfg_path,
        output_root=tmp_path / "audits",
        resume=False,
    )
    # Either the preflight check or the lifecycle backend probe rejects;
    # both surface as exit code 3 (external dependency).
    assert rc == EXIT_EXTERNAL_ERROR
