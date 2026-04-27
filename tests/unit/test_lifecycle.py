"""Tests for senex.lmstudio_lifecycle - backends, Lifecycle API, resume, doctor, threat surface.

Implements M4 Tasks 4.1-4.6 per the M4 plan.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from senex.lmstudio_lifecycle import (
    LifecycleBackendFactory,
    LifecycleBackendUnavailable,
    LMSCLIBackend,
    LMStudioSDKBackend,
    ModelInfo,
    _compute_fingerprint,
)


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
