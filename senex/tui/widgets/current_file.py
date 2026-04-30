"""senex.tui.widgets.current_file — Current-file panel (M9 Task 9.6b).

Driven by ``FileStart`` (sets filename + resets counters), ``FileContextBuilt``
/ ``ThinkingStarted`` / ``OutputStarted`` / ``OutputComplete`` (phase
transitions), ``ThinkingTick`` / ``OutputTick`` (token counters), and
``ToolCall`` (tools-used counter).

Per the plan: counters reset on **FileStart** (NOT on FileComplete — the
user briefly sees the final state before the next file starts).
"""
from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Label

from senex.events import (
    BaseEvent,
    FileContextBuilt,
    FileStart,
    MemoryInjected,
    OutputComplete,
    OutputStarted,
    OutputTick,
    SkillsInjected,
    ThinkingComplete,
    ThinkingStarted,
    ThinkingTick,
    ToolCall,
)
from senex.tui.exceptions import WidgetRenderError


class CurrentFileWidget(Widget):
    """Current-file panel (spec §5.7)."""

    DEFAULT_CSS = "CurrentFileWidget { height: 5; }"

    def __init__(self, tools_max: int = 8, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._phase = "-"
        self._thinking_tokens = 0
        self._out_tokens = 0
        self._in_tokens = 0
        self._tools_used = 0
        self._tools_max = tools_max
        self._skill_names: list[str] = []
        self._memory_findings = 0

    def compose(self) -> ComposeResult:
        yield Label("File: -", id="filename")
        yield Label(f"Phase: {self._phase}", id="phase")
        with Horizontal():
            yield Label("Thinking tokens: 0", id="thinking_tokens")
            yield Label("In: 0", id="in_tokens")
            yield Label("Out: 0", id="out_tokens")
        with Horizontal():
            yield Label(f"Tools used: 0 / {self._tools_max}", id="tools_used")
            yield Label("Skills: -", id="skills")
            yield Label("Memory: 0", id="memory")

    def handle_audit_event(self, event: BaseEvent) -> None:
        try:
            self._dispatch(event)
        except Exception as exc:  # noqa: BLE001 — convert to WidgetRenderError.
            raise WidgetRenderError(f"current_file render failed: {exc}") from exc

    def _dispatch(self, event: BaseEvent) -> None:
        if isinstance(event, FileStart):
            self._reset_for_new_file(event.path)
        elif isinstance(event, FileContextBuilt):
            self._phase = "context"
            self._refresh_phase()
        elif isinstance(event, ThinkingStarted):
            self._phase = "thinking"
            self._refresh_phase()
        elif isinstance(event, ThinkingTick):
            self._thinking_tokens = event.tokens_so_far
            self._refresh_thinking()
        elif isinstance(event, ThinkingComplete):
            self._thinking_tokens = event.total_thinking_tokens
            self._refresh_thinking()
        elif isinstance(event, OutputStarted):
            self._phase = "writing"
            self._refresh_phase()
        elif isinstance(event, OutputTick):
            self._out_tokens = event.tokens_so_far
            self._refresh_out()
        elif isinstance(event, OutputComplete):
            self._out_tokens = event.total_output_tokens
            self._phase = "rendering"
            self._refresh_phase()
            self._refresh_out()
        elif isinstance(event, ToolCall):
            self._tools_used += 1
            self._refresh_tools()
        elif isinstance(event, SkillsInjected):
            self._skill_names = list(event.names)
            self._refresh_skills()
        elif isinstance(event, MemoryInjected):
            self._memory_findings = event.finding_count
            self._refresh_memory()

    def _reset_for_new_file(self, path: str) -> None:
        self._phase = "-"
        self._thinking_tokens = 0
        self._out_tokens = 0
        self._in_tokens = 0
        self._tools_used = 0
        self._skill_names = []
        self._memory_findings = 0
        self._set("#filename", f"File: {path}")
        self._refresh_phase()
        self._refresh_thinking()
        self._refresh_out()
        self._refresh_tools()
        self._refresh_skills()
        self._refresh_memory()

    def _refresh_phase(self) -> None:
        self._set("#phase", f"Phase: {self._phase}")

    def _refresh_thinking(self) -> None:
        self._set("#thinking_tokens", f"Thinking tokens: {self._thinking_tokens}")

    def _refresh_out(self) -> None:
        self._set("#out_tokens", f"Out: {self._out_tokens}")

    def _refresh_tools(self) -> None:
        self._set(
            "#tools_used",
            f"Tools used: {self._tools_used} / {self._tools_max}",
        )

    def _refresh_skills(self) -> None:
        if self._skill_names:
            text = "Skills: " + ", ".join(self._skill_names)
        else:
            text = "Skills: -"
        self._set("#skills", text)

    def _refresh_memory(self) -> None:
        self._set("#memory", f"Memory: {self._memory_findings}")

    def _set(self, sel: str, text: str) -> None:
        try:
            self.query_one(sel, Label).update(text)
        except Exception:  # noqa: BLE001 — DOM not yet ready.
            return


__all__ = ["CurrentFileWidget"]
