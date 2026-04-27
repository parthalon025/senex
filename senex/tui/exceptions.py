"""senex.tui.exceptions — TUI-only named exceptions (M9 Task 9.4).

Per conventions §4: named exceptions only; live at module top-level.
The Textual app boundary catches ``WidgetRenderError`` and surfaces it
via the ErrorBanner; it MUST NEVER propagate out of the TUI.
"""
from __future__ import annotations


class WidgetRenderError(Exception):
    """A widget's ``on_event`` could not reconcile state.

    Caught at the Textual app boundary
    (``SenexApp._handle_widget_error``) and surfaced as an ErrorBanner
    update; NEVER propagates out of the TUI.
    """


class ReplayError(Exception):
    """``senex view`` could not parse a recorded ``events.jsonl`` line
    or the file is invalid (corrupt JSON, unknown discriminator,
    missing file).
    """


__all__ = ["ReplayError", "WidgetRenderError"]
