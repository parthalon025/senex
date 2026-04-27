"""Tests for CurrentFileWidget (M9 Task 9.6b)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from senex.events import (
    FileComplete,
    FileContextBuilt,
    FileStart,
    OutputComplete,
    OutputStarted,
    OutputTick,
    ThinkingComplete,
    ThinkingStarted,
    ThinkingTick,
    ToolCall,
)
from senex.tui.widgets.current_file import CurrentFileWidget


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.widget = CurrentFileWidget(id="current_file")

    def compose(self) -> ComposeResult:
        yield self.widget


def _label(host: _Host, sel: str) -> str:
    return str(host.widget.query_one(sel, Label).render())


@pytest.mark.asyncio
async def test_current_file_initial_state_is_blank() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        assert _label(host, "#filename") == "File: -"
        assert _label(host, "#phase") == "Phase: -"


@pytest.mark.asyncio
async def test_current_file_file_start_sets_filename() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            FileStart(ts=_now(), run_id="r1", path="src/foo.py", idx=1, total=10)
        )
        await pilot.pause()
        assert "src/foo.py" in _label(host, "#filename")


@pytest.mark.asyncio
async def test_current_file_phase_transitions() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            FileContextBuilt(
                ts=_now(), run_id="r1", path="x", graph_context_tokens=10
            )
        )
        await pilot.pause()
        assert "context" in _label(host, "#phase").lower()

        host.widget.handle_audit_event(
            ThinkingStarted(ts=_now(), run_id="r1", path="x")
        )
        await pilot.pause()
        assert "thinking" in _label(host, "#phase").lower()

        host.widget.handle_audit_event(
            OutputStarted(ts=_now(), run_id="r1", path="x")
        )
        await pilot.pause()
        assert "writing" in _label(host, "#phase").lower()

        host.widget.handle_audit_event(
            OutputComplete(
                ts=_now(),
                run_id="r1",
                path="x",
                total_output_tokens=10,
                latency_ms=100,
            )
        )
        await pilot.pause()
        assert "rendering" in _label(host, "#phase").lower()


@pytest.mark.asyncio
async def test_current_file_thinking_tick_increments() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            ThinkingTick(
                ts=_now(),
                run_id="r1",
                path="x",
                tokens_so_far=500,
                delta_since_last_tick=500,
            )
        )
        await pilot.pause()
        assert "500" in _label(host, "#thinking_tokens")


@pytest.mark.asyncio
async def test_current_file_output_tick_updates_out_counter() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            OutputTick(
                ts=_now(),
                run_id="r1",
                path="x",
                tokens_so_far=312,
                delta_since_last_tick=312,
            )
        )
        await pilot.pause()
        assert "312" in _label(host, "#out_tokens")


@pytest.mark.asyncio
async def test_current_file_tool_call_increments() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        for i in range(3):
            host.widget.handle_audit_event(
                ToolCall(
                    ts=_now(),
                    run_id="r1",
                    path="x",
                    tool_name="grep",
                    tool_input="{}",
                    call_id=f"c{i}",
                )
            )
        await pilot.pause()
        assert "3" in _label(host, "#tools_used")


@pytest.mark.asyncio
async def test_current_file_complete_then_start_resets_for_next() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            FileStart(ts=_now(), run_id="r1", path="src/foo.py", idx=1, total=10)
        )
        host.widget.handle_audit_event(
            ThinkingTick(
                ts=_now(),
                run_id="r1",
                path="src/foo.py",
                tokens_so_far=100,
                delta_since_last_tick=100,
            )
        )
        host.widget.handle_audit_event(
            FileComplete(
                ts=_now(),
                run_id="r1",
                path="src/foo.py",
                finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 1},
            )
        )
        host.widget.handle_audit_event(
            FileStart(ts=_now(), run_id="r1", path="src/bar.py", idx=2, total=10)
        )
        await pilot.pause()
        assert "src/bar.py" in _label(host, "#filename")
        # Counters reset on new FileStart.
        assert "0" in _label(host, "#thinking_tokens")
        assert "0" in _label(host, "#tools_used")


@pytest.mark.asyncio
async def test_current_file_thinking_complete_pins_in_token_count() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            ThinkingComplete(
                ts=_now(),
                run_id="r1",
                path="x",
                total_thinking_tokens=1247,
                latency_ms=200,
            )
        )
        await pilot.pause()
        assert "1247" in _label(host, "#thinking_tokens")
