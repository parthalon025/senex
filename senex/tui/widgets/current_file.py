"""senex.tui.widgets.current_file — current-file panel (placeholder; M9 Task 9.6b)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from senex.events import BaseEvent


class CurrentFileWidget(Widget):
    """Stub; replaced in Task 9.6b."""

    DEFAULT_CSS = "CurrentFileWidget { height: 4; }"

    def compose(self) -> ComposeResult:
        yield Label("File: -", id="filename")
        yield Label("Phase: -", id="phase")

    def handle_audit_event(self, event: BaseEvent) -> None:  # noqa: ARG002 — 9.6b fills.
        return None


__all__ = ["CurrentFileWidget"]
