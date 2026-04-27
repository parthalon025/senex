"""senex.tui.widgets.status — Status strip (M9 Task 9.6e).

Reads ``MetricsCollectorSubscriber.metrics`` and renders the rollup line.
Updates on every non-tick event (per spec §5.7 "no per-token render");
``*Tick`` events are skipped.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from senex.events import BaseEvent, OutputTick, ThinkingTick
from senex.subscribers.metrics import MetricsCollectorSubscriber
from senex.tui.exceptions import WidgetRenderError


class StatusStripWidget(Widget):
    """Status strip (spec §5.7)."""

    DEFAULT_CSS = "StatusStripWidget { height: 1; }"

    def __init__(self, metrics: MetricsCollectorSubscriber, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._metrics_sub = metrics
        # Test instrumentation: counts non-tick render dispatches.
        self.render_calls: int = 0

    def compose(self) -> ComposeResult:
        yield Label(self._render_text(), id="status_label")

    def _render_text(self) -> str:
        m = self._metrics_sub.metrics
        f = m.findings_by_priority
        return (
            f"HIGH {f.get('high', 0)} | "
            f"MEDIUM {f.get('medium', 0)} | "
            f"LOW {f.get('low', 0)} | "
            f"HEALTHY {f.get('healthy', 0)} | "
            f"TOOLS {m.tool_calls} | "
            f"COMPACTIONS {m.compactions}"
        )

    def handle_audit_event(self, event: BaseEvent) -> None:
        if isinstance(event, (ThinkingTick, OutputTick)):
            return
        self.render_calls += 1
        try:
            self.query_one("#status_label", Label).update(self._render_text())
        except Exception as exc:  # noqa: BLE001 — convert.
            raise WidgetRenderError(f"status render failed: {exc}") from exc


__all__ = ["StatusStripWidget"]
