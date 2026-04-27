"""senex.cli_aggregate — ``cmd_aggregate`` (M10 Task 10.4).

Re-runs Phase 5 (``AggregatePhase``) against an existing audit directory.
Useful when the original run crashed mid-aggregation but everything before
that completed (per-file `<file>.md` are present, ``findings.partial.jsonl``
is intact).

Validates the audit_dir structure BEFORE invoking the phase:
  * ``checkpoint.json`` must exist (we read run_id and hashes from it)
  * ``findings.partial.jsonl`` must exist (the aggregator's input)

Returns:
    0 — aggregation succeeded.
    1 — ``AggregateFailed`` (per-file artifacts intact; user can inspect).
    2 — required files missing (operator error).
    130 — KeyboardInterrupt.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from senex.checkpoint import Checkpoint, CheckpointCorrupt, CheckpointSchemaError
from senex.config import (
    SenexConfig,
    UnknownConfigKey,
    load_config,
)
from senex.events import CommandBus, EventBus
from senex.lens import Lens, LensNotFound, LensValidationError
from senex.phases.aggregate import AggregatePhase
from senex.phases.base import AggregateFailed
from senex.render_models import RunMetadata


log = logging.getLogger(__name__)


_EXIT_OK = 0
_EXIT_PARTIAL = 1
_EXIT_CONFIG = 2
_EXIT_INTERRUPT = 130


def cmd_aggregate(args: argparse.Namespace) -> int:
    """``senex aggregate <audit-dir>`` dispatcher. Returns exit code."""
    audit_dir = Path(args.audit_dir) if args.audit_dir else None
    if audit_dir is None:
        sys.stderr.write("senex aggregate: <audit-dir> is required\n")
        return _EXIT_CONFIG
    if not audit_dir.exists():
        sys.stderr.write(f"senex aggregate: audit dir does not exist: {audit_dir}\n")
        return _EXIT_CONFIG
    if not audit_dir.is_dir():
        sys.stderr.write(f"senex aggregate: not a directory: {audit_dir}\n")
        return _EXIT_CONFIG

    # Required files.
    checkpoint_path = audit_dir / "checkpoint.json"
    partial_path = audit_dir / "findings.partial.jsonl"
    if not checkpoint_path.exists():
        sys.stderr.write(
            f"senex aggregate: missing required file: {checkpoint_path} "
            f"(was Phase 1 ever entered?)\n"
        )
        return _EXIT_CONFIG
    if not partial_path.exists():
        sys.stderr.write(
            f"senex aggregate: missing required file: {partial_path} "
            f"(no per-file findings to aggregate)\n"
        )
        return _EXIT_CONFIG

    # Load checkpoint.
    try:
        cp = Checkpoint.load(audit_dir)
    except (CheckpointCorrupt, CheckpointSchemaError) as exc:
        sys.stderr.write(f"senex aggregate: checkpoint invalid: {exc}\n")
        return _EXIT_CONFIG

    # Build a RunMetadata stub from checkpoint + audit_dir naming.
    run_metadata = _reconstruct_run_metadata(audit_dir, cp.data)

    # Build AggregatePhase + bus.
    phase = AggregatePhase(
        audit_dir=audit_dir,
        run_metadata=run_metadata,
        run_id=cp.data.get("run_id", ""),
    )

    # Try to load the snapshot config + lens for the lens/config args
    # AggregatePhase.do_work expects. The phase ignores them per its docstring,
    # but we still need to pass *something*.
    config = _try_load_snapshot_config(audit_dir)
    lens = _try_load_lens(config)

    bus = EventBus()
    command_bus = CommandBus()

    async def _run() -> dict[str, Any]:
        state = await phase.read_state(audit_dir)
        result = await phase.do_work(state, lens, config, bus, command_bus)
        await phase.write_state(audit_dir, result)
        return result

    try:
        asyncio.run(_run())
    except AggregateFailed as exc:
        sys.stderr.write(f"senex aggregate: phase failed: {exc.message}\n")
        return _EXIT_PARTIAL
    except KeyboardInterrupt:
        return _EXIT_INTERRUPT
    return _EXIT_OK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reconstruct_run_metadata(
    audit_dir: Path, checkpoint: dict[str, Any]
) -> RunMetadata:
    """Build a ``RunMetadata`` from the checkpoint + audit_dir name.

    Fields not in the checkpoint default to empty strings or zeros; the
    aggregator only reads a subset for the ``run`` block in ``findings.json``.
    """
    run_id = str(checkpoint.get("run_id", ""))
    run_id_short = run_id[:8] if run_id else ""
    # Audit dir name is "<DATE>-<run_id_short>" — recover the date.
    parts = audit_dir.name.split("-")
    date = "-".join(parts[:3]) if len(parts) >= 4 else ""
    repo_name = audit_dir.parent.name if audit_dir.parent.name else ""

    return RunMetadata(
        repo=repo_name,
        run_id=run_id,
        run_id_short=run_id_short,
        audit_dir=str(audit_dir),
        model="",
        model_fingerprint=str(checkpoint.get("model_fingerprint", "")),
        lens="",
        lens_version=str(checkpoint.get("lens_version", "")),
        started_at="",
        duration_seconds=0.0,
        config_hash=str(checkpoint.get("config_hash", "")),
        prompt_hash=str(checkpoint.get("prompt_hash", "")),
        tool_pack_hash=str(checkpoint.get("tool_pack_hash", "")),
        date=date,
    )


def _try_load_snapshot_config(audit_dir: Path) -> SenexConfig:
    """Return the audit's snapshot config; empty SenexConfig on failure."""
    snap = audit_dir / "config.snapshot.toml"
    if snap.exists():
        try:
            return load_config(snap)
        except (UnknownConfigKey, ValueError, FileNotFoundError) as exc:
            log.warning("could not load snapshot config: %s", exc)
    return SenexConfig()


def _try_load_lens(config: SenexConfig) -> Lens:
    """Best-effort lens load; falls back to a minimal stub."""
    try:
        return Lens.load(config.lens.name)
    except (LensNotFound, LensValidationError):
        # Fallback: AggregatePhase doesn't read the lens (it operates on
        # persisted artifacts), but the protocol signature requires one.
        # Return a synthetic lens that won't be queried.
        return Lens(  # type: ignore[call-arg]
            name="(stub)",
            version="",
            description="",
            system_prompt_path=Path(""),
            response_schema_path=Path(""),
            crosscut_prompt_path=Path(""),
            crosscut_schema_path=Path(""),
            renderer_template_path=Path(""),
            category_taxonomy=("correctness",),
            tools=[],
            fingerprint="",
        )


__all__ = ["cmd_aggregate"]
