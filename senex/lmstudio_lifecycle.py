"""senex.lmstudio_lifecycle - Model lifecycle management for LM Studio.

Implements spec section 5.5.2 (Model Lifecycle Management) including:
- 5.5.2.1 Acquire/Release semantics (Lifecycle high-level API).
- 5.5.2.3 Implementation backends (SDK + CLI) and canonical fingerprint.
- 5.5.2.4 Failure modes (timeouts, load failures, unload failures).
- 5.5.2.5 Events (the 11 lifecycle event types).
- 5.5.2.6 Threat surface (subprocess injection, lockfile poisoning, fingerprint substitution).
- 5.5.2.7 Resume integration (loaded_by_us=False invariant).
- SEC-4 model_id regex validation pre-subprocess.

Backends are mechanism-only: events are emitted from Lifecycle, never from a backend.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


# Exceptions ----------------------------------------------------------------


class LifecycleError(Exception):
    """Base exception for lifecycle failures."""


class LifecycleBackendUnavailable(LifecycleError):
    """Raised when neither the lmstudio SDK nor the lms CLI is available."""


class ModelLoadTimeout(LifecycleError):
    """Raised when the load wait exceeds LifecycleCfg.load_timeout_seconds."""


class ModelLoadFailed(LifecycleError):
    """Raised when the backend load reports a non-timeout failure."""


class ModelNotLoaded(LifecycleError):
    """Raised when auto_load=False and the model is not currently loaded."""


class InvalidModelId(LifecycleError, ValueError):
    """Raised when model_id fails the SEC-4 regex validation."""


class FingerprintMismatch(LifecycleError):
    """Raised on resume when probed fingerprint != checkpoint fingerprint and not allow_mixed."""


class ResumedRunCannotOwnLoad(LifecycleError):
    """Defense-in-depth: raised if internal helpers ever try loaded_by_us=True for a resumed run."""


# model_id validation -------------------------------------------------------

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_MODEL_ID_MAX_LEN = 256


def validate_model_id(model_id: str) -> str:
    """Validate model_id against the SEC-4 regex; raise InvalidModelId on failure."""
    if not isinstance(model_id, str) or not model_id:
        raise InvalidModelId(
            f"model_id must be non-empty str, got {type(model_id).__name__!r}"
        )
    if len(model_id) > _MODEL_ID_MAX_LEN:
        raise InvalidModelId(
            f"model_id length {len(model_id)} exceeds {_MODEL_ID_MAX_LEN}"
        )
    if not _MODEL_ID_RE.match(model_id):
        raise InvalidModelId(
            f"model_id {model_id!r} does not match {_MODEL_ID_RE.pattern}"
        )
    return model_id


# Fingerprint ---------------------------------------------------------------


def _compute_fingerprint(model_id: str, quant: str, checkpoint_digest: str) -> str:
    """Canonical fingerprint shared by both backends (spec 5.5.2.3)."""
    payload = json.dumps(
        [model_id, quant, checkpoint_digest], separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ModelInfo (pydantic v2 strict) --------------------------------------------


class ModelInfo(BaseModel):
    """Concrete metadata for a loaded model. Strict (extra='forbid')."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    quant: str
    checkpoint_digest: str  # sha256 hex; "unknown" sentinel allowed
    fingerprint: str  # _compute_fingerprint(model_id, quant, checkpoint_digest)
    backend: str  # "sdk" | "cli"


# Backend protocol ----------------------------------------------------------


@runtime_checkable
class LifecycleBackend(Protocol):
    async def is_loaded(self, model_id: str) -> bool: ...
    async def load(self, model_id: str, timeout: int) -> ModelInfo: ...
    async def unload(self, model_id: str) -> None: ...
    async def list_loaded(self) -> list[ModelInfo]: ...


# SDK backend ---------------------------------------------------------------


class LMStudioSDKBackend:
    """LM Studio Python SDK backend (preferred when available)."""

    backend_name = "sdk"

    def __init__(self, sdk: Any) -> None:
        # ``sdk`` is the imported ``lmstudio`` module.
        self._sdk = sdk

    @staticmethod
    def _info_from_sdk(record: Any, fallback_id: str) -> ModelInfo:
        model_id = (
            getattr(record, "model_id", None)
            or getattr(record, "id", None)
            or fallback_id
        )
        quant = (
            getattr(record, "quant", None)
            or getattr(record, "quantization", None)
            or "unknown"
        )
        digest = (
            getattr(record, "checkpoint_digest", None)
            or getattr(record, "digest", None)
            or "unknown"
        )
        fp = _compute_fingerprint(model_id, quant, digest)
        return ModelInfo(
            model_id=model_id,
            quant=quant,
            checkpoint_digest=digest,
            fingerprint=fp,
            backend=LMStudioSDKBackend.backend_name,
        )

    async def is_loaded(self, model_id: str) -> bool:
        validate_model_id(model_id)
        loaded = await self._sdk.list_loaded_models()
        for record in loaded:
            mid = getattr(record, "model_id", None) or getattr(record, "id", None)
            if mid == model_id:
                return True
        return False

    async def load(self, model_id: str, timeout: int) -> ModelInfo:
        validate_model_id(model_id)
        record = await self._sdk.llm(model_id)
        return self._info_from_sdk(record, fallback_id=model_id)

    async def unload(self, model_id: str) -> None:
        validate_model_id(model_id)
        unload_fn = getattr(self._sdk, "unload", None)
        if unload_fn is not None:
            await unload_fn(model_id)
            return
        record = await self._sdk.llm(model_id)
        await record.unload()

    async def list_loaded(self) -> list[ModelInfo]:
        records = await self._sdk.list_loaded_models()
        out: list[ModelInfo] = []
        for r in records:
            mid = getattr(r, "model_id", None) or getattr(r, "id", None) or ""
            out.append(self._info_from_sdk(r, fallback_id=mid))
        return out


# CLI backend ---------------------------------------------------------------


class LMSCLIBackend:
    """``lms`` CLI backend (fallback when SDK is not available)."""

    backend_name = "cli"

    @staticmethod
    async def _run_lms(*args: str, timeout: int | None = None) -> tuple[bytes, bytes, int]:
        """Run ``lms <args>`` via asyncio.create_subprocess_exec (list-form, shell=False).

        Caller MUST validate any model_id arg via ``validate_model_id`` before invoking.
        """
        proc = await asyncio.create_subprocess_exec(
            "lms",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if timeout is not None:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        else:
            stdout, stderr = await proc.communicate()
        return stdout, stderr, proc.returncode if proc.returncode is not None else 1

    @staticmethod
    def _info_from_cli(record: dict[str, Any], fallback_id: str) -> ModelInfo:
        model_id = str(record.get("model_id") or record.get("id") or fallback_id)
        quant = str(record.get("quant") or record.get("quantization") or "unknown")
        digest = str(record.get("checkpoint_digest") or record.get("digest") or "unknown")
        fp = _compute_fingerprint(model_id, quant, digest)
        return ModelInfo(
            model_id=model_id,
            quant=quant,
            checkpoint_digest=digest,
            fingerprint=fp,
            backend=LMSCLIBackend.backend_name,
        )

    async def is_loaded(self, model_id: str) -> bool:
        validate_model_id(model_id)
        stdout, _stderr, _rc = await self._run_lms("ps", "--json")
        try:
            records = json.loads(stdout.decode("utf-8") or "[]")
        except json.JSONDecodeError:
            return False
        if not isinstance(records, list):
            return False
        for r in records:
            if not isinstance(r, dict):
                continue
            mid = r.get("model_id") or r.get("id")
            if mid == model_id:
                return True
        return False

    async def load(self, model_id: str, timeout: int) -> ModelInfo:
        validate_model_id(model_id)
        stdout, stderr, rc = await self._run_lms("load", model_id, timeout=timeout)
        if rc != 0:
            raise ModelLoadFailed(
                f"lms load {model_id!r} failed (rc={rc}): "
                f"{stderr.decode('utf-8', 'replace')}"
            )
        try:
            payload = json.loads(stdout.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload:
            return self._info_from_cli(payload, fallback_id=model_id)
        ps_stdout, _ps_stderr, _ps_rc = await self._run_lms("ps", "--json")
        try:
            records = json.loads(ps_stdout.decode("utf-8") or "[]")
        except json.JSONDecodeError:
            records = []
        for r in records if isinstance(records, list) else []:
            if isinstance(r, dict) and (
                r.get("model_id") == model_id or r.get("id") == model_id
            ):
                return self._info_from_cli(r, fallback_id=model_id)
        return self._info_from_cli({"model_id": model_id}, fallback_id=model_id)

    async def unload(self, model_id: str) -> None:
        validate_model_id(model_id)
        _stdout, stderr, rc = await self._run_lms("unload", model_id)
        if rc != 0:
            raise ModelLoadFailed(
                f"lms unload {model_id!r} failed (rc={rc}): "
                f"{stderr.decode('utf-8', 'replace')}"
            )

    async def list_loaded(self) -> list[ModelInfo]:
        stdout, _stderr, _rc = await self._run_lms("ps", "--json")
        try:
            records = json.loads(stdout.decode("utf-8") or "[]")
        except json.JSONDecodeError:
            records = []
        out: list[ModelInfo] = []
        for r in records if isinstance(records, list) else []:
            if not isinstance(r, dict):
                continue
            mid = str(r.get("model_id") or r.get("id") or "")
            out.append(self._info_from_cli(r, fallback_id=mid))
        return out


# Backend factory -----------------------------------------------------------


class LifecycleBackendFactory:
    """Selects the best available backend (SDK preferred -> CLI fallback)."""

    @classmethod
    async def select(cls) -> LifecycleBackend:
        """Try SDK first, then CLI; raise LifecycleBackendUnavailable if neither."""
        try:
            import lmstudio

            if lmstudio is not None:
                try:
                    backend = LMStudioSDKBackend(lmstudio)
                    await lmstudio.list_loaded_models()
                except Exception:
                    pass
                else:
                    return backend
        except ImportError:
            pass

        if shutil.which("lms") is not None:
            return LMSCLIBackend()

        raise LifecycleBackendUnavailable(
            "neither lmstudio Python SDK nor lms CLI is available; "
            "install one or set lifecycle.auto_load=false"
        )


# Helpers used by Lifecycle (Tasks 4.2-4.3) ---------------------------------


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _current_pid() -> int:
    return os.getpid()
