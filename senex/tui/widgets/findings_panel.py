"""senex.tui.widgets.findings_panel — recent-findings deque (placeholder; 9.6c)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from senex.events import BaseEvent


class FindingsPanelWidget(Widget):
    """Stub; replaced in Task 9.6c."""

    DEFAULT_CSS = "FindingsPanelWidget { height: 6; }"

    def compose(self) -> ComposeResult:
        yield Label("Recent findings (0):", id="findings_header")

    def handle_audit_event(self, event: BaseEvent) -> None:  # noqa: ARG002 — 9.6c fills.
        return None


__all__ = ["FindingsPanelWidget"]
