"""Tests for MetricsCollectorSubscriber (M9 Task 9.3)."""
from __future__ import annotations

import builtins
from datetime import datetime, UTC
from unittest.mock import patch

import pytest

from senex.events import (
    CompactionComplete,
    CompactionTriggered,
    FileComplete,
    OutputComplete,
    OutputTick,
    ThinkingComplete,
    ThinkingTick,
    ToolCall,
)
from senex.subscribers.metrics import (
    Metrics,
    MetricsCollectorSubscriber,
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_metrics_starts_zeroed() -> None:
    sub = MetricsCollectorSubscriber()
    m = sub.metrics
    assert m.files_done == 0
    assert m.findings_by_priority == {"high": 0, "medium": 0, "low": 0, "healthy": 0}
    assert m.tool_calls == 0
    assert m.compactions == 0
    assert m.total_thinking_seconds == 0.0
    assert m.total_output_seconds == 0.0


@pytest.mark.asyncio
async def test_metrics_drop_policy_is_drop_oldest() -> None:
    sub = MetricsCollectorSubscriber()
    assert sub.drop_policy == "drop_oldest"
    assert sub.name == "metrics"
    assert sub.queue_capacity == 1024


@pytest.mark.asyncio
async def test_metrics_increments_files_done_on_file_complete() -> None:
    sub = MetricsCollectorSubscriber()
    for i in range(5):
        await sub.consume(
            FileComplete(
                ts=_now(),
                run_id="r1",
                path=f"src/f{i}.py",
                finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 1},
            )
        )
    assert sub.metrics.files_done == 5


@pytest.mark.asyncio
async def test_metrics_aggregates_findings_by_priority() -> None:
    sub = MetricsCollectorSubscriber()
    fc = FileComplete(
        ts=_now(),
        run_id="r1",
        path="x",
        finding_counts={"high": 2, "medium": 1, "low": 3, "healthy": 0},
    )
    await sub.consume(fc)
    await sub.consume(fc)
    assert sub.metrics.findings_by_priority == {
        "high": 4,
        "medium": 2,
        "low": 6,
        "healthy": 0,
    }


@pytest.mark.asyncio
async def test_metrics_increments_tool_calls() -> None:
    sub = MetricsCollectorSubscriber()
    for i in range(7):
        await sub.consume(
            ToolCall(
                ts=_now(),
                run_id="r1",
                path="x",
                tool_name="grep",
                tool_input="{}",
                call_id=f"c{i}",
            )
        )
    assert sub.metrics.tool_calls == 7


@pytest.mark.asyncio
async def test_metrics_increments_compactions_on_compaction_complete_only() -> None:
    sub = MetricsCollectorSubscriber()
    await sub.consume(
        CompactionTriggered(
            ts=_now(),
            run_id="r1",
            path="x",
            message_tokens_before=1000,
            threshold=800,
        )
    )
    assert sub.metrics.compactions == 0
    await sub.consume(
        CompactionComplete(
            ts=_now(),
            run_id="r1",
            path="x",
            message_tokens_after=500,
            kept_findings=2,
        )
    )
    assert sub.metrics.compactions == 1


@pytest.mark.asyncio
async def test_metrics_sums_thinking_seconds_from_thinking_complete() -> None:
    sub = MetricsCollectorSubscriber()
    for _ in range(2):
        await sub.consume(
            ThinkingComplete(
                ts=_now(),
                run_id="r1",
                path="x",
                total_thinking_tokens=100,
                latency_ms=1500,
            )
        )
    assert sub.metrics.total_thinking_seconds == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_metrics_sums_output_seconds_from_output_complete() -> None:
    sub = MetricsCollectorSubscriber()
    await sub.consume(
        OutputComplete(
            ts=_now(),
            run_id="r1",
            path="x",
            total_output_tokens=200,
            latency_ms=2500,
        )
    )
    assert sub.metrics.total_output_seconds == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_metrics_no_io_during_consume() -> None:
    sub = MetricsCollectorSubscriber()
    real_open = builtins.open
    with patch.object(builtins, "open", side_effect=AssertionError("no I/O allowed")):
        for _ in range(50):
            await sub.consume(
                FileComplete(
                    ts=_now(),
                    run_id="r1",
                    path="x",
                    finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 1},
                )
            )
            await sub.consume(
                ThinkingTick(
                    ts=_now(),
                    run_id="r1",
                    path="x",
                    tokens_so_far=1,
                    delta_since_last_tick=1,
                )
            )
    builtins.open = real_open  # restore


@pytest.mark.asyncio
async def test_metrics_property_returns_frozen_copy() -> None:
    sub = MetricsCollectorSubscriber()
    m1 = sub.metrics
    await sub.consume(
        FileComplete(
            ts=_now(),
            run_id="r1",
            path="x",
            finding_counts={"high": 1, "medium": 0, "low": 0, "healthy": 0},
        )
    )
    m2 = sub.metrics
    # m1 captures pre-consume state; mutation does not bleed back.
    assert m1.files_done == 0
    assert m2.files_done == 1


@pytest.mark.asyncio
async def test_metrics_metrics_model_extra_forbid() -> None:
    with pytest.raises(Exception):
        Metrics.model_validate(
            {
                "files_done": 1,
                "findings_by_priority": {"high": 0, "medium": 0, "low": 0, "healthy": 0},
                "tool_calls": 0,
                "compactions": 0,
                "total_thinking_seconds": 0.0,
                "total_output_seconds": 0.0,
                "extra_unknown_field": True,
            }
        )


@pytest.mark.asyncio
async def test_metrics_ignores_ticks_for_counters() -> None:
    sub = MetricsCollectorSubscriber()
    await sub.consume(
        ThinkingTick(
            ts=_now(),
            run_id="r1",
            path="x",
            tokens_so_far=1,
            delta_since_last_tick=1,
        )
    )
    await sub.consume(
        OutputTick(
            ts=_now(),
            run_id="r1",
            path="x",
            tokens_so_far=1,
            delta_since_last_tick=1,
        )
    )
    m = sub.metrics
    assert m.files_done == 0
    assert m.tool_calls == 0
