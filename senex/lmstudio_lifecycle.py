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
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from senex.config import LifecycleCfg
    from senex.events import EventBus
    from senex.runlock import RunLock as RunLockType
    from senex.secret_redactor import SecretRedactor


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
    """Validate model_id against the SEC-4 regex; raise InvalidModelId on failure.

    Defense in depth (spec section 5.5.2.6): rejects shell metacharacters via the
    base regex, plus an explicit path-traversal rejection for ``..`` segments.
    The base regex allows ``.`` and ``/`` because legitimate model_ids contain
    them (e.g. ``google/gemma-4-26b-a4b``), but ``..`` segments are never
    legitimate and would feed a directory escape if interpreted as a filesystem
    path by either backend.
    """
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
    # Path-traversal rejection: ``..`` segments at any position.
    parts = model_id.replace("\\", "/").split("/")
    if any(seg == ".." for seg in parts):
        raise InvalidModelId(
            f"model_id {model_id!r} contains a path-traversal segment ('..')"
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


def _resume_holder_record(
    info: ModelInfo, *, run_id: str, loaded_by_us: bool
) -> dict[str, Any]:
    """Construct a holder record for the resume path.

    Defense-in-depth (spec 5.5.2.7 watch-out): a resumed run can NEVER own the
    load. Even if internal callers somehow set ``loaded_by_us=True``, raise
    ``ResumedRunCannotOwnLoad``. The public ``acquire_for_resume`` API never
    exposes this knob, but this helper exists so unit tests can verify the
    invariant and so future refactors can't bypass it silently.
    """
    if loaded_by_us is not False:
        raise ResumedRunCannotOwnLoad(
            "resume path cannot transfer load ownership; loaded_by_us must be False"
        )
    return {
        "run_id": run_id,
        "fingerprint": info.fingerprint,
        "loaded_by_us": False,
    }


# Doctor check (spec section 8.1) -------------------------------------------


class DoctorCheck(BaseModel):
    """A single preflight/doctor row.

    TODO(M10): when ``senex/phases/preflight.py`` is created, re-export this
    model from there and have callers import it from preflight; M4 declares
    it inline so the lifecycle backend check can be tested in isolation.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    status: str  # "pass" | "warn" | "fail"
    message: str


async def doctor_check_lifecycle_backend(config: Any) -> DoctorCheck:
    """Probe ``LifecycleBackendFactory.select`` and return a 3-branch DoctorCheck.

    Spec section 8.1 row "lmstudio_lifecycle_backend":
    - backend reachable           -> status='pass'.
    - no backend AND auto_load=T  -> status='fail' (run will not start).
    - no backend AND auto_load=F  -> status='warn' (manual load still works).
    """
    auto_load = bool(config.lmstudio.lifecycle.auto_load)
    try:
        backend = await LifecycleBackendFactory.select()
    except LifecycleBackendUnavailable as exc:
        if auto_load:
            return DoctorCheck(
                name="lmstudio_lifecycle_backend",
                status="fail",
                message=(
                    f"no lifecycle backend available and auto_load is enabled: "
                    f"{exc!s}"
                ),
            )
        return DoctorCheck(
            name="lmstudio_lifecycle_backend",
            status="warn",
            message=(
                f"no lifecycle backend available; auto_load is disabled so the "
                f"run can still attach to a manually-loaded model: {exc!s}"
            ),
        )
    backend_name = getattr(backend, "backend_name", "unknown")
    return DoctorCheck(
        name="lmstudio_lifecycle_backend",
        status="pass",
        message=f"lifecycle backend selected: {backend_name}",
    )


# Lifecycle high-level API --------------------------------------------------


class Lifecycle:
    """High-level lifecycle policy: decides whether to load, attach, hold, unload.

    Mechanism (load/unload, list_loaded) lives in the LifecycleBackend; events
    are emitted from this class only (per spec 5.5.2.5 / watch-out: backends are
    mechanism-only).
    """

    def __init__(
        self,
        backend: LifecycleBackend,
        bus: "EventBus | Any",
        config: "LifecycleCfg",
        redactor: "SecretRedactor",
    ) -> None:
        self._backend = backend
        self._bus = bus
        self._config = config
        self._redactor = redactor
        # Cache (model_id, run_id) -> fingerprint set at acquire so release can
        # find the right runlock without re-probing.
        self._fingerprints: dict[tuple[str, str], str] = {}

    async def _publish(self, event_cls_name: str, **fields: Any) -> None:
        """Construct an event by name and publish to the bus."""
        from senex import events as _events

        cls = getattr(_events, event_cls_name)
        event = cls(ts=datetime.now(tz=timezone.utc), run_id=fields.pop("run_id"), **fields)
        await self._bus.publish(event)

    async def _probe_info(self, model_id: str) -> ModelInfo:
        """Find the ModelInfo for an already-loaded model via list_loaded()."""
        records = await self._backend.list_loaded()
        for r in records:
            if r.model_id == model_id:
                return r
        # If we can't find it but is_loaded reported True, synthesize a minimal record.
        fp = _compute_fingerprint(model_id, "unknown", "unknown")
        return ModelInfo(
            model_id=model_id,
            quant="unknown",
            checkpoint_digest="unknown",
            fingerprint=fp,
            backend=getattr(self._backend, "backend_name", "unknown"),
        )

    async def acquire(
        self,
        model_id: str,
        run_id: str,
        runlock: "type[RunLockType]",
        *,
        auto_load: bool,
    ) -> tuple[ModelInfo, bool]:
        """Acquire the runlock for ``model_id``.

        Returns (info, loaded_by_us). See spec 5.5.2.1.
        - not loaded + auto_load=True  -> load -> runlock.acquire(loaded_by_us=True)  -> (info, True)
        - not loaded + auto_load=False -> raise ModelNotLoaded BEFORE any runlock touch
        - already loaded               -> probe info -> runlock.acquire(loaded_by_us=False) -> (info, False)
        """
        validate_model_id(model_id)
        if not await self._backend.is_loaded(model_id):
            if not auto_load:
                raise ModelNotLoaded(
                    f"model {model_id!r} is not loaded and auto_load is disabled"
                )
            # Emit ModelLoadRequested -> ModelLoadStarted -> backend.load -> ModelLoadComplete.
            await self._publish(
                "ModelLoadRequested",
                run_id=run_id,
                model_id=model_id,
                target_fingerprint="",  # unknown until load completes
            )
            await self._publish("ModelLoadStarted", run_id=run_id, model_id=model_id)
            started_at = datetime.now(tz=timezone.utc)
            try:
                info = await asyncio.wait_for(
                    self._backend.load(model_id, timeout=self._config.load_timeout_seconds),
                    timeout=self._config.load_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                await self._publish(
                    "ModelLoadFailed",
                    run_id=run_id,
                    model_id=model_id,
                    error_kind="timeout",
                    error_message=self._redactor.redact(repr(exc)),
                )
                raise ModelLoadTimeout(
                    f"load({model_id!r}) timed out after "
                    f"{self._config.load_timeout_seconds}s"
                ) from exc
            except Exception as exc:
                await self._publish(
                    "ModelLoadFailed",
                    run_id=run_id,
                    model_id=model_id,
                    error_kind="load_failed",
                    error_message=self._redactor.redact(repr(exc)),
                )
                raise ModelLoadFailed(
                    f"load({model_id!r}) failed: {self._redactor.redact(str(exc))}"
                ) from exc
            duration = (datetime.now(tz=timezone.utc) - started_at).total_seconds()
            await self._publish(
                "ModelLoadComplete",
                run_id=run_id,
                model_id=model_id,
                duration_seconds=duration,
                fingerprint=info.fingerprint,
            )
            count = runlock.acquire(
                info.fingerprint, run_id, _current_pid(), True
            )
            self._fingerprints[(model_id, run_id)] = info.fingerprint
            await self._publish(
                "RunLockAcquired",
                run_id=run_id,
                model_fingerprint=info.fingerprint,
                holder_count=count,
            )
            return info, True

        # Already loaded: attach.
        info = await self._probe_info(model_id)
        count = runlock.acquire(info.fingerprint, run_id, _current_pid(), False)
        self._fingerprints[(model_id, run_id)] = info.fingerprint
        await self._publish(
            "RunLockAcquired",
            run_id=run_id,
            model_fingerprint=info.fingerprint,
            holder_count=count,
        )
        return info, False

    async def release(
        self,
        model_id: str,
        run_id: str,
        runlock: "type[RunLockType]",
        *,
        auto_unload: bool,
        loaded_by_us: bool,
        resumed: bool = False,
    ) -> None:
        """Release the runlock; conditionally unload the model.

        Spec 5.5.2.1 release algorithm:
            count = runlock.release(fp, run_id)
            if count == 0 and we_loaded and auto_unload and not resumed:
                unload -> emit ModelUnloadComplete
            else:
                emit ModelUnloadSkipped(reason=<enum>)
        """
        validate_model_id(model_id)
        # Use the fingerprint cached at acquire time. For resume paths or
        # external callers that didn't go through acquire(), fall back to a
        # probe; if the model was unloaded externally we use a deterministic
        # synthesized fingerprint as a last resort.
        fingerprint = self._fingerprints.pop((model_id, run_id), None)
        if fingerprint is None:
            try:
                info = await self._probe_info(model_id)
                fingerprint = info.fingerprint
            except Exception:
                fingerprint = _compute_fingerprint(model_id, "unknown", "unknown")

        remaining = runlock.release(fingerprint, run_id)
        await self._publish(
            "RunLockReleased",
            run_id=run_id,
            model_fingerprint=fingerprint,
            remaining_holders=remaining,
        )

        if resumed:
            await self._publish(
                "ModelUnloadSkipped",
                run_id=run_id,
                model_id=model_id,
                reason="resumed_run_does_not_own_load",
            )
            return

        if remaining > 0:
            await self._publish(
                "ModelUnloadSkipped",
                run_id=run_id,
                model_id=model_id,
                reason="concurrent_holders",
            )
            return

        if not loaded_by_us:
            await self._publish(
                "ModelUnloadSkipped",
                run_id=run_id,
                model_id=model_id,
                reason="not_loaded_by_us",
            )
            return

        if not auto_unload:
            await self._publish(
                "ModelUnloadSkipped",
                run_id=run_id,
                model_id=model_id,
                reason="auto_unload_disabled",
            )
            return

        # All conditions met: unload.
        await self._publish("ModelUnloadStarted", run_id=run_id, model_id=model_id)
        started_at = datetime.now(tz=timezone.utc)
        try:
            await self._backend.unload(model_id)
        except Exception as exc:
            await self._publish(
                "ModelUnloadFailed",
                run_id=run_id,
                model_id=model_id,
                error_kind="unload_failed",
                error_message=self._redactor.redact(repr(exc)),
            )
            return
        duration = (datetime.now(tz=timezone.utc) - started_at).total_seconds()
        await self._publish(
            "ModelUnloadComplete",
            run_id=run_id,
            model_id=model_id,
            duration_seconds=duration,
        )

    async def acquire_for_resume(
        self,
        model_id: str,
        run_id: str,
        runlock: "type[RunLockType]",
        *,
        checkpoint_fingerprint: str,
        allow_mixed: bool,
    ) -> ModelInfo:
        """Acquire a runlock on the resume path.

        Spec 5.5.2.7 invariants:
        1. Re-probe; recompute fingerprint via the backend.
        2. If the model is unloaded externally and ``auto_load`` is enabled,
           re-load — but the runlock holder is ``loaded_by_us=False``
           UNCONDITIONALLY (a resumed run can never transfer load ownership).
        3. If the probed fingerprint differs from ``checkpoint_fingerprint``
           and ``allow_mixed`` is False, raise ``FingerprintMismatch`` BEFORE
           any runlock touch.
        4. Otherwise call ``runlock.acquire(loaded_by_us=False)`` and emit
           ``RunLockAcquired``.
        """
        validate_model_id(model_id)
        if not await self._backend.is_loaded(model_id):
            if not self._config.auto_load:
                raise ModelNotLoaded(
                    f"model {model_id!r} is not loaded and auto_load is disabled"
                )
            # Resume path may need to re-load; reuse the same emission sequence
            # as acquire(), but the holder will still be loaded_by_us=False.
            await self._publish(
                "ModelLoadRequested",
                run_id=run_id,
                model_id=model_id,
                target_fingerprint=checkpoint_fingerprint,
            )
            await self._publish("ModelLoadStarted", run_id=run_id, model_id=model_id)
            started_at = datetime.now(tz=timezone.utc)
            try:
                info = await asyncio.wait_for(
                    self._backend.load(
                        model_id, timeout=self._config.load_timeout_seconds
                    ),
                    timeout=self._config.load_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                await self._publish(
                    "ModelLoadFailed",
                    run_id=run_id,
                    model_id=model_id,
                    error_kind="timeout",
                    error_message=self._redactor.redact(repr(exc)),
                )
                raise ModelLoadTimeout(
                    f"load({model_id!r}) timed out after "
                    f"{self._config.load_timeout_seconds}s"
                ) from exc
            except Exception as exc:
                await self._publish(
                    "ModelLoadFailed",
                    run_id=run_id,
                    model_id=model_id,
                    error_kind="load_failed",
                    error_message=self._redactor.redact(repr(exc)),
                )
                raise ModelLoadFailed(
                    f"load({model_id!r}) failed: {self._redactor.redact(str(exc))}"
                ) from exc
            duration = (datetime.now(tz=timezone.utc) - started_at).total_seconds()
            await self._publish(
                "ModelLoadComplete",
                run_id=run_id,
                model_id=model_id,
                duration_seconds=duration,
                fingerprint=info.fingerprint,
            )
        else:
            info = await self._probe_info(model_id)

        # Fingerprint comparison is BEFORE runlock.acquire so a refused resume
        # never poisons the lockfile with a holder that won't release.
        if info.fingerprint != checkpoint_fingerprint:
            if not allow_mixed:
                raise FingerprintMismatch(
                    f"resume fingerprint mismatch: expected={checkpoint_fingerprint!r} "
                    f"observed={info.fingerprint!r}"
                )
            await self._publish(
                "ModelFingerprintChanged",
                run_id=run_id,
                path="",  # whole-run mismatch, not file-scoped
                expected_fingerprint=checkpoint_fingerprint,
                observed_fingerprint=info.fingerprint,
            )

        # Defense-in-depth: forbids loaded_by_us=True even from internal callers.
        _holder = _resume_holder_record(info, run_id=run_id, loaded_by_us=False)
        del _holder  # invariant check only; runlock holder built by RunLock.acquire().

        count = runlock.acquire(info.fingerprint, run_id, _current_pid(), False)
        self._fingerprints[(model_id, run_id)] = info.fingerprint
        await self._publish(
            "RunLockAcquired",
            run_id=run_id,
            model_fingerprint=info.fingerprint,
            holder_count=count,
        )
        return info
