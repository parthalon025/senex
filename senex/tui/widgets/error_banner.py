"""senex.tui.widgets.error_banner — sticky error banner (placeholder; 9.6d)."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from senex.events import BaseEvent


class ErrorBannerWidget(Widget):
    """Stub; replaced in Task 9.6d."""

    DEFAULT_CSS = "ErrorBannerWidget { height: 1; display: none; }"

    def compose(self) -> ComposeResult:
        yield Label("", id="banner_label")

    def handle_audit_event(self, event: BaseEvent) -> None:  # noqa: ARG002 — 9.6d fills.
        return None

    def show_external_error(self, message: str) -> None:
        self.display = True
        try:
            self.query_one("#banner_label", Label).update(message)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["ErrorBannerWidget"]
