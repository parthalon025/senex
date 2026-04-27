"""senex.tui.widgets.progress — Progress bar + ETA (placeholder; M9 Task 9.6a)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from senex.events import BaseEvent


class ProgressWidget(Widget):
    """Stub; replaced in Task 9.6a."""

    DEFAULT_CSS = "ProgressWidget { height: 1; }"

    def compose(self) -> ComposeResult:
        yield Label("0/0 files 0% — ETA --", id="progress_label")

    def handle_audit_event(self, event: BaseEvent) -> None:  # noqa: ARG002 — task 9.6a fills this
        return None


__all__ = ["ProgressWidget"]
