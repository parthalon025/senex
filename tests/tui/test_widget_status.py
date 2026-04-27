"""Tests for StatusStripWidget (M9 Task 9.6e)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from senex.events import (
    CompactionComplete,
    FileComplete,
    OutputTick,
    ThinkingTick,
    ToolCall,
)
from senex.subscribers.metrics import MetricsCollectorSubscriber
from senex.tui.widgets.status import StatusStripWidget


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class _Host(App[None]):
    def __init__(self, metrics: MetricsCollectorSubscriber) -> None:
        super().__init__()
        self.widget = StatusStripWidget(metrics, id="status")

    def compose(self) -> ComposeResult:
        yield self.widget


def _label(host: _Host) -> str:
    return str(host.widget.query_one("#status_label", Label).render())


@pytest.mark.asyncio
async def test_status_strip_initial_zeros() -> None:
    metrics = MetricsCollectorSubscriber()
    host = _Host(metrics)
    async with host.run_test() as pilot:
        await pilot.pause()
        text = _label(host)
        assert "HIGH 0" in text
        assert "MEDIUM 0" in text
        assert "LOW 0" in text
        assert "HEALTHY 0" in text
        assert "TOOLS 0" in text
        assert "COMPACTIONS 0" in text


@pytest.mark.asyncio
async def test_status_strip_updates_on_file_complete() -> None:
    metrics = MetricsCollectorSubscriber()
    host = _Host(metrics)
    async with host.run_test() as pilot:
        await pilot.pause()
        fc = FileComplete(
            ts=_now(),
            run_id="r1",
            path="x",
            finding_counts={"high": 2, "medium": 1, "low": 3, "healthy": 0},
        )
        await metrics.consume(fc)
        host.widget.handle_audit_event(fc)
        await pilot.pause()
        text = _label(host)
        assert "HIGH 2" in text
        assert "MEDIUM 1" in text
        assert "LOW 3" in text


@pytest.mark.asyncio
async def test_status_strip_does_not_render_on_tick() -> None:
    metrics = MetricsCollectorSubscriber()
    host = _Host(metrics)
    async with host.run_test() as pilot:
        await pilot.pause()
        original_render = host.widget.render_calls
        for i in range(50):
            host.widget.handle_audit_event(
                ThinkingTick(
                    ts=_now(),
                    run_id="r1",
                    path="x",
                    tokens_so_far=i,
                    delta_since_last_tick=1,
                )
            )
            host.widget.handle_audit_event(
                OutputTick(
                    ts=_now(),
                    run_id="r1",
                    path="x",
                    tokens_so_far=i,
                    delta_since_last_tick=1,
                )
            )
        await pilot.pause()
        assert host.widget.render_calls == original_render


@pytest.mark.asyncio
async def test_status_strip_handles_tool_calls_counter() -> None:
    metrics = MetricsCollectorSubscriber()
    host = _Host(metrics)
    async with host.run_test() as pilot:
        await pilot.pause()
        for i in range(5):
            tc = ToolCall(
                ts=_now(),
                run_id="r1",
                path="x",
                tool_name="grep",
                tool_input="{}",
                call_id=f"c{i}",
            )
            await metrics.consume(tc)
            host.widget.handle_audit_event(tc)
        await pilot.pause()
        assert "TOOLS 5" in _label(host)


@pytest.mark.asyncio
async def test_status_strip_handles_compactions_counter() -> None:
    metrics = MetricsCollectorSubscriber()
    host = _Host(metrics)
    async with host.run_test() as pilot:
        await pilot.pause()
        for _ in range(2):
            cc = CompactionComplete(
                ts=_now(),
                run_id="r1",
                path="x",
                message_tokens_after=500,
                kept_findings=2,
            )
            await metrics.consume(cc)
            host.widget.handle_audit_event(cc)
        await pilot.pause()
        assert "COMPACTIONS 2" in _label(host)
