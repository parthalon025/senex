"""senex.tui.monitor — Monitor screen + command-bus keybindings (M9 Tasks 9.6/9.8).

Implements spec §5.7 (Monitor widgets) + §5.6.2 (command bus) + ARCH-16
(keybinding namespacing — Monitor owns these keys; Launcher owns nothing
global; replay-mode disables ``s``/``r``).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen, Screen
from textual.widgets import Label, Static

from senex.events import (
    BaseEvent,
    Command,
    CommandBus,
    EventBus,
    FileStart,
)
from senex.subscribers.metrics import MetricsCollectorSubscriber
from senex.tui.exceptions import WidgetRenderError
from senex.tui.widgets.current_file import CurrentFileWidget
from senex.tui.widgets.error_banner import ErrorBannerWidget
from senex.tui.widgets.findings_panel import FindingsPanelWidget
from senex.tui.widgets.progress import ProgressWidget
from senex.tui.widgets.status import StatusStripWidget

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class ConfirmDialog(ModalScreen[bool]):
    """Tiny modal: ``enter`` = True; ``escape`` = False."""

    BINDINGS = [
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Container(id="confirm_dialog"):
            yield Static(self._prompt, id="confirm_prompt")
            yield Label("Press Enter to confirm, Escape to cancel.")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class MonitorScreen(Screen[None]):
    """Monitor (spec §5.7).

    Composes the 5 widgets and binds the command-bus keybindings:

    | Key | Action | Confirms? | Posts |
    |---|---|---|---|
    | ``q`` | Quit | yes | ``Command(type="Quit")`` |
    | ``ctrl+q`` | Force-quit | no | ``Quit`` + cancels audit |
    | ``s`` | Skip current | yes | ``Command(type="Skip", target=path)`` |
    | ``r`` | Rerun current | yes | ``Command(type="Rerun", target=path)`` |
    | ``p`` | Pause/Resume toggle | no | ``Command(type="Pause"|"Resume")`` |
    | ``t`` | Toggle min-priority display | no | (local only) |
    """

    BINDINGS = [
        Binding("q", "request_quit", "Quit"),
        Binding("s", "request_skip", "Skip"),
        Binding("r", "request_rerun", "Rerun"),
        Binding("p", "toggle_pause", "Pause/Resume"),
        Binding("t", "toggle_min_priority", "Toggle priority"),
    ]

    DEFAULT_CSS = """
    MonitorScreen { layout: vertical; }
    """

    def __init__(
        self,
        bus: EventBus,
        command_bus: CommandBus,
        tools_max: int = 8,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._command_bus = command_bus
        self._metrics = MetricsCollectorSubscriber()
        self._current_path: str | None = None
        self._paused = False
        self._min_priority_display = False
        self._sub_handle: Any = None
        self._replay_mode = False
        self._tools_max = tools_max

    def set_replay_mode(self, value: bool) -> None:
        self._replay_mode = value

    def compose(self) -> ComposeResult:
        yield ErrorBannerWidget(id="error_banner")
        yield ProgressWidget(id="progress")
        yield CurrentFileWidget(tools_max=self._tools_max, id="current_file")
        yield FindingsPanelWidget(id="findings_panel")
        yield StatusStripWidget(self._metrics, id="status_strip")

    async def on_mount(self) -> None:
        # Register a local subscriber on the bus so each event is fanned
        # out to widgets + the metrics aggregator. We use ``BaseEvent`` so
        # every published event is dispatched.
        self._sub_handle = self._bus.subscribe_local(
            "Monitor", BaseEvent, self._dispatch_event
        )

    async def on_unmount(self) -> None:
        if self._sub_handle is not None:
            self._sub_handle.unsubscribe()

    async def _dispatch_event(self, event: BaseEvent) -> None:
        # Track current file path for command targets.
        if isinstance(event, FileStart):
            self._current_path = event.path
        # Metrics aggregator first.
        try:
            await self._metrics.consume(event)
        except Exception as exc:  # noqa: BLE001 — never propagate to bus.
            log.error("metrics consume failed: %s", exc)
        # Fan out to widgets.
        for wid in (
            "error_banner",
            "progress",
            "current_file",
            "findings_panel",
            "status_strip",
        ):
            try:
                widget = self.query_one(f"#{wid}")
            except Exception:  # noqa: BLE001 — DOM may be transitioning.
                continue
            handler = getattr(widget, "handle_audit_event", None)
            if handler is None:
                continue
            try:
                handler(event)
            except WidgetRenderError as exc:
                self._handle_widget_error(exc)
            except Exception as exc:  # noqa: BLE001 — convert to render error
                self._handle_widget_error(WidgetRenderError(str(exc)))

    def _handle_widget_error(self, exc: WidgetRenderError) -> None:
        log.error("widget render error: %s", exc)
        # Surface via app sink if available.
        from senex.tui.app import SenexApp

        if isinstance(self.app, SenexApp):
            self.app._handle_widget_error(exc)

    def post_audit_error(self, exc: BaseException) -> None:
        """Surface a top-level audit-task error via the ErrorBanner."""
        try:
            banner = self.query_one("#error_banner", ErrorBannerWidget)
            banner.show_external_error(f"audit failed: {type(exc).__name__}: {exc}")
        except Exception as render_exc:  # noqa: BLE001
            raise WidgetRenderError(str(render_exc)) from render_exc

    # ---------- key actions ----------

    async def action_request_quit(self) -> None:
        if self._replay_mode:
            await self._post(Command(type="Quit", ts=_now()))
            self.app.exit(0)
            return

        def _quit_done(confirmed: bool | None) -> None:
            if confirmed:
                self._post_then(Command(type="Quit", ts=_now()), exit_after=True)

        await self.app.push_screen(ConfirmDialog("Quit the audit?"), _quit_done)

    async def action_request_skip(self) -> None:
        if self._replay_mode or self._current_path is None:
            return
        path = self._current_path

        def _done(confirmed: bool | None) -> None:
            if confirmed:
                self._post_then(Command(type="Skip", target=path, ts=_now()))

        await self.app.push_screen(ConfirmDialog(f"Skip {path}?"), _done)

    async def action_request_rerun(self) -> None:
        if self._replay_mode or self._current_path is None:
            return
        path = self._current_path

        def _done(confirmed: bool | None) -> None:
            if confirmed:
                self._post_then(Command(type="Rerun", target=path, ts=_now()))

        await self.app.push_screen(ConfirmDialog(f"Rerun {path}?"), _done)

    async def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            await self._post(Command(type="Pause", ts=_now()))
        else:
            await self._post(Command(type="Resume", ts=_now()))

    async def action_toggle_min_priority(self) -> None:
        self._min_priority_display = not self._min_priority_display

    async def _post(self, command: Command) -> None:
        await self._command_bus.publish(command)

    def _post_then(self, command: Command, *, exit_after: bool = False) -> None:
        """Schedule a command publish on the running loop (callback context)."""
        async def _do() -> None:
            await self._command_bus.publish(command)
            if exit_after:
                self.app.exit(0)

        self.app.call_later(_do)


__all__ = ["ConfirmDialog", "MonitorScreen"]
