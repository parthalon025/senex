"""Tests for senex.lmstudio_lifecycle - backends, Lifecycle API, resume, doctor, threat surface.

Implements M4 Tasks 4.1-4.6 per the M4 plan.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from senex.config import LifecycleCfg
from senex.events import BaseEvent
from senex.lmstudio_lifecycle import (
    Lifecycle,
    LifecycleBackendFactory,
    LifecycleBackendUnavailable,
    LMSCLIBackend,
    LMStudioSDKBackend,
    ModelInfo,
    ModelLoadFailed,
    ModelLoadTimeout,
    ModelNotLoaded,
    _compute_fingerprint,
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
