"""senex.tui.widgets.progress — Progress + rolling-ETA (M9 Task 9.6a).

Driven by ``FileStart`` (captures ``total``) and ``FileComplete`` (records
the duration of the just-completed file). ETA is computed from a rolling
window of the last N=10 per-file durations × remaining-file count.
"""
from __future__ import annotations

import time
from collections import deque

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from senex.events import BaseEvent, FileComplete, FileStart
from senex.tui.exceptions import WidgetRenderError

_WINDOW_N = 10


def _format_eta(seconds: float) -> str:
    if seconds <= 0 or seconds != seconds:  # NaN guard
        return "--"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


class ProgressWidget(Widget):
    """Progress bar + rolling-window ETA (spec §5.7)."""

    DEFAULT_CSS = "ProgressWidget { height: 1; }"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._idx = 0
        self._total = 0
        self._durations: deque[float] = deque(maxlen=_WINDOW_N)
        self._last_start_time: float | None = None

    def compose(self) -> ComposeResult:
        yield Label("0/0 files 0% — ETA --", id="progress_label")

    def _now(self) -> float:
        return time.monotonic()

    def handle_audit_event(self, event: BaseEvent) -> None:
        try:
            if isinstance(event, FileStart):
                self._idx = event.idx
                self._total = event.total
                self._last_start_time = self._now()
                self._refresh()
            elif isinstance(event, FileComplete):
                if self._last_start_time is not None:
                    self._durations.append(self._now() - self._last_start_time)
                self._refresh()
        except Exception as exc:  # noqa: BLE001 — convert to WidgetRenderError.
            raise WidgetRenderError(f"progress render failed: {exc}") from exc

    def _refresh(self) -> None:
        try:
            label = self.query_one("#progress_label", Label)
        except Exception:  # noqa: BLE001 — DOM not ready.
            return
        if self._total <= 0:
            label.update("0/0 files — — ETA --")
            return
        pct = int((self._idx / self._total) * 100)
        eta_s = self._compute_eta()
        label.update(
            f"{self._idx}/{self._total} files {pct}% — ETA {_format_eta(eta_s)}"
        )

    def _compute_eta(self) -> float:
        if not self._durations:
            return -1.0
        avg = sum(self._durations) / len(self._durations)
        remaining = max(0, self._total - self._idx + 1)
        return avg * remaining


__all__ = ["ProgressWidget"]
