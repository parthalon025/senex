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
from textual.widget import Widget
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


class _FocusHost(Widget):
    """Invisible focusable widget that anchors Monitor focus (F-10).

    The Monitor's five visible widgets are all read-only renderers that
    cannot accept focus; without this host, ``app.focused`` would be
    ``None`` after any modal dismiss. The CSS keeps the host
    non-displaying (zero height + no background) but ``can_focus = True``
    means Textual will park focus here when no other widget claims it.
    """

    can_focus = True

    DEFAULT_CSS = """
    _FocusHost {
        height: 0;
        width: 0;
        background: transparent;
    }
    """


class HelpScreen(ModalScreen[None]):
    """In-app help / keybindings reference (F-9).

    Lists every Monitor keybinding plus the help binding itself.
    Dismisses on ``Escape`` or ``Enter`` (per F-9 acceptance criterion).
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
        Binding("question_mark", "close", "Close"),
        Binding("f1", "close", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help_dialog {
        width: 60;
        height: auto;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #help_title { text-style: bold; }
    """

    _HELP_TEXT = (
        "senex Monitor - keybindings\n"
        "\n"
        "  q     Quit (with confirmation)\n"
        "  s     Skip current file\n"
        "  r     Rerun current file\n"
        "  p     Pause / resume\n"
        "  t     Toggle minimum priority\n"
        "  e     Dismiss error banner\n"
        "  ?     Show this help\n"
        "\n"
        "  ctrl+q   Force quit (cancels audit)\n"
        "\n"
        "Press Enter or Escape to close."
    )

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Label("senex - Help", id="help_title")
            yield Static(self._HELP_TEXT, id="help_body")

    def action_close(self) -> None:
        self.dismiss(None)


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
    | ``e`` | Dismiss error banner | no | (local only) |
    | ``?`` / ``F1`` | Show help screen | no | (local only) |
    """

    BINDINGS = [
        Binding("q", "request_quit", "Quit"),
        Binding("s", "request_skip", "Skip"),
        Binding("r", "request_rerun", "Rerun"),
        Binding("p", "toggle_pause", "Pause/Resume"),
        Binding("t", "toggle_min_priority", "Toggle priority"),
        Binding("e", "dismiss_banner", "Dismiss banner"),
        Binding("question_mark", "show_help", "Help"),
        Binding("f1", "show_help", "Help", show=False),
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
        # F-10: invisible focusable host. None of the five visible widgets
        # are focusable, so after a modal dismiss focus has nowhere to land.
        # This zero-height container takes focus so ``app.focused`` is
        # always non-None while the Monitor is active.
        yield _FocusHost(id="monitor_focus_host")

    async def on_screen_resume(self) -> None:
        """Restore focus to the host whenever this screen regains focus.

        Triggered on initial mount and after any modal (ConfirmDialog,
        HelpScreen) dismiss - the underlying Textual screen-resume hook
        is the canonical place to assert focus restoration (F-10).
        """
        self._restore_focus()

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
            self._restore_focus()
            if confirmed:
                self._post_then(Command(type="Quit", ts=_now()), exit_after=True)

        await self.app.push_screen(ConfirmDialog("Quit the audit?"), _quit_done)

    async def action_request_skip(self) -> None:
        if self._replay_mode or self._current_path is None:
            return
        path = self._current_path

        def _done(confirmed: bool | None) -> None:
            self._restore_focus()
            if confirmed:
                self._post_then(Command(type="Skip", target=path, ts=_now()))

        await self.app.push_screen(ConfirmDialog(f"Skip {path}?"), _done)

    async def action_request_rerun(self) -> None:
        if self._replay_mode or self._current_path is None:
            return
        path = self._current_path

        def _done(confirmed: bool | None) -> None:
            self._restore_focus()
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

    async def action_dismiss_banner(self) -> None:
        """Dismiss the sticky error banner (F-1).

        No-op when the banner is not currently displayed; subsequent
        errors will resurface it. The action is bound to the ``e`` key
        on this Monitor screen so the banner's own "press e to dismiss"
        instruction now works.
        """
        try:
            banner = self.query_one("#error_banner", ErrorBannerWidget)
        except Exception:  # noqa: BLE001 - DOM may be transitioning.
            return
        if not banner.display:
            return
        banner.dismiss()
        # Restore focus to the focusable host so keybindings continue
        # to work after dismissal (F-10 supports the same host).
        self._restore_focus()

    async def action_show_help(self) -> None:
        """Show the in-app help screen (F-9)."""
        await self.app.push_screen(HelpScreen(), self._help_done)

    def _help_done(self, _: object) -> None:
        """Restore focus after the help screen closes."""
        self._restore_focus()

    def _restore_focus(self) -> None:
        """Restore focus to the Monitor's focusable host (F-10).

        Called after any modal dismiss (ConfirmDialog, HelpScreen) to
        guarantee ``app.focused`` is non-None - keybindings are screen
        level so this is belt-and-braces, but the WCAG 2.4.3 / 2.4.7
        contract requires an asserted focus owner.
        """
        try:
            host = self.query_one("#monitor_focus_host")
        except Exception:  # noqa: BLE001 - DOM may be transitioning.
            return
        try:
            host.focus()
        except Exception:  # noqa: BLE001 - focus may be denied transiently.
            return

    async def _post(self, command: Command) -> None:
        await self._command_bus.publish(command)

    def _post_then(self, command: Command, *, exit_after: bool = False) -> None:
        """Schedule a command publish on the running loop (callback context)."""
        async def _do() -> None:
            await self._command_bus.publish(command)
            if exit_after:
                self.app.exit(0)

        self.app.call_later(_do)


__all__ = ["ConfirmDialog", "HelpScreen", "MonitorScreen"]
