"""Tests for ErrorBannerWidget (M9 Task 9.6d)."""
from __future__ import annotations

from datetime import datetime, UTC

import pytest
from textual.app import App, ComposeResult

from senex.events import (
    CompactionError,
    FileError,
    ModelLoadCompleteAfterWait,
    ModelLoadFailed,
    ModelLoadStillWaiting,
    ModelLoadWaiting,
    ModelUnloadFailed,
    ToolBudgetExhausted,
    ToolError,
)
from senex.tui.widgets.error_banner import ErrorBannerWidget


def _now() -> datetime:
    return datetime.now(tz=UTC)


class _Host(App[None]):
    BINDINGS = [("e", "dismiss_error", "Dismiss")]

    def __init__(self) -> None:
        super().__init__()
        self.widget = ErrorBannerWidget(id="banner")

    def compose(self) -> ComposeResult:
        yield self.widget

    async def action_dismiss_error(self) -> None:
        self.widget.dismiss()


@pytest.mark.asyncio
async def test_error_banner_hidden_initially() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        assert host.widget.display is False
        assert host.widget.error_count == 0


@pytest.mark.asyncio
async def test_error_banner_appears_on_first_error_event() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            FileError(
                ts=_now(),
                run_id="r1",
                path="x",
                phase="thinking",
                error_kind="lms",
                error_message="boom",
            )
        )
        await pilot.pause()
        assert host.widget.display is True
        assert host.widget.error_count == 1
        assert "boom" in host.widget.last_message


@pytest.mark.asyncio
async def test_error_banner_count_increments() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        for i in range(3):
            host.widget.handle_audit_event(
                FileError(
                    ts=_now(),
                    run_id="r1",
                    path=f"x{i}",
                    phase="thinking",
                    error_kind="lms",
                    error_message=f"msg{i}",
                )
            )
        await pilot.pause()
        assert host.widget.error_count == 3
        assert "msg2" in host.widget.last_message


@pytest.mark.asyncio
async def test_error_banner_dismiss_with_e_key() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            FileError(
                ts=_now(),
                run_id="r1",
                path="x",
                phase="t",
                error_kind="lms",
                error_message="msg",
            )
        )
        await pilot.pause()
        assert host.widget.display is True
        await pilot.press("e")
        await pilot.pause()
        assert host.widget.display is False


@pytest.mark.asyncio
async def test_error_banner_resurfaces_on_new_error_after_dismiss() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            FileError(
                ts=_now(),
                run_id="r1",
                path="x",
                phase="t",
                error_kind="lms",
                error_message="m1",
            )
        )
        host.widget.dismiss()
        await pilot.pause()
        assert host.widget.display is False
        host.widget.handle_audit_event(
            FileError(
                ts=_now(),
                run_id="r1",
                path="x",
                phase="t",
                error_kind="lms",
                error_message="m2",
            )
        )
        await pilot.pause()
        assert host.widget.display is True
        assert host.widget.error_count == 2


@pytest.mark.asyncio
async def test_error_banner_handles_all_documented_kinds() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        events = [
            FileError(
                ts=_now(),
                run_id="r1",
                path="x",
                phase="t",
                error_kind="lms",
                error_message="m",
            ),
            ToolError(
                ts=_now(),
                run_id="r1",
                path="x",
                tool_name="grep",
                call_id="c",
                kind="path_rejected",
                error_message="bad path",
            ),
            ToolBudgetExhausted(
                ts=_now(), run_id="r1", path="x", calls_made=5
            ),
            CompactionError(
                ts=_now(), run_id="r1", path="x", error_kind="schema"
            ),
            ModelLoadFailed(
                ts=_now(),
                run_id="r1",
                model_id="m",
                error_kind="timeout",
                error_message="late",
            ),
            ModelUnloadFailed(
                ts=_now(),
                run_id="r1",
                model_id="m",
                error_kind="api",
                error_message="api error",
            ),
        ]
        for e in events:
            host.widget.handle_audit_event(e)
        await pilot.pause()
        assert host.widget.error_count == len(events)


@pytest.mark.asyncio
async def test_error_banner_show_external_error() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.show_external_error("audit failed: RuntimeError: boom")
        await pilot.pause()
        assert host.widget.display is True
        assert "boom" in host.widget.last_message


@pytest.mark.asyncio
async def test_error_banner_surfaces_model_load_waiting() -> None:
    """M11: ``ModelLoadWaiting`` must show the manual-load instructions."""
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            ModelLoadWaiting(
                ts=_now(),
                run_id="r1",
                model_id="google/gemma-4-26b-a4b",
                timeout_seconds=600,
                reason="resource guardrail rejected lms load",
            )
        )
        await pilot.pause()
        assert host.widget.display is True
        msg = host.widget.last_message
        assert "auto_load failed" in msg
        assert "google/gemma-4-26b-a4b" in msg
        assert "lms load" in msg


@pytest.mark.asyncio
async def test_error_banner_surfaces_model_load_still_waiting() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            ModelLoadStillWaiting(
                ts=_now(),
                run_id="r1",
                model_id="m",
                elapsed_seconds=120,
                remaining_seconds=480,
            )
        )
        await pilot.pause()
        assert "still waiting" in host.widget.last_message
        assert "120s" in host.widget.last_message


@pytest.mark.asyncio
async def test_error_banner_surfaces_model_load_complete_after_wait() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(
            ModelLoadCompleteAfterWait(
                ts=_now(),
                run_id="r1",
                model_id="m",
                fingerprint="sha256:abc",
                wait_seconds=42,
            )
        )
        await pilot.pause()
        assert "loaded after" in host.widget.last_message
        assert "42s" in host.widget.last_message
