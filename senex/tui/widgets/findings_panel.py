"""senex.tui.widgets.findings_panel — Recent-findings deque (M9 Task 9.6c).

Driven by ``FileComplete.last_finding_summary`` (R12 minimal subset:
``{priority, title, location}``). Maintains a ``deque(maxlen=30)``;
the 31st finding silently evicts the oldest (eviction is by design —
TUI is a window, not a log).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from senex.events import BaseEvent, FileComplete
from senex.tui.exceptions import WidgetRenderError

_MAX_ROWS = 30


@dataclass(frozen=True)
class FindingRow:
    priority: str
    path: str
    title: str
    location: str | None = None

    def render_line(self) -> str:
        bracket = f"[{self.priority.upper()}]".ljust(10)
        loc = f" ({self.location})" if self.location else ""
        return f"{bracket}{self.path}{loc} — {self.title}"


class FindingsPanelWidget(Widget):
    """Recent-findings panel (spec §5.7)."""

    DEFAULT_CSS = """
    FindingsPanelWidget { height: auto; min-height: 6; }
    .priority-high { color: red; }
    .priority-medium { color: yellow; }
    .priority-low { color: cyan; }
    .priority-healthy { color: green; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rows: deque[FindingRow] = deque(maxlen=_MAX_ROWS)

    def compose(self) -> ComposeResult:
        yield Static("Recent findings (0):", id="findings_header")
        yield Static("", id="findings_body")

    def rows(self) -> list[FindingRow]:
        """Public accessor for tests / replay."""
        return list(self._rows)

    def handle_audit_event(self, event: BaseEvent) -> None:
        try:
            if isinstance(event, FileComplete):
                summary = event.last_finding_summary
                if summary is None:
                    return
                row = FindingRow(
                    priority=summary.priority,
                    path=event.path,
                    title=summary.title,
                    location=summary.location,
                )
                self._rows.append(row)
                self._refresh()
        except Exception as exc:  # noqa: BLE001 — convert.
            raise WidgetRenderError(f"findings_panel render failed: {exc}") from exc

    def _refresh(self) -> None:
        try:
            header = self.query_one("#findings_header", Static)
            body = self.query_one("#findings_body", Static)
        except Exception:  # noqa: BLE001 — DOM not yet ready.
            return
        header.update(f"Recent findings ({len(self._rows)}):")
        if not self._rows:
            body.update("")
            return
        lines = [r.render_line() for r in self._rows]
        body.update("\n".join(lines))


__all__ = ["FindingRow", "FindingsPanelWidget"]
