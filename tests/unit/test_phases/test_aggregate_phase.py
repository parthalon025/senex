"""``AggregatePhase`` tests (M8 Task 8.6).

AggregatePhase calls Aggregator.run() -> write_handoff() -> render_combined().
On any failure: raises AggregateFailed (exit_code=1, recoverable via
`senex aggregate`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from senex.config import SenexConfig
from senex.events import (
    AggregateComplete,
    AggregateStart,
    BaseEvent,
    CommandBus,
    EventBus,
)
from senex.findings_partial import FindingsPartialWriter
from senex.lens import Lens
from senex.phases import AggregateFailed
from senex.phases.aggregate import AggregatePhase
from senex.render_models import FindingRecord, LocationRecord


def _seed_partial(audit_dir: Path) -> None:
    audit_dir.mkdir(exist_ok=True, parents=True)
    rec = FindingRecord(
        id="f-aaaaaaaaaaaa",
        file="main.py",
        category="Correctness",
        priority="high",
        title="Off-by-one",
        issue="returns n+1",
        why="slice +1",
        fix="xs[:n]",
        confidence="medium",
        location=LocationRecord(line_start=4, symbol="first_n"),
        report_path="main.py.md",
        suppressed=False,
        prompt_hash="p",
        config_hash="c",
        model_fingerprint="fp",
        lens_version="1.0.0",
    )
    with FindingsPartialWriter(audit_dir) as pw:
        pw.append(rec)


def _make_run_metadata(audit_dir: Path) -> Any:
    from senex.render_models import RunMetadata

    return RunMetadata(
        repo="tiny_python",
        run_id="01JZRUNDTEST1234567890ABCD",
        run_id_short="01JZRUND",
        audit_dir=str(audit_dir),
        model="google/gemma-4-26b-a4b",
        model_fingerprint="fp",
        lens="correctness",
        lens_version="1.0.0",
        started_at="2026-04-26T00:00:00+00:00",
        duration_seconds=10.0,
        config_hash="c",
        prompt_hash="p",
        date="2026-04-26",
    )


@pytest.mark.asyncio
async def test_aggregate_writes_all_artifacts(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    _seed_partial(audit_dir)

    bus = EventBus()
    captured: list[BaseEvent] = []

    async def cb(evt: BaseEvent) -> None:
        captured.append(evt)

    bus.subscribe_local("test", BaseEvent, cb)
    cb_bus = CommandBus()

    phase = AggregatePhase(
        audit_dir=audit_dir,
        run_metadata=_make_run_metadata(audit_dir),
        run_id="01JZRUNDTEST1234567890ABCD",
    )
    state = await phase.do_work(
        {"themes": []},
        Lens.load("correctness"),
        SenexConfig(),
        bus,
        cb_bus,
    )
    assert state["finding_count"] == 1
    assert (audit_dir / "findings.json").exists()
    assert (audit_dir / "claude-handoff.md").exists()
    assert (audit_dir / "combined.md").exists()
    starts = [e for e in captured if isinstance(e, AggregateStart)]
    completes = [e for e in captured if isinstance(e, AggregateComplete)]
    assert len(starts) == 1
    assert len(completes) == 1


@pytest.mark.asyncio
async def test_aggregate_failure_raises_aggregate_failed(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    _seed_partial(audit_dir)

    phase = AggregatePhase(
        audit_dir=audit_dir,
        run_metadata=_make_run_metadata(audit_dir),
        run_id="01JZRUNDTEST1234567890ABCD",
    )
    bus = EventBus()
    cb_bus = CommandBus()

    # Force the aggregator to crash.
    with patch("senex.phases.aggregate.Aggregator") as agg_class:
        agg_instance = agg_class.return_value

        async def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("aggregator boom")

        agg_instance.run = boom
        with pytest.raises(AggregateFailed) as excinfo:
            await phase.do_work(
                {"themes": []},
                Lens.load("correctness"),
                SenexConfig(),
                bus,
                cb_bus,
            )
        assert excinfo.value.exit_code == 1


@pytest.mark.asyncio
async def test_aggregate_no_partial_returns_empty(tmp_path: Path) -> None:
    """Empty partial -> findings.json is empty, but the run still finalizes."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    bus = EventBus()
    cb_bus = CommandBus()
    phase = AggregatePhase(
        audit_dir=audit_dir,
        run_metadata=_make_run_metadata(audit_dir),
        run_id="01JZRUNDTEST1234567890ABCD",
    )
    state = await phase.do_work(
        {"themes": []},
        Lens.load("correctness"),
        SenexConfig(),
        bus,
        cb_bus,
    )
    assert state["finding_count"] == 0
    assert (audit_dir / "findings.json").exists()


@pytest.mark.asyncio
async def test_aggregate_atomic_no_partial_combined_on_crash(tmp_path: Path) -> None:
    """Step 8.6.3: atomic-write semantics — no partial combined.md on crash."""
    audit_dir = tmp_path / "audit"
    _seed_partial(audit_dir)
    phase = AggregatePhase(
        audit_dir=audit_dir,
        run_metadata=_make_run_metadata(audit_dir),
        run_id="01JZRUNDTEST1234567890ABCD",
    )
    bus = EventBus()
    cb_bus = CommandBus()

    # Force the renderer to crash on render_combined.
    from senex.renderer import Renderer

    original = Renderer.render_combined

    def boom(self: Any, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError("render combined boom")

    Renderer.render_combined = boom  # type: ignore[method-assign]
    try:
        with pytest.raises(AggregateFailed):
            await phase.do_work(
                {"themes": []},
                Lens.load("correctness"),
                SenexConfig(),
                bus,
                cb_bus,
            )
    finally:
        Renderer.render_combined = original  # type: ignore[method-assign]

    # No combined.md should be present (only the .tmp may have been created
    # earlier and could remain — but we never publish a partial combined.md).
    combined = audit_dir / "combined.md"
    assert not combined.exists()
