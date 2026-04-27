"""senex.tui — Textual launcher + monitor TUI (spec §5.7).

The TUI is a thin consumer of the event bus: every line on screen is
driven by an event the auditor already publishes. Crashing widgets do
NOT take the audit down — ``DiskWriterSubscriber`` is the durable
record. See ``senex/subscribers/`` for the back-pressure semantics.
"""
from __future__ import annotations

from senex.tui.exceptions import ReplayError, WidgetRenderError

__all__ = ["ReplayError", "WidgetRenderError"]
