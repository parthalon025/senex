"""senex.tui.widgets.error_banner — sticky error banner (M9 Task 9.6d).

Driven by ``FileError``, ``ToolError``, ``ToolBudgetExhausted``,
``CompactionError``, ``ModelLoadFailed``, ``ModelUnloadFailed``.

Also surfaces M11 manual-load progress (``ModelLoadWaiting``,
``ModelLoadStillWaiting``, ``ModelLoadCompleteAfterWait``) so the user
knows when auto_load failed but a manual GUI load can still rescue
the run. Sticky: remains visible until dismissed via the ``e``
keybinding (registered on ``MonitorScreen.BINDINGS`` -> action
``dismiss_banner``); resurfaces on the next error or wait
notification.
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from senex.events import (
    BaseEvent,
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
from senex.tui.exceptions import WidgetRenderError


class ErrorBannerWidget(Widget):
    """Sticky error banner (spec §5.7)."""

    DEFAULT_CSS = """
    ErrorBannerWidget { height: 1; display: none; background: $error 30%; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._count: int = 0
        self._last_message: str = ""

    def compose(self) -> ComposeResult:
        yield Label("", id="banner_label")

    @property
    def error_count(self) -> int:
        return self._count

    @property
    def last_message(self) -> str:
        return self._last_message

    def handle_audit_event(self, event: BaseEvent) -> None:
        try:
            msg = self._classify(event)
        except Exception as exc:  # noqa: BLE001 — convert.
            raise WidgetRenderError(f"error_banner classify failed: {exc}") from exc
        if msg is None:
            return
        self._count += 1
        self._last_message = msg
        self._show()

    def _classify(self, event: BaseEvent) -> str | None:
        if isinstance(event, FileError):
            return f"FileError {event.path}: {event.error_message}"
        if isinstance(event, ToolError):
            return (
                f"ToolError tool={event.tool_name} kind={event.kind}: {event.error_message}"
            )
        if isinstance(event, ToolBudgetExhausted):
            return f"ToolBudgetExhausted {event.path}: {event.calls_made} calls"
        if isinstance(event, CompactionError):
            return f"CompactionError {event.path}: {event.error_kind}"
        if isinstance(event, ModelLoadFailed):
            return (
                f"ModelLoadFailed {event.model_id}: {event.error_kind} "
                f"({event.error_message})"
            )
        if isinstance(event, ModelUnloadFailed):
            return f"ModelUnloadFailed {event.model_id}: {event.error_kind}"
        if isinstance(event, ModelLoadWaiting):
            # Surfaces the manual-load instructions prominently. The banner
            # stays sticky so the user sees them even while heartbeats arrive.
            return (
                f"auto_load failed: {event.reason} — "
                f"load `{event.model_id}` on the inference server "
                f"(SGLang: restart container; LM Studio: GUI/`lms load`); "
                f"waiting up to {event.timeout_seconds}s"
            )
        if isinstance(event, ModelLoadStillWaiting):
            return (
                f"still waiting for manual load of `{event.model_id}` "
                f"({event.elapsed_seconds}s / "
                f"{event.elapsed_seconds + event.remaining_seconds}s)"
            )
        if isinstance(event, ModelLoadCompleteAfterWait):
            return (
                f"model `{event.model_id}` loaded after "
                f"{event.wait_seconds}s wait — proceeding"
            )
        return None

    def show_external_error(self, message: str) -> None:
        """Surface a non-bus error (e.g. an audit-task crash)."""
        self._count += 1
        self._last_message = message
        self._show()

    def dismiss(self) -> None:
        """Hide the banner. Subsequent errors resurface it."""
        self.display = False

    def _show(self) -> None:
        self.display = True
        try:
            self.query_one("#banner_label", Label).update(
                f"[!] {self._count} error(s) — last: {self._last_message}  (press e to dismiss)"
            )
        except Exception:  # noqa: BLE001 — DOM not ready.
            return


__all__ = ["ErrorBannerWidget"]
