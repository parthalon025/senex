"""senex.tui.app — Textual ``SenexApp`` skeleton (M9 Task 9.4).

Implements spec §5.7 (TUI overview), ARCH-16 (keybinding namespacing),
conventions §3 (cancellation propagation) and §4 (named exceptions).

The app composes two screens:
  * ``LauncherScreen`` — repo / lens / model selection; emits a
    ``RuntimeConfig`` via ``SenexApp.start_audit``.
  * ``MonitorScreen`` — live audit progress; binds the command-bus
    keybindings (``q``/``ctrl+q``/``s``/``r``/``p``/``t``).

The audit task lifecycle:
  1. ``start_audit(rt_cfg)`` pushes ``MonitorScreen`` and creates a
     task wrapping ``run_audit``.
  2. ``add_done_callback(_on_audit_done)`` captures any exception, logs
     it, and renders an error message; NEVER swallows.
  3. ``ctrl+q`` cancels the task (the cancellation propagates as
     ``CancelledError`` inside ``run_audit``).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from textual.app import App
from textual.binding import Binding

from senex.events import CommandBus, EventBus
from senex.tui.exceptions import WidgetRenderError
from senex.tui.launcher import LauncherScreen
from senex.tui.monitor import MonitorScreen

if TYPE_CHECKING:  # pragma: no cover
    from senex.tui.runtime import RuntimeConfig

log = logging.getLogger(__name__)

# Type alias for an audit-runner callable. Production wires this to
# ``senex.auditor.run_audit``; tests inject a fake.
AuditRunner = Callable[..., Awaitable[int]]


def _default_audit_runner(**kwargs: Any) -> Awaitable[int]:
    from senex.auditor import run_audit

    return run_audit(**kwargs)


class SenexApp(App[int]):
    """Top-level Textual app (spec §5.7).

    Owns the bus, the command bus, and the audit task. Screens read
    these via ``self.app``.
    """

    BINDINGS = [
        Binding("ctrl+q", "force_quit", "Force quit", priority=True),
    ]

    def __init__(
        self,
        config_path: Path,
        repo_path_default: Path,
        bus: EventBus | None = None,
        command_bus: CommandBus | None = None,
        replay_mode: bool = False,
    ) -> None:
        super().__init__()
        self._config_path = config_path
        self._repo_path_default = repo_path_default
        self._bus: EventBus = bus if bus is not None else EventBus()
        self._command_bus: CommandBus = (
            command_bus if command_bus is not None else CommandBus()
        )
        self._audit_task: asyncio.Task[int] | None = None
        self._last_error_message: str | None = None
        self._audit_runner: AuditRunner = _default_audit_runner
        self._replay_mode = replay_mode
        self._truncated_replay = False
        self._pending_runtime_config: "RuntimeConfig | None" = None

    @classmethod
    def in_replay_mode(
        cls,
        audit_dir: Path,
        truncated: bool = False,
    ) -> SenexApp:
        """Construct an app for the ``senex view`` replay handler."""
        app = cls(
            config_path=audit_dir / "config.snapshot.toml",
            repo_path_default=audit_dir,
            replay_mode=True,
        )
        app._truncated_replay = truncated
        return app

    async def on_mount(self) -> None:
        await self.push_screen(LauncherScreen(self._config_path, self._repo_path_default))

    @property
    def bus(self) -> EventBus:
        return self._bus

    @property
    def command_bus(self) -> CommandBus:
        return self._command_bus

    @property
    def replay_mode(self) -> bool:
        return self._replay_mode

    def start_audit(self, runtime_config: "RuntimeConfig") -> None:
        """Push Monitor + spawn the audit task."""
        self._pending_runtime_config = runtime_config
        # Defer task creation until after the screen push so the Monitor
        # has had a chance to subscribe widgets to the bus.
        self.call_later(self._push_monitor_and_spawn)

    def start_audit_for_test(self) -> None:
        """Test helper: skip RuntimeConfig and just spawn ``_audit_runner``.

        Tests inject a fake ``_audit_runner`` and use this to drive the
        task lifecycle without building a full RuntimeConfig.
        """
        self.call_later(self._push_monitor_and_spawn_test_only)

    async def _push_monitor_and_spawn(self) -> None:
        cfg = self._pending_runtime_config
        tools_max = (
            cfg.config.lmstudio.tools.max_calls_per_file
            if cfg is not None
            else 8
        )
        await self.push_screen(
            MonitorScreen(self._bus, self._command_bus, tools_max=tools_max)
        )
        if cfg is None:
            return
        self._pending_runtime_config = None
        awaitable = self._audit_runner(
            repo=cfg.repo,
            config=cfg.config,
            lens=cfg.lens,
            bus=self._bus,
            command_bus=self._command_bus,
            config_path=cfg.config_path,
            output_root=cfg.output_root,
            resume=cfg.resume,
            allow_mixed_resume=cfg.allow_mixed_resume,
        )
        self._audit_task = asyncio.ensure_future(awaitable)
        self._audit_task.add_done_callback(self._on_audit_done)

    async def _push_monitor_and_spawn_test_only(self) -> None:
        await self.push_screen(MonitorScreen(self._bus, self._command_bus))
        awaitable = self._audit_runner()
        self._audit_task = asyncio.ensure_future(awaitable)
        self._audit_task.add_done_callback(self._on_audit_done)

    def _on_audit_done(self, task: asyncio.Task[int]) -> None:
        if task.cancelled():
            log.info("audit task cancelled")
            return
        exc = task.exception()
        if exc is not None:
            self._last_error_message = f"{type(exc).__name__}: {exc}"
            log.error("audit task crashed: %s", exc, exc_info=exc)
            try:
                self.call_from_thread(self._render_error, exc)
            except Exception:  # noqa: BLE001 — done-callbacks must never raise.
                # If we're already on the loop, ``call_from_thread`` raises;
                # fall back to a direct call.
                self._render_error(exc)
            return
        # Successful completion: ``run_audit`` returns the exit code.
        log.info("audit task completed, exit_code=%s", task.result())

    def _render_error(self, exc: BaseException) -> None:
        """Surface ``exc`` via the ErrorBanner widget on the active Monitor."""
        screen = self.screen
        if isinstance(screen, MonitorScreen):
            try:
                screen.post_audit_error(exc)
            except WidgetRenderError as render_exc:
                log.error("error banner failed: %s", render_exc)

    def _handle_widget_error(self, error: WidgetRenderError) -> None:
        """Sink for widget render exceptions — never raises further."""
        self._last_error_message = str(error)
        log.error("widget render error: %s", error)

    async def action_force_quit(self) -> None:
        """``ctrl+q``: cancel any running audit, then exit."""
        if self._audit_task is not None and not self._audit_task.done():
            self._audit_task.cancel()
            try:
                await self._audit_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                # Swallow CancelledError on shutdown; _on_audit_done logs.
                pass
        self.exit(130)


__all__ = ["AuditRunner", "SenexApp"]
