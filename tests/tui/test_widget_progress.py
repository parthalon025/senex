"""Tests for ProgressWidget (M9 Task 9.6a)."""
from __future__ import annotations

from datetime import datetime, UTC

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from senex.events import FileComplete, FileStart
from senex.tui.widgets.progress import ProgressWidget


def _now() -> datetime:
    return datetime.now(tz=UTC)


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.widget = ProgressWidget(id="progress")

    def compose(self) -> ComposeResult:
        yield self.widget


def _label(host: _Host) -> str:
    return str(host.widget.query_one("#progress_label", Label).render())


@pytest.mark.asyncio
async def test_progress_initial_render_shows_zero() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        assert "0/0" in _label(host)


@pytest.mark.asyncio
async def test_progress_updates_on_file_start_total() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            FileStart(ts=_now(), run_id="r1", path="x", idx=1, total=87)
        )
        await pilot.pause()
        assert "1/87" in _label(host)
        assert "ETA --" in _label(host)


@pytest.mark.asyncio
async def test_progress_eta_after_first_complete() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget._now = lambda: 0.0  # type: ignore[method-assign]
        host.widget.handle_audit_event(
            FileStart(ts=_now(), run_id="r1", path="a", idx=1, total=10)
        )
        host.widget._now = lambda: 10.0  # type: ignore[method-assign]
        host.widget.handle_audit_event(
            FileComplete(
                ts=_now(),
                run_id="r1",
                path="a",
                finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 1},
            )
        )
        host.widget.handle_audit_event(
            FileStart(ts=_now(), run_id="r1", path="b", idx=2, total=10)
        )
        await pilot.pause()
        # 9 remaining files × 10s avg = 90s
        text = _label(host)
        assert "ETA" in text
        assert "1m30s" in text


@pytest.mark.asyncio
async def test_progress_eta_uses_rolling_window_n10() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        # Feed 11 file completes; durations 100, 1, 1, ..., 1.
        # The rolling window of 10 should drop the 100s outlier from the average.
        durations = [100.0] + [1.0] * 10
        t = 0.0
        for i, dur in enumerate(durations, start=1):
            host.widget._now = lambda v=t: v  # type: ignore[method-assign]
            host.widget.handle_audit_event(
                FileStart(ts=_now(), run_id="r1", path=f"f{i}", idx=i, total=20)
            )
            t += dur
            host.widget._now = lambda v=t: v  # type: ignore[method-assign]
            host.widget.handle_audit_event(
                FileComplete(
                    ts=_now(),
                    run_id="r1",
                    path=f"f{i}",
                    finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 1},
                )
            )
        # Avg should now be 1.0s (last 10 durations); ETA for 9 remaining = 9s.
        host.widget.handle_audit_event(
            FileStart(ts=_now(), run_id="r1", path="next", idx=12, total=20)
        )
        await pilot.pause()
        assert "ETA 9s" in _label(host)


@pytest.mark.asyncio
async def test_progress_handles_zero_total() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        # idx=0/total=0 must not raise ZeroDivisionError.
        host.widget.handle_audit_event(
            FileStart(ts=_now(), run_id="r1", path="x", idx=0, total=0)
        )
        await pilot.pause()
        text = _label(host)
        assert "0/0" in text
