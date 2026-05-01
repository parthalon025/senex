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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

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
from senex.lmstudio_client import LMStudioClient
from senex.lmstudio_lifecycle import (
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
    from senex.events import CommandBus
    from senex.lens import Lens

log = logging.getLogger(__name__)


# Exit code constants (R11 collapse).
EXIT_SUCCESS: int = 0
EXIT_PARTIAL_SUCCESS: int = 1
EXIT_CONFIG_ERROR: int = 2
EXIT_EXTERNAL_ERROR: int = 3
EXIT_INTERRUPTED: int = 130


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def compute_audit_dir(
    repo: Path, output_root: Path, run_id_short: str
) -> Path:
    """``<output_root>/<repo_name>/<DATE>-<run_id_short>/`` per POL-1."""
    date = time.strftime("%Y-%m-%d")
    return output_root / repo.name / f"{date}-{run_id_short}"


@asynccontextmanager
async def lifecycle_acquire_or_resume(
    *,
    config: "SenexConfig",
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
    backend = await LifecycleBackendFactory.select(
        base_url=config.lmstudio.base_url,
        api_key=config.lmstudio.api_key,
        sglang_cfg=config.lmstudio.sglang,
    )
    lifecycle = Lifecycle(
        backend=backend,
        bus=bus,
        config=config.lmstudio.lifecycle,
        redactor=redactor,
    )
    model_id = config.lmstudio.model
    loaded_by_us = False
    info: ModelInfo

    if resume and checkpoint_fingerprint:
        info = await lifecycle.acquire_for_resume(
            model_id=model_id,
            run_id=run_id,
            runlock=RunLock,
            checkpoint_fingerprint=checkpoint_fingerprint,
            allow_mixed=config.lmstudio.lifecycle.allow_mixed,
        )
        # Resume can never own the load (defense-in-depth in lifecycle).
        loaded_by_us = False
    else:
        info, loaded_by_us = await lifecycle.acquire(
            model_id=model_id,
            run_id=run_id,
            runlock=RunLock,
            auto_load=config.lmstudio.lifecycle.auto_load,
        )

    try:
        yield info, loaded_by_us
    finally:
        with contextlib.suppress(Exception):
            await lifecycle.release(
                model_id=model_id,
                run_id=run_id,
                runlock=RunLock,
                auto_unload=config.lmstudio.lifecycle.auto_unload,
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


async def run_audit(
    *,
    repo: Path,
    config: "SenexConfig",
    lens: "Lens",
    bus: EventBus,
    command_bus: "CommandBus",
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

    # ---- Build run identity ----
    run_id_full = str(ULID())
    run_id_short = run_id_full[:8]
    audit_dir = compute_audit_dir(repo, output_root, run_id_short)
    audit_dir.mkdir(parents=True, exist_ok=True)

    # ---- Tool registry: register the v1 6-tool default pack so the lens's
    # declared tools resolve to handlers at runtime. The registry is the
    # single source of truth for what tool calls the model can make per file.
    from senex.tools import register_default_tools  # local import: lazy load

    registry = ToolRegistry()
    register_default_tools(registry)
    enabled_tools = lens.openai_tools_for(
        registry=registry, config_subset=config.lmstudio.tools.enabled_tools
    )
    # Compute tool_pack_hash; with the default pack registered, every lens-declared
    # tool is present and the hash is stable across runs (resume discipline §8.5).
    try:
        tool_pack_hash = compute_tool_pack_hash(enabled_tools, registry)
    except KeyError:
        # Defensive: if a lens declares a tool not in the default pack and not
        # registered elsewhere, fall back to an empty pack hash rather than crash.
        tool_pack_hash = ""

    # ---- Hash bundle (resume discipline §8.5) ----
    config_hash = _stable_hash(repr(config.model_dump()))
    prompt_hash = lens.fingerprint  # snapshotted at lens load.
    lens_version = lens.version

    # ---- Checkpoint: load or create ----
    checkpoint_path = audit_dir / "checkpoint.json"
    if resume and checkpoint_path.exists():
        try:
            cp = Checkpoint.load(audit_dir)
        except (CheckpointCorrupt, CheckpointSchemaError) as exc:
            log.error("checkpoint corrupt: %s", exc)
            await bus.publish(
                _runcomplete(run_id_full, EXIT_CONFIG_ERROR, totals={})
            )
            return EXIT_CONFIG_ERROR
        # Resume hash check.
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
            await bus.publish(
                _runcomplete(run_id_full, err.exit_code, totals={})
            )
            return err.exit_code
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

    checkpoint_fingerprint = (
        cp.data.get("model_fingerprint")
        if cp.data.get("model_fingerprint") not in ("", "pending", None)
        else None
    )

    # ---- Wire DiskWriterSubscriber (events.jsonl persistence, §5.6.1) ----
    # The audit_dir is now known; DiskWriter MUST be wired before any phase
    # publishes (PreflightPhase emits PreflightWarning before any file work).
    # The subscriber is local-fire (synchronous in publish loop) so the
    # "block" policy collapses to a direct await — events.jsonl is lossless.
    disk_writer = DiskWriterSubscriber(audit_dir=audit_dir)
    _disk_writer_handle = bus.subscribe_local(
        "DiskWriter", _events_BaseEvent(), _disk_writer_dispatch(disk_writer)
    )

    # ---- Lifecycle bracket ----
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
            # Update checkpoint with the real fingerprint if we just loaded.
            if cp.data["model_fingerprint"] == "pending":
                # Atomic update via a fresh create call would be wrong;
                # patch the file directly.
                _patch_checkpoint_fingerprint(audit_dir, model_info.fingerprint)

            # Re-read so the in-memory copy matches disk.
            cp = Checkpoint.load(audit_dir)

            # ---- Build LM Studio client + per-file phase deps ----
            client = LMStudioClient(
                config=config.lmstudio, bus=bus, redactor=redactor
            )
            # Pin the client's fingerprint from its own HTTP view of /v1/models,
            # not the lifecycle's `lms ps --json` view. The two surfaces report
            # different quant/digest fields for the same loaded model, so the
            # client must use the source it will actually compare against on
            # every subsequent chat (per-call swap detection §3.7). The
            # lifecycle's fingerprint stays in `model_info.fingerprint` for
            # cross-session resume comparison via Checkpoint.is_compatible.
            try:
                _http_models = await client.list_loaded_models()
                _target = next(
                    (m for m in _http_models if m.id == config.lmstudio.model),
                    None,
                )
                if _target is not None:
                    client._fingerprint_pinned = client.compute_fingerprint(_target)
                else:
                    # Fall back to lifecycle's view if /v1/models doesn't list it.
                    client._fingerprint_pinned = model_info.fingerprint
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                log.warning(
                    "could not derive client-side fingerprint pin (%s); "
                    "using lifecycle fingerprint",
                    exc,
                )
                client._fingerprint_pinned = model_info.fingerprint

            # Per-file artifacts.
            renderer = Renderer(audit_dir, redactor)
            partial_writer = FindingsPartialWriter(audit_dir)

            # Graph awareness provider (best-effort; preflight may have warned).
            graph_provider: Any
            try:
                graph_provider = await GitNexusCLIProvider.preflight(
                    repo_name=repo.name, bus=bus
                )
            except Exception as exc:  # noqa: BLE001 — warn-only path
                log.warning("gitnexus unavailable: %s", exc)
                graph_provider = _NoopGraphProvider()

            # Compactor factory (one per file).
            from senex.compaction import Compactor

            def compactor_factory(file: Path) -> Any:
                return Compactor(
                    client=client,
                    prompt_path=Path(__file__).parent / "prompts" / "compaction.md",
                    schema_path=Path(__file__).parent
                    / "schema"
                    / "compaction_response.schema.json",
                    config=config.lmstudio.compaction,
                    bus=bus,
                    run_id=run_id_full,
                    path=file,
                    model_id=config.lmstudio.model,
                    redactor=redactor,
                )

            # Run metadata snapshot (filled in over the run).
            run_metadata = RunMetadata(
                repo=repo.name,
                run_id=run_id_full,
                run_id_short=run_id_short,
                audit_dir=str(audit_dir),
                model=config.lmstudio.model,
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

            # ---- Phases ----
            preflight_inputs = PreflightInputs(
                config_path=config_path,
                repo_path=repo,
                output_dir=output_root,
                min_disk_bytes=1024 * 1024,
                addendum_path=None,
                anchors_dir=Path(__file__).parent / "prompts",
            )
            phases: list[Phase] = [
                PreflightPhase(
                    client=client, inputs=preflight_inputs, run_id=run_id_full
                ),
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
                CrosscutPhase(
                    audit_dir=audit_dir, client=client, run_id=run_id_full
                ),
                AggregatePhase(
                    audit_dir=audit_dir,
                    run_metadata=run_metadata,
                    run_id=run_id_full,
                ),
            ]

            await bus.publish(
                RunStart(
                    ts=_now(),
                    run_id=run_id_full,
                    repo=str(repo),
                    audit_dir=str(audit_dir),
                    model=config.lmstudio.model,
                    lens=lens.name,
                    lens_version=lens.version,
                    config_hash=config_hash,
                    prompt_hash=prompt_hash,
                    model_fingerprint=model_info.fingerprint,
                    tool_pack_hash=tool_pack_hash,
                    started_at=started_at,
                )
            )

            # ---- Phase loop with checkpoint state machine ----
            phase_state: Any = None
            partial_success = False
            for phase in phases:
                # ---- Command bus poll point #2: between phases ----
                # (The canonical interrupt boundary is per-file in
                # FileAuditPhase. Between-phase polling catches Quit only.)
                # We do not subscribe a queue here to avoid double-consuming
                # commands; QueueFull at the file phase is fine.

                # Skip already-complete phases on resume.
                if cp.data["phase_status"].get(phase.name) == "complete":
                    log.info("resume: skipping completed phase %s", phase.name)
                    continue

                Checkpoint.set_phase(audit_dir, phase.name, "in_progress")  # type: ignore[arg-type]
                read_state = await phase.read_state(audit_dir)
                input_state = read_state if read_state is not None else phase_state
                phase_state = await phase.do_work(
                    input_state, lens, config, bus, command_bus
                )
                await phase.write_state(audit_dir, phase_state)
                Checkpoint.set_phase(audit_dir, phase.name, "complete")  # type: ignore[arg-type]

                # Detect partial success + propagate file counts to RunMetadata
                # so combined.md and findings.json show non-zero totals (M11 bug 3).
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
                    # Re-stamp the aggregate phase that follows so it sees the
                    # updated counts when it serializes RunMetadata into
                    # combined.md / findings.json.
                    for p in phases:
                        if isinstance(p, AggregatePhase):
                            p._run_metadata = run_metadata

            # Snapshot config + prompts to audit_dir (TOCTOU §SEC-9).
            _snapshot_config(config, audit_dir)

            duration = (_now() - started_at).total_seconds()
            totals: dict[str, int] = {
                "files_audited": int(getattr(run_metadata, "files_audited", 0) or 0),
                "files_errored": int(getattr(run_metadata, "files_errored", 0) or 0),
                "files_skipped": int(getattr(run_metadata, "files_skipped", 0) or 0),
                "high": 0,
                "medium": 0,
                "low": 0,
                "healthy": 0,
            }
            # Tally priorities from findings.partial.jsonl (already on disk via
            # FindingsPartialWriter; aggregate phase has run by this point).
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
                totals["finding_count"] = int(phase_state.get("finding_count", totals["finding_count"]))
            await bus.publish(
                RunComplete(
                    ts=_now(),
                    run_id=run_id_full,
                    duration_seconds=duration,
                    totals=totals,
                    exit_status=(
                        EXIT_PARTIAL_SUCCESS if partial_success else EXIT_SUCCESS
                    ),
                )
            )
            exit_code = EXIT_PARTIAL_SUCCESS if partial_success else EXIT_SUCCESS

            await client.aclose()

    except PreflightFailure as exc:
        log.error("preflight failed [%s]: %s", exc.check_name, exc.message)
        await bus.publish(_runcomplete(run_id_full, exc.exit_code, totals={}))
        return exc.exit_code
    except ResumeIncompatible as exc:
        log.error("resume incompatible: %s", exc.message)
        await bus.publish(_runcomplete(run_id_full, exc.exit_code, totals={}))
        return exc.exit_code
    except AggregateFailed as exc:
        log.error("aggregate failed: %s", exc.message)
        await bus.publish(_runcomplete(run_id_full, exc.exit_code, totals={}))
        return exc.exit_code
    except (RenderFatal, PhaseAborted) as exc:
        log.error("run aborted: %s", exc)
        await bus.publish(
            _runcomplete(run_id_full, EXIT_EXTERNAL_ERROR, totals={})
        )
        return EXIT_EXTERNAL_ERROR
    except (LifecycleBackendUnavailable, FingerprintMismatch) as exc:
        log.error("lifecycle: %s", exc)
        await bus.publish(
            _runcomplete(run_id_full, EXIT_EXTERNAL_ERROR, totals={})
        )
        return EXIT_EXTERNAL_ERROR
    except Exception as exc:  # noqa: BLE001 — final defense; surface as external error
        from senex.lmstudio_lifecycle import LifecycleError

        if isinstance(exc, LifecycleError):
            log.error("lifecycle error: %s", exc)
            await bus.publish(
                _runcomplete(run_id_full, EXIT_EXTERNAL_ERROR, totals={})
            )
            return EXIT_EXTERNAL_ERROR
        # Anything else propagates so tests / programmers see real bugs.
        raise
    except KeyboardInterrupt:
        log.info("interrupted by user")
        await bus.publish(
            _runcomplete(run_id_full, EXIT_INTERRUPTED, totals={})
        )
        return EXIT_INTERRUPTED
    finally:
        if partial_writer is not None:
            partial_writer.close()
        # Unsubscribe + close the DiskWriter so the file handle is released.
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


def _disk_writer_dispatch(sub: DiskWriterSubscriber):
    """Build a callback that forwards events to the subscriber.

    Errors during disk write are logged but never propagated — they are
    persisted into ``events.jsonl`` only for read-side replay (M9). A bad
    write should not abort the run (vs. e.g. ``ENOSPC`` which the audit's
    own per-file artifact writes will surface as ``DiskFatalError``).
    """
    from senex.events import BaseEvent

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


def _snapshot_config(config: "SenexConfig", audit_dir: Path) -> None:
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
