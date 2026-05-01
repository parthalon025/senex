"""Auditor exception-handler coverage (M10 Task 10.8).

Pushes ``senex.auditor.run_audit`` coverage past 85% by exercising each
typed-exception branch (PreflightFailure / ResumeIncompatible /
AggregateFailed / RenderFatal / PhaseAborted / LifecycleBackendUnavailable /
FingerprintMismatch / LifecycleError / generic exception passthrough).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from senex.auditor import (
    EXIT_CONFIG_ERROR,
    EXIT_EXTERNAL_ERROR,
    EXIT_PARTIAL_SUCCESS,
    run_audit,
)
from senex.config import SenexConfig
from senex.events import CommandBus, EventBus
from senex.lens import Lens


class _FakeBackend:
    backend_name = "fake"

    def __init__(self) -> None:
        self.loaded = False

    async def is_loaded(self, model_id: str) -> bool:
        return self.loaded

    async def load(self, model_id: str, timeout: int) -> Any:
        from senex.inference_lifecycle import ModelInfo, _compute_fingerprint

        self.loaded = True
        fp = _compute_fingerprint(model_id, "q4", "deadbeef")
        return ModelInfo(
            model_id=model_id,
            quant="q4",
            checkpoint_digest="deadbeef",
            fingerprint=fp,
            backend="fake",
        )

    async def unload(self, model_id: str) -> None:
        self.loaded = False

    async def list_loaded(self) -> list[Any]:
        return []


async def _async_return(v: Any) -> Any:
    return v


def _build_config(tmp_path: Path) -> SenexConfig:
    cfg = SenexConfig()
    cfg.inference.lifecycle = cfg.inference.lifecycle.model_copy(
        update={
            "auto_load": False,
            "auto_unload": False,
            "runlock_dir": str(tmp_path / "locks"),
        }
    )
    return cfg


def _build_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


async def _run_with_phase_raising(
    tmp_path: Path,
    exception: BaseException,
    expected_exit: int,
) -> None:
    """Run the auditor with a phase that raises ``exception``; assert exit code."""
    repo = _build_repo(tmp_path)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[lmstudio]\nmodel='m'\n", encoding="utf-8")
    cfg = _build_config(tmp_path)

    fake = _FakeBackend()
    fake.loaded = True

    async def _boom(*args: Any, **kw: Any) -> Any:
        raise exception

    from senex.runlock import RunLock

    with patch(
        "senex.auditor.LifecycleBackendFactory.select",
        new=lambda *a, **kw: _async_return(fake),
    ), patch.object(RunLock, "acquire", lambda *a, **kw: 1), patch.object(
        RunLock, "release", lambda *a, **kw: 0
    ), patch("senex.auditor.InferenceClient") as lms_class:
        lms_inst = lms_class.return_value
        lms_inst._fingerprint_pinned = ""

        async def _aclose() -> None:
            return None

        lms_inst.aclose = _aclose

        with patch(
            "senex.phases.preflight.PreflightPhase.do_work", new=_boom
        ):
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
    assert rc == expected_exit, (
        f"expected exit {expected_exit}, got {rc} for {type(exception).__name__}"
    )


@pytest.mark.asyncio
async def test_preflight_failure_returns_exit_code(tmp_path: Path) -> None:
    """``PreflightFailure(exit_code=2)`` propagates as exit 2."""
    from senex.phases.base import PreflightFailure

    await _run_with_phase_raising(
        tmp_path,
        PreflightFailure(exit_code=2, message="bad", check_name="x"),
        2,
    )


@pytest.mark.asyncio
async def test_preflight_failure_external_error_exit_3(tmp_path: Path) -> None:
    from senex.phases.base import PreflightFailure

    await _run_with_phase_raising(
        tmp_path,
        PreflightFailure(exit_code=3, message="lms", check_name="lms_reachable"),
        3,
    )


@pytest.mark.asyncio
async def test_aggregate_failed_returns_partial_success(tmp_path: Path) -> None:
    from senex.phases.base import AggregateFailed

    await _run_with_phase_raising(
        tmp_path,
        AggregateFailed("aggregation crashed", exit_code=1),
        EXIT_PARTIAL_SUCCESS,
    )


@pytest.mark.asyncio
async def test_phase_aborted_returns_external_error(tmp_path: Path) -> None:
    from senex.phases.base import PhaseAborted

    await _run_with_phase_raising(
        tmp_path,
        PhaseAborted("phase blew up"),
        EXIT_EXTERNAL_ERROR,
    )


@pytest.mark.asyncio
async def test_render_fatal_returns_external_error(tmp_path: Path) -> None:
    from senex.phases.base import RenderFatal

    await _run_with_phase_raising(
        tmp_path,
        RenderFatal("renderer borked"),
        EXIT_EXTERNAL_ERROR,
    )


@pytest.mark.asyncio
async def test_resume_incompatible_returns_config_error(tmp_path: Path) -> None:
    """``ResumeIncompatible`` lifted by run_audit returns the exception's exit_code."""
    from senex.phases.base import ResumeIncompatible

    await _run_with_phase_raising(
        tmp_path,
        ResumeIncompatible("stale checkpoint"),
        EXIT_CONFIG_ERROR,
    )


@pytest.mark.asyncio
async def test_lifecycle_backend_unavailable_returns_external_error(
    tmp_path: Path,
) -> None:
    """``LifecycleBackendUnavailable`` raised inside the run -> exit 3."""
    from senex.inference_lifecycle import LifecycleBackendUnavailable

    await _run_with_phase_raising(
        tmp_path,
        LifecycleBackendUnavailable("no backend"),
        EXIT_EXTERNAL_ERROR,
    )


@pytest.mark.asyncio
async def test_fingerprint_mismatch_returns_external_error(tmp_path: Path) -> None:
    """``FingerprintMismatch`` from inside the lifecycle CM -> exit 3."""
    from senex.inference_lifecycle import FingerprintMismatch

    await _run_with_phase_raising(
        tmp_path,
        FingerprintMismatch("expected X, got Y"),
        EXIT_EXTERNAL_ERROR,
    )


@pytest.mark.asyncio
async def test_lifecycle_error_returns_external_error(tmp_path: Path) -> None:
    """A generic ``LifecycleError`` (parent of LifecycleBackendUnavailable) -> exit 3."""
    from senex.inference_lifecycle import LifecycleError

    await _run_with_phase_raising(
        tmp_path,
        LifecycleError("backend timed out"),
        EXIT_EXTERNAL_ERROR,
    )


@pytest.mark.asyncio
async def test_unexpected_exception_propagates(tmp_path: Path) -> None:
    """Non-typed exceptions re-raise so tests / programmers see real bugs."""
    repo = _build_repo(tmp_path)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[lmstudio]\nmodel='m'\n", encoding="utf-8")
    cfg = _build_config(tmp_path)

    fake = _FakeBackend()
    fake.loaded = True

    async def _boom(*args: Any, **kw: Any) -> Any:
        raise ValueError("not a senex-typed exception")

    from senex.runlock import RunLock

    with patch(
        "senex.auditor.LifecycleBackendFactory.select",
        new=lambda *a, **kw: _async_return(fake),
    ), patch.object(RunLock, "acquire", lambda *a, **kw: 1), patch.object(
        RunLock, "release", lambda *a, **kw: 0
    ), patch("senex.auditor.InferenceClient") as lms_class:
        lms_inst = lms_class.return_value
        lms_inst._fingerprint_pinned = ""

        async def _aclose() -> None:
            return None

        lms_inst.aclose = _aclose

        with patch(
            "senex.phases.preflight.PreflightPhase.do_work", new=_boom
        ):
            with pytest.raises(ValueError):
                await run_audit(
                    repo=repo,
                    config=cfg,
                    lens=Lens.load("correctness"),
                    bus=EventBus(),
                    command_bus=CommandBus(),
                    config_path=cfg_path,
                    output_root=tmp_path / "audits",
                    resume=False,
                )
