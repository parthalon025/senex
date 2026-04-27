"""senex.tui.widgets.status — status strip (placeholder; M9 Task 9.6e)."""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from senex.events import BaseEvent
from senex.subscribers.metrics import MetricsCollectorSubscriber


class StatusStripWidget(Widget):
    """Stub; replaced in Task 9.6e."""

    DEFAULT_CSS = "StatusStripWidget { height: 1; }"

    def __init__(self, metrics: MetricsCollectorSubscriber, **kw: Any) -> None:
        super().__init__(**kw)
        self._metrics_sub = metrics

    def compose(self) -> ComposeResult:
        yield Label(
            "HIGH 0 | MEDIUM 0 | LOW 0 | HEALTHY 0 | TOOLS 0 | COMPACTIONS 0",
            id="status_label",
        )

    def handle_audit_event(self, event: BaseEvent) -> None:  # noqa: ARG002 — 9.6e fills.
        return None


__all__ = ["StatusStripWidget"]
