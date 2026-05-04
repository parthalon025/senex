"""Tests for FindingsPanelWidget (M9 Task 9.6c)."""
from __future__ import annotations

from datetime import datetime, UTC

import pytest
from textual.app import App, ComposeResult

from senex.events import FileComplete, FindingSummary
from senex.tui.widgets.findings_panel import FindingsPanelWidget


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _fc(path: str, priority: str | None, title: str | None = None) -> FileComplete:
    summary = (
        FindingSummary(priority=priority, title=title or "t", location=None)  # type: ignore[arg-type]
        if priority
        else None
    )
    return FileComplete(
        ts=_now(),
        run_id="r1",
        path=path,
        finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0},
        last_finding_summary=summary,
    )


class _Host(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.widget = FindingsPanelWidget(id="findings")

    def compose(self) -> ComposeResult:
        yield self.widget


@pytest.mark.asyncio
async def test_findings_panel_initial_empty() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        assert len(host.widget.rows()) == 0


@pytest.mark.asyncio
async def test_findings_panel_appends_on_file_complete() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(_fc("src/x.py", "high", "MD5 hash"))
        await pilot.pause()
        rows = host.widget.rows()
        assert len(rows) == 1
        assert rows[0].priority == "high"
        assert rows[0].path == "src/x.py"
        assert rows[0].title == "MD5 hash"


@pytest.mark.asyncio
async def test_findings_panel_skips_when_summary_is_none() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        host.widget.handle_audit_event(_fc("src/clean.py", None))
        await pilot.pause()
        assert len(host.widget.rows()) == 0


@pytest.mark.asyncio
async def test_findings_panel_deque_maxlen_30() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        for i in range(31):
            host.widget.handle_audit_event(
                _fc(f"src/f{i}.py", "low", title=f"f{i}")
            )
        await pilot.pause()
        rows = host.widget.rows()
        assert len(rows) == 30
        # First-added (idx=0) was evicted; idx=1 is now the oldest.
        assert rows[0].title == "f1"


@pytest.mark.asyncio
async def test_findings_panel_priority_styling_classes() -> None:
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        for prio in ("high", "medium", "low", "healthy"):
            host.widget.handle_audit_event(_fc(f"f_{prio}.py", prio))
        await pilot.pause()
        rows = host.widget.rows()
        priorities = [r.priority for r in rows]
        assert priorities == ["high", "medium", "low", "healthy"]
