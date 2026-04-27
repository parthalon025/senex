"""Tests for TuiSubscriber (M9 Task 9.7)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from senex.events import (
    BaseEvent,
    FileComplete,
    FileError,
    FileStart,
    RunComplete,
    ThinkingTick,
)
from senex.subscribers.tui_subscriber import TuiSubscriber


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class _MockWidget:
    def __init__(self) -> None:
        self.events: list[BaseEvent] = []

    def handle_audit_event(self, event: BaseEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_tui_subscriber_drop_policy_is_drop_token_only() -> None:
    sub = TuiSubscriber()
    assert sub.drop_policy == "drop_token_only"
    assert sub.name == "tui"
    assert sub.queue_capacity == 1024


@pytest.mark.asyncio
async def test_tui_subscriber_dispatches_to_widgets_on_phase_events() -> None:
    sub = TuiSubscriber()
    widgets = [_MockWidget() for _ in range(5)]
    for w in widgets:
        sub.register_widget(w)
    await sub.start()
    try:
        await sub.consume(
            FileStart(ts=_now(), run_id="r1", path="x", idx=1, total=10)
        )
        await sub.flush_now()
        for w in widgets:
            assert len(w.events) == 1
            assert isinstance(w.events[0], FileStart)
    finally:
        await sub.shutdown()


@pytest.mark.asyncio
async def test_tui_subscriber_drops_thinking_ticks_under_pressure() -> None:
    # Force a tiny capacity so 100 ticks oversaturates the buffer.
    sub = TuiSubscriber(rate_limit_hz=1.0)
    sub.queue_capacity = 10
    widget = _MockWidget()
    sub.register_widget(widget)
    await sub.start()
    try:
        for i in range(100):
            await sub.consume(
                ThinkingTick(
                    ts=_now(),
                    run_id="r1",
                    path="x",
                    tokens_so_far=i,
                    delta_since_last_tick=1,
                )
            )
        # Excess ticks are dropped (queue capacity exhausted).
        assert sub.tick_drop_count > 0
    finally:
        await sub.shutdown()


@pytest.mark.asyncio
async def test_tui_subscriber_never_drops_run_complete() -> None:
    sub = TuiSubscriber()
    widget = _MockWidget()
    sub.register_widget(widget)
    await sub.start()
    try:
        # Fill queue with ticks first.
        for i in range(1024):
            try:
                await sub.consume(
                    ThinkingTick(
                        ts=_now(),
                        run_id="r1",
                        path="x",
                        tokens_so_far=i,
                        delta_since_last_tick=1,
                    )
                )
            except Exception:
                pass
        rc = RunComplete(
            ts=_now(),
            run_id="r1",
            duration_seconds=1.0,
            totals={},
            exit_status=0,
        )
        await sub.consume(rc)
        await sub.flush_now()
        assert any(isinstance(e, RunComplete) for e in widget.events)
    finally:
        await sub.shutdown()


@pytest.mark.asyncio
async def test_tui_subscriber_never_drops_file_complete() -> None:
    sub = TuiSubscriber()
    widget = _MockWidget()
    sub.register_widget(widget)
    await sub.start()
    try:
        fc = FileComplete(
            ts=_now(),
            run_id="r1",
            path="x",
            finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 1},
        )
        await sub.consume(fc)
        await sub.flush_now()
        assert any(isinstance(e, FileComplete) for e in widget.events)
    finally:
        await sub.shutdown()


@pytest.mark.asyncio
async def test_tui_subscriber_never_drops_file_error() -> None:
    sub = TuiSubscriber()
    widget = _MockWidget()
    sub.register_widget(widget)
    await sub.start()
    try:
        fe = FileError(
            ts=_now(),
            run_id="r1",
            path="x",
            phase="t",
            error_kind="lms",
            error_message="m",
        )
        await sub.consume(fe)
        await sub.flush_now()
        assert any(isinstance(e, FileError) for e in widget.events)
    finally:
        await sub.shutdown()


@pytest.mark.asyncio
async def test_tui_subscriber_render_rate_limit() -> None:
    """100 ticks within 100ms produce <= ~10 widget update batches."""
    sub = TuiSubscriber(rate_limit_hz=10.0)
    widget = _MockWidget()
    sub.register_widget(widget)
    await sub.start()
    try:
        for i in range(100):
            await sub.consume(
                ThinkingTick(
                    ts=_now(),
                    run_id="r1",
                    path="x",
                    tokens_so_far=i,
                    delta_since_last_tick=1,
                )
            )
        # Allow rate-limit pump to run for 100ms.
        await asyncio.sleep(0.10)
        assert sub.batch_count <= 2  # ~10Hz; one batch per 100ms.
    finally:
        await sub.shutdown()


@pytest.mark.asyncio
async def test_tui_subscriber_shutdown_flushes_pending() -> None:
    sub = TuiSubscriber()
    widget = _MockWidget()
    sub.register_widget(widget)
    await sub.start()
    # Buffer 5 events without explicit flush.
    for i in range(5):
        await sub.consume(
            FileStart(ts=_now(), run_id="r1", path=f"x{i}", idx=i, total=5)
        )
    await sub.shutdown()
    # All 5 events processed.
    assert len(widget.events) == 5


@pytest.mark.asyncio
async def test_tui_subscriber_widget_render_error_logged_not_raised() -> None:
    sub = TuiSubscriber()

    class CrashWidget:
        def handle_audit_event(self, event: BaseEvent) -> None:
            raise RuntimeError("widget boom")

    sub.register_widget(CrashWidget())
    await sub.start()
    try:
        # Must not raise — TuiSubscriber catches widget exceptions.
        await sub.consume(
            FileStart(ts=_now(), run_id="r1", path="x", idx=1, total=1)
        )
        await sub.flush_now()
    finally:
        await sub.shutdown()


@pytest.mark.asyncio
async def test_tui_subscriber_propagates_cancelled_error() -> None:
    sub = TuiSubscriber()
    await sub.start()
    pump_task = sub._pump_task
    assert pump_task is not None
    pump_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pump_task
