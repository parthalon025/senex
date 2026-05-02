"""senex.auditor — async ``run_audit`` coroutine (M8 orchestrator).

Implements spec §3 (architecture) + §4 (data flow phases 1-5) + §5.5.2.1
(lifecycle phase block) + §8.3 (run-level recovery) + §8.5 (resume hash
discipline) + §ARCH-3 (phases as objects) + §ARCH-15 (checkpoint state
machine).

``run_audit`` is the producer side of the senex contract: it iterates the
five Phase objects, brackets the run with the Lifecycle context manager,
maintains the checkpoint state machine, and emits ``RunStart`` / ``RunComplete``.
The TUI (M9) and CLI (M10) consume events on the bus; neither imports this
module's internals — the auditor only publishes.

Import-boundary rule (spec §3.2): NO ``senex.tui`` import here. The AST
test in ``tests/unit/test_import_boundaries.py`` enforces this.

Exit code surface (R11 collapse):
    0   — full success.
    1   — partial success (some files errored OR ``AggregateFailed``).
    2   — config / setup error (incl. ``ResumeIncompatible``,
          ``PreflightFailure(exit_code=2)``).
    3   — external dependency error (``PreflightFailure(exit_code=3)``).
    130 — interrupted (``KeyboardInterrupt``).

Lifecycle is wrapped in an ``async with`` so ``Lifecycle.release`` runs on
ANY exception path. This is non-negotiable; any other pattern leaks the
runlock when a phase crashes.
"""
from __future__ import annotations

import contextlib
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator, Callable, Coroutine

from ulid import ULID

from senex.atomic_io import write_text_atomic
from senex.checkpoint import (
    Checkpoint,
    CheckpointCorrupt,
    CheckpointSchemaError,
)
from senex.events import EventBus, RunComplete, RunStart
from senex.findings_partial import FindingsPartialWriter
from senex.graph_awareness import GitNexusCLIProvider
from senex.inference_client import InferenceClient
from senex.inference_lifecycle import (
    FingerprintMismatch,
    Lifecycle,
    LifecycleBackendFactory,
    LifecycleBackendUnavailable,
    ModelInfo,
)
from senex.render_models import RunMetadata
from senex.renderer import Renderer
from senex.runlock import RunLock
from senex.secret_redactor import SecretRedactor
from senex.subscribers import DiskWriterSubscriber
from senex.tools.pack_hash import compute_tool_pack_hash
from senex.tools.registry import ToolRegistry

from .phases import (
    AggregateFailed,
    AggregatePhase,
    CrosscutPhase,
    DiscoveryPhase,
    FileAuditPhase,
    Phase,
    PhaseAborted,
    PreflightFailure,
    PreflightInputs,
    PreflightPhase,
    RenderFatal,
    ResumeIncompatible,
)

if TYPE_CHECKING:  # pragma: no cover
    from senex.config import SenexConfig
    from senex.events import BaseEvent, CommandBus
    from senex.lens import Lens

log = logging.getLogger(__name__)


# Exit code constants (R11 collapse).
EXIT_SUCCESS: int = 0
EXIT_PARTIAL_SUCCESS: int = 1
EXIT_CONFIG_ERROR: int = 2
EXIT_EXTERNAL_ERROR: int = 3
EXIT_INTERRUPTED: int = 130


def _now() -> datetime:
    return datetime.now(tz=UTC)


def compute_audit_dir(
    repo: Path, output_root: Path, run_id_short: str
) -> Path:
    """``<output_root>/<repo_name>/<DATE>-<run_id_short>/`` per POL-1."""
    date = time.strftime("%Y-%m-%d")
    return output_root / repo.name / f"{date}-{run_id_short}"


@asynccontextmanager
async def lifecycle_acquire_or_resume(
    *,
    config: SenexConfig,
    run_id: str,
    bus: EventBus,
    redactor: SecretRedactor,
    resume: bool,
    checkpoint_fingerprint: str | None,
) -> AsyncIterator[tuple[ModelInfo, bool]]:
    """Bracket the audit with ``Lifecycle.acquire`` / ``release``.

    On entry: select backend; call ``acquire`` (fresh) or ``acquire_for_resume``;
    yield ``(ModelInfo, loaded_by_us)``. ``loaded_by_us`` is False on the
    resume path (spec §5.5.2.7).

    On exit (incl. exception): ``Lifecycle.release`` always runs.

    Raises:
        LifecycleBackendUnavailable: no backend (SDK or CLI) available and
            auto_load is enabled.
        FingerprintMismatch: resume + observed fingerprint != checkpoint
            fingerprint and ``allow_mixed`` is False.
        ModelLoadFailed / ModelLoadTimeout: backend load failed.
    """
    backend = await LifecycleBackendFactory.select(config.inference)
    lifecycle = Lifecycle(
        backend=backend,
        bus=bus,
        config=config.inference.lifecycle,
        redactor=redactor,
    )
    model_id = (
        config.inference.ollama.model
        if config.inference.backend == "ollama"
        else config.inference.model
    )
    loaded_by_us = False
    info: ModelInfo

    if resume and checkpoint_fingerprint:
        info = await lifecycle.acquire_for_resume(
            model_id=model_id,
            run_id=run_id,
            runlock=RunLock,
            checkpoint_fingerprint=checkpoint_fingerprint,
            allow_mixed=config.inference.lifecycle.allow_mixed,
        )
        # Resume can never own the load (defense-in-depth in lifecycle).
        loaded_by_us = False
    else:
        info, loaded_by_us = await lifecycle.acquire(
            model_id=model_id,
            run_id=run_id,
            runlock=RunLock,
            auto_load=config.inference.lifecycle.auto_load,
        )

    try:
        yield info, loaded_by_us
    finally:
        with contextlib.suppress(Exception):
            await lifecycle.release(
                model_id=model_id,
                run_id=run_id,
                runlock=RunLock,
                auto_unload=config.inference.lifecycle.auto_unload,
                loaded_by_us=loaded_by_us,
                resumed=resume,
            )


def _hash_resume_fields(
    *,
    config_hash: str,
    prompt_hash: str,
    model_fingerprint: str,
    tool_pack_hash: str,
    lens_version: str,
) -> dict[str, str]:
    return {
        "config_hash": config_hash,
        "prompt_hash": prompt_hash,
        "model_fingerprint": model_fingerprint,
        "tool_pack_hash": tool_pack_hash,
        "lens_version": lens_version,
    }


def _init_run_identity(
    repo: Path,
    output_root: Path,
) -> tuple[str, str, Path]:
    """Create ULID-based run_id, run_id_short, and audit_dir (mkdir'd)."""
    run_id_full = str(ULID())
    run_id_short = run_id_full[:8]
    audit_dir = compute_audit_dir(repo, output_root, run_id_short)
    audit_dir.mkdir(parents=True, exist_ok=True)
    return run_id_full, run_id_short, audit_dir


def _register_default_tools_and_pack(
    lens: Lens,
    config: SenexConfig,
) -> tuple[ToolRegistry, list[Any], str]:
    """Register v1 default tools, resolve lens tools, compute tool_pack_hash."""
    from senex.tools import register_default_tools  # local import: lazy load

    registry = ToolRegistry()
    register_default_tools(registry)
    enabled_tools = lens.openai_tools_for(
        registry=registry, config_subset=config.inference.tools.enabled_tools
    )
    try:
        tool_pack_hash = compute_tool_pack_hash(enabled_tools, registry)
    except KeyError:
        tool_pack_hash = ""
    return registry, enabled_tools, tool_pack_hash


async def _resolve_or_resume_checkpoint(
    *,
    audit_dir: Path,
    run_id_full: str,
    resume: bool,
    allow_mixed_resume: bool,
    config_hash: str,
    prompt_hash: str,
    tool_pack_hash: str,
    lens_version: str,
    bus: EventBus,
) -> tuple[Checkpoint, str, str] | tuple[None, str, str]:
    """Load existing checkpoint (resume) or create fresh one.

    Returns (cp, run_id_full, run_id_short).
    Returns (None, run_id_full, run_id_short) only when checkpoint is corrupt
    and the caller should publish RunComplete + return EXIT_CONFIG_ERROR.
    Actually raises to signal early-exit: returns None to signal error path.
    """
    checkpoint_path = audit_dir / "checkpoint.json"
    if resume and checkpoint_path.exists():
        try:
            cp = Checkpoint.load(audit_dir)
        except (CheckpointCorrupt, CheckpointSchemaError) as exc:
            log.error("checkpoint corrupt: %s", exc)
            await bus.publish(_runcomplete(run_id_full, EXIT_CONFIG_ERROR, totals={}))
            return None, run_id_full, run_id_full[:8]
        if not cp.is_compatible(
            _hash_resume_fields(
                config_hash=config_hash,
                prompt_hash=prompt_hash,
                model_fingerprint=cp.data.get("model_fingerprint", ""),
                tool_pack_hash=tool_pack_hash,
                lens_version=lens_version,
            )
        ) and not allow_mixed_resume:
            err = ResumeIncompatible(
                "checkpoint hash mismatch (use --allow-mixed-resume to override)"
            )
            await bus.publish(_runcomplete(run_id_full, err.exit_code, totals={}))
            return None, run_id_full, run_id_full[:8]
        run_id_full = cp.data["run_id"]
        run_id_short = run_id_full[:8]
    else:
        cp = Checkpoint.create(
            audit_dir,
            run_id=run_id_full,
            config_hash=config_hash,
            prompt_hash=prompt_hash,
            model_fingerprint="pending",
            tool_pack_hash=tool_pack_hash,
            lens_version=lens_version,
        )
        run_id_short = run_id_full[:8]
    return cp, run_id_full, run_id_short


def _wire_disk_writer(
    audit_dir: Path,
    bus: EventBus,
) -> tuple[DiskWriterSubscriber, Any]:
    """Create and subscribe DiskWriterSubscriber; return (writer, handle)."""
    disk_writer = DiskWriterSubscriber(audit_dir=audit_dir)
    handle = bus.subscribe_local(
        "DiskWriter", _events_BaseEvent(), _disk_writer_dispatch(disk_writer)
    )
    return disk_writer, handle


async def _pin_client_fingerprint(
    client: InferenceClient,
    config: SenexConfig,
    fallback_fingerprint: str,
) -> None:
    """Pin client fingerprint from /v1/models; fall back to lifecycle fingerprint."""
    try:
        _http_models = await client.list_loaded_models()
        _target = next(
            (m for m in _http_models if m.id == config.inference.model),
            None,
        )
        if _target is not None:
            client._fingerprint_pinned = client.compute_fingerprint(_target)
        else:
            client._fingerprint_pinned = fallback_fingerprint
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        log.warning(
            "could not derive client-side fingerprint pin (%s); "
            "using lifecycle fingerprint",
            exc,
        )
        client._fingerprint_pinned = fallback_fingerprint


def _build_phases(
    *,
    audit_dir: Path,
    repo: Path,
    output_root: Path,
    run_id_full: str,
    run_id_short: str,
    config: SenexConfig,
    config_path: Path,
    client: InferenceClient,
    registry: ToolRegistry,
    enabled_tools: list[Any],
    graph_provider: Any,
    renderer: Renderer,
    partial_writer: FindingsPartialWriter,
    run_metadata: RunMetadata,
    config_hash: str,
    prompt_hash: str,
    bus: EventBus,
    redactor: SecretRedactor,
) -> list[Phase]:
    """Construct and return the ordered list of Phase objects."""
    from senex.compaction import Compactor

    def compactor_factory(file: Path) -> Any:
        return Compactor(
            client=client,
            prompt_path=Path(__file__).parent / "prompts" / "compaction.md",
            schema_path=Path(__file__).parent
            / "schema"
            / "compaction_response.schema.json",
            config=config.inference.compaction,
            bus=bus,
            run_id=run_id_full,
            path=file,
            model_id=(
                config.inference.ollama.model
                if config.inference.backend == "ollama"
                else config.inference.model
            ),
            redactor=redactor,
        )

    preflight_inputs = PreflightInputs(
        config_path=config_path,
        repo_path=repo,
        output_dir=output_root,
        min_disk_bytes=1024 * 1024,
        addendum_path=None,
        anchors_dir=Path(__file__).parent / "prompts",
    )
    return [
        PreflightPhase(client=client, inputs=preflight_inputs, run_id=run_id_full),
        DiscoveryPhase(repo=repo, run_id=run_id_full),
        FileAuditPhase(
            audit_dir=audit_dir,
            repo_root=repo,
            repo_name=repo.name,
            run_id=run_id_full,
            run_id_short=run_id_short,
            client=client,
            compactor_factory=compactor_factory,
            graph_provider=graph_provider,
            renderer=renderer,
            partial_writer=partial_writer,
            tool_registry=registry,
            lens_tools_resolved=enabled_tools,
            config_hash=config_hash,
            prompt_hash=prompt_hash,
        ),
        CrosscutPhase(audit_dir=audit_dir, client=client, run_id=run_id_full),
        AggregatePhase(audit_dir=audit_dir, run_metadata=run_metadata, run_id=run_id_full),
    ]


async def _run_phase_loop(
    *,
    phases: list[Phase],
    cp: Checkpoint,
    audit_dir: Path,
    lens: Lens,
    config: SenexConfig,
    bus: EventBus,
    command_bus: CommandBus,
    run_metadata: RunMetadata,
) -> tuple[Any, bool, RunMetadata]:
    """Execute phases in order; return (phase_state, partial_success, run_metadata)."""
    phase_state: Any = None
    partial_success = False
    for phase in phases:
        if cp.data["phase_status"].get(phase.name) == "complete":
            log.info("resume: skipping completed phase %s", phase.name)
            continue

        Checkpoint.set_phase(audit_dir, phase.name, "in_progress")  # type: ignore[arg-type]
        read_state = await phase.read_state(audit_dir)
        input_state = read_state if read_state is not None else phase_state
        phase_state = await phase.do_work(input_state, lens, config, bus, command_bus)
        await phase.write_state(audit_dir, phase_state)
        Checkpoint.set_phase(audit_dir, phase.name, "complete")  # type: ignore[arg-type]

        if phase.name == "file_audit" and isinstance(phase_state, dict):
            if phase_state.get("errored") or phase_state.get("skipped"):
                partial_success = True
            completed = phase_state.get("completed") or []
            errored = phase_state.get("errored") or []
            skipped = phase_state.get("skipped") or []
            run_metadata = run_metadata.model_copy(
                update={
                    "files_audited": len(completed),
                    "files_errored": len(errored),
                    "files_skipped": len(skipped),
                }
            )
            for p in phases:
                if isinstance(p, AggregatePhase):
                    p._run_metadata = run_metadata

    return phase_state, partial_success, run_metadata


async def _resolve_graph_provider(repo: Path, bus: EventBus) -> Any:
    """Resolve graph provider; fall back to noop on any error."""
    try:
        return await GitNexusCLIProvider.preflight(repo_name=repo.name, bus=bus)
    except Exception as exc:  # noqa: BLE001 — warn-only path
        log.warning("gitnexus unavailable: %s", exc)
        return _NoopGraphProvider()


async def _execute_lifecycle_body(
    *,
    model_info: ModelInfo,
    cp: Checkpoint,
    audit_dir: Path,
    repo: Path,
    output_root: Path,
    run_id_full: str,
    run_id_short: str,
    config: SenexConfig,
    config_path: Path,
    lens: Lens,
    bus: EventBus,
    command_bus: CommandBus,
    redactor: SecretRedactor,
    registry: ToolRegistry,
    enabled_tools: list[Any],
    config_hash: str,
    prompt_hash: str,
    tool_pack_hash: str,
    started_at: datetime,
) -> tuple[int, FindingsPartialWriter]:
    """Body of the lifecycle async-with block; returns (exit_code, partial_writer)."""
    if cp.data["model_fingerprint"] == "pending":
        _patch_checkpoint_fingerprint(audit_dir, model_info.fingerprint)
    cp = Checkpoint.load(audit_dir)

    _bt = "ollama" if config.inference.backend == "ollama" else "sglang"
    client = InferenceClient(
        config=config.inference, bus=bus, redactor=redactor, backend_type=_bt
    )
    await _pin_client_fingerprint(client, config, model_info.fingerprint)

    renderer = Renderer(audit_dir, redactor)
    partial_writer = FindingsPartialWriter(audit_dir)
    graph_provider = await _resolve_graph_provider(repo, bus)

    run_metadata = RunMetadata(
        repo=repo.name,
        run_id=run_id_full,
        run_id_short=run_id_short,
        audit_dir=str(audit_dir),
        model=config.inference.model,
        model_fingerprint=model_info.fingerprint,
        lens=lens.name,
        lens_version=lens.version,
        started_at=started_at.isoformat(),
        duration_seconds=0.0,
        config_hash=config_hash,
        prompt_hash=prompt_hash,
        tool_pack_hash=tool_pack_hash,
        date=time.strftime("%Y-%m-%d"),
    )

    phases = _build_phases(
        audit_dir=audit_dir,
        repo=repo,
        output_root=output_root,
        run_id_full=run_id_full,
        run_id_short=run_id_short,
        config=config,
        config_path=config_path,
        client=client,
        registry=registry,
        enabled_tools=enabled_tools,
        graph_provider=graph_provider,
        renderer=renderer,
        partial_writer=partial_writer,
        run_metadata=run_metadata,
        config_hash=config_hash,
        prompt_hash=prompt_hash,
        bus=bus,
        redactor=redactor,
    )

    await bus.publish(
        RunStart(
            ts=_now(),
            run_id=run_id_full,
            repo=str(repo),
            audit_dir=str(audit_dir),
            model=config.inference.model,
            lens=lens.name,
            lens_version=lens.version,
            config_hash=config_hash,
            prompt_hash=prompt_hash,
            model_fingerprint=model_info.fingerprint,
            tool_pack_hash=tool_pack_hash,
            started_at=started_at,
        )
    )

    phase_state, partial_success, run_metadata = await _run_phase_loop(
        phases=phases,
        cp=cp,
        audit_dir=audit_dir,
        lens=lens,
        config=config,
        bus=bus,
        command_bus=command_bus,
        run_metadata=run_metadata,
    )

    _snapshot_config(config, audit_dir)
    duration = (_now() - started_at).total_seconds()
    totals = _tally_totals(audit_dir, run_metadata, phase_state)
    exit_code = EXIT_PARTIAL_SUCCESS if partial_success else EXIT_SUCCESS
    await bus.publish(
        RunComplete(
            ts=_now(),
            run_id=run_id_full,
            duration_seconds=duration,
            totals=totals,
            exit_status=exit_code,
        )
    )
    await client.aclose()
    return exit_code, partial_writer


async def _handle_phase_exception(
    exc: BaseException,
    run_id_full: str,
    bus: EventBus,
) -> int:
    """Map a caught exception to exit code + publish RunComplete; return exit code.

    Returns -1 for exceptions that should propagate (not handled here).
    Raises KeyboardInterrupt through unchanged.
    """
    if isinstance(exc, PreflightFailure):
        log.error("preflight failed [%s]: %s", exc.check_name, exc.message)
        await bus.publish(_runcomplete(run_id_full, exc.exit_code, totals={}))
        return exc.exit_code
    if isinstance(exc, ResumeIncompatible):
        log.error("resume incompatible: %s", exc.message)
        await bus.publish(_runcomplete(run_id_full, exc.exit_code, totals={}))
        return exc.exit_code
    if isinstance(exc, AggregateFailed):
        log.error("aggregate failed: %s", exc.message)
        await bus.publish(_runcomplete(run_id_full, exc.exit_code, totals={}))
        return exc.exit_code
    if isinstance(exc, (RenderFatal, PhaseAborted)):
        log.error("run aborted: %s", exc)
        await bus.publish(_runcomplete(run_id_full, EXIT_EXTERNAL_ERROR, totals={}))
        return EXIT_EXTERNAL_ERROR
    if isinstance(exc, (LifecycleBackendUnavailable, FingerprintMismatch)):
        log.error("lifecycle: %s", exc)
        await bus.publish(_runcomplete(run_id_full, EXIT_EXTERNAL_ERROR, totals={}))
        return EXIT_EXTERNAL_ERROR
    if isinstance(exc, Exception):
        from senex.inference_lifecycle import LifecycleError

        if isinstance(exc, LifecycleError):
            log.error("lifecycle error: %s", exc)
            await bus.publish(_runcomplete(run_id_full, EXIT_EXTERNAL_ERROR, totals={}))
            return EXIT_EXTERNAL_ERROR
    return -1  # caller should re-raise


def _tally_totals(
    audit_dir: Path,
    run_metadata: RunMetadata,
    phase_state: Any,
) -> dict[str, int]:
    """Build totals dict from RunMetadata + findings.partial.jsonl."""
    totals: dict[str, int] = {
        "files_audited": int(getattr(run_metadata, "files_audited", 0) or 0),
        "files_errored": int(getattr(run_metadata, "files_errored", 0) or 0),
        "files_skipped": int(getattr(run_metadata, "files_skipped", 0) or 0),
        "high": 0,
        "medium": 0,
        "low": 0,
        "healthy": 0,
    }
    partial = audit_dir / "findings.partial.jsonl"
    if partial.is_file():
        import json as _json

        for raw in partial.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = _json.loads(raw)
            except _json.JSONDecodeError:
                continue
            p = rec.get("priority", "")
            if p in totals:
                totals[p] += 1
    totals["finding_count"] = totals["high"] + totals["medium"] + totals["low"]
    if isinstance(phase_state, dict):
        totals["finding_count"] = int(
            phase_state.get("finding_count", totals["finding_count"])
        )
    return totals


async def run_audit(
    *,
    repo: Path,
    config: SenexConfig,
    lens: Lens,
    bus: EventBus,
    command_bus: CommandBus,
    config_path: Path,
    output_root: Path | None = None,
    resume: bool = False,
    allow_mixed_resume: bool = False,
) -> int:
    """Drive the audit pipeline; return the OS exit code.

    Returns:
        0 — full success.
        1 — partial success (some files skipped/errored, or AggregateFailed).
        2 — config / setup error (incl. ResumeIncompatible).
        3 — external dependency error.
        130 — KeyboardInterrupt.

    The Lifecycle async-CM ensures release runs on EVERY exit path.
    """
    output_root = output_root or Path(config.output.root)
    redactor = SecretRedactor()

    run_id_full, run_id_short, audit_dir = _init_run_identity(repo, output_root)
    registry, enabled_tools, tool_pack_hash = _register_default_tools_and_pack(lens, config)

    config_hash = _stable_hash(repr(config.model_dump()))
    prompt_hash = lens.fingerprint
    lens_version = lens.version

    cp_result = await _resolve_or_resume_checkpoint(
        audit_dir=audit_dir,
        run_id_full=run_id_full,
        resume=resume,
        allow_mixed_resume=allow_mixed_resume,
        config_hash=config_hash,
        prompt_hash=prompt_hash,
        tool_pack_hash=tool_pack_hash,
        lens_version=lens_version,
        bus=bus,
    )
    if cp_result[0] is None:
        return EXIT_CONFIG_ERROR
    cp, run_id_full, run_id_short = cp_result

    checkpoint_fingerprint = (
        cp.data.get("model_fingerprint")
        if cp.data.get("model_fingerprint") not in ("", "pending", None)
        else None
    )

    disk_writer, _disk_writer_handle = _wire_disk_writer(audit_dir, bus)

    started_at = _now()
    exit_code = EXIT_SUCCESS
    partial_writer: FindingsPartialWriter | None = None
    try:
        async with lifecycle_acquire_or_resume(
            config=config,
            run_id=run_id_full,
            bus=bus,
            redactor=redactor,
            resume=resume,
            checkpoint_fingerprint=checkpoint_fingerprint,
        ) as (model_info, _loaded_by_us):
            exit_code, partial_writer = await _execute_lifecycle_body(
                model_info=model_info,
                cp=cp,
                audit_dir=audit_dir,
                repo=repo,
                output_root=output_root,
                run_id_full=run_id_full,
                run_id_short=run_id_short,
                config=config,
                config_path=config_path,
                lens=lens,
                bus=bus,
                command_bus=command_bus,
                redactor=redactor,
                registry=registry,
                enabled_tools=enabled_tools,
                config_hash=config_hash,
                prompt_hash=prompt_hash,
                tool_pack_hash=tool_pack_hash,
                started_at=started_at,
            )

    except KeyboardInterrupt:
        log.info("interrupted by user")
        await bus.publish(_runcomplete(run_id_full, EXIT_INTERRUPTED, totals={}))
        return EXIT_INTERRUPTED
    except BaseException as exc:
        handled = await _handle_phase_exception(exc, run_id_full, bus)
        if handled >= 0:
            return handled
        raise
    finally:
        if partial_writer is not None:
            partial_writer.close()
        try:
            _disk_writer_handle.unsubscribe()
        except Exception as exc:  # noqa: BLE001
            log.warning("disk_writer unsubscribe: %s", exc)
        try:
            await disk_writer.shutdown()
        except Exception as exc:  # noqa: BLE001
            log.warning("disk_writer shutdown: %s", exc)

    return exit_code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _events_BaseEvent() -> type:
    """Return the ``BaseEvent`` type so we can subscribe to all events.

    Local helper to avoid leaking the ``BaseEvent`` import at module top
    (the import-boundary test forbids ``senex.tui`` here, but ``events`` is
    fine; this helper just keeps the wiring expressive at the call site).
    """
    from senex.events import BaseEvent

    return BaseEvent


def _disk_writer_dispatch(
    sub: DiskWriterSubscriber,
) -> Callable[[BaseEvent], Coroutine[Any, Any, None]]:
    """Build a callback that forwards events to the subscriber.

    Errors during disk write are logged but never propagated — they are
    persisted into ``events.jsonl`` only for read-side replay (M9). A bad
    write should not abort the run (vs. e.g. ``ENOSPC`` which the audit's
    own per-file artifact writes will surface as ``DiskFatalError``).
    """

    async def _forward(event: BaseEvent) -> None:
        try:
            await sub.consume(event)
        except Exception as exc:  # noqa: BLE001 — never propagate to bus.
            log.error("disk_writer consume failed: %s", exc)

    return _forward


def _runcomplete(run_id: str, exit_status: int, totals: dict[str, int]) -> RunComplete:
    return RunComplete(
        ts=_now(),
        run_id=run_id,
        duration_seconds=0.0,
        totals=totals,
        exit_status=exit_status,
    )


def _stable_hash(s: str) -> str:
    import hashlib

    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _patch_checkpoint_fingerprint(audit_dir: Path, fingerprint: str) -> None:
    """Update the model_fingerprint field on an existing checkpoint."""
    import json

    path = audit_dir / "checkpoint.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["model_fingerprint"] = fingerprint
    body = json.dumps(data, indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def _snapshot_config(config: SenexConfig, audit_dir: Path) -> None:
    """Snapshot the resolved config to TOML for TOCTOU defense (§SEC-9).

    pydantic ``model_dump`` emits ``None`` for unset Optionals; tomli_w
    can't serialize ``None``, so we recursively strip them first.
    """
    import tomli_w

    def _strip_none(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _strip_none(v) for k, v in obj.items() if v is not None}
        if isinstance(obj, list):
            return [_strip_none(item) for item in obj]
        return obj

    target = audit_dir / "config.snapshot.toml"
    cleaned = _strip_none(config.model_dump())
    body = tomli_w.dumps(cleaned)
    write_text_atomic(target, body)


class _NoopGraphProvider:
    """Fallback graph provider when ``npx gitnexus`` is unavailable."""

    async def fetch(self, file_relpath: str) -> Any:
        from senex.graph_awareness import GraphContext

        return GraphContext(
            cluster=None,
            cluster_summary=None,
            public_symbols=[],
            callers_d1_count={},
            top_processes=[],
            available=False,
            raw_text_block="[graph context unavailable]",
        )


__all__ = [
    "EXIT_CONFIG_ERROR",
    "EXIT_EXTERNAL_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_PARTIAL_SUCCESS",
    "EXIT_SUCCESS",
    "compute_audit_dir",
    "lifecycle_acquire_or_resume",
    "run_audit",
]
