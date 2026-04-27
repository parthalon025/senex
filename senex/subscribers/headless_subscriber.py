"""senex.subscribers.headless_subscriber — stdout progress lines (M9 Task 9.2).

Implements spec §5.6.1 (drop-oldest tick policy) + §5.7 progress line format.
Per conventions §3 (no sync I/O on async paths) writes go through
``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import sys
from typing import IO, Final, Literal

from senex.events import (
    BaseEvent,
    FileComplete,
    FileError,
    FileStart,
    ModelLoadCompleteAfterWait,
    ModelLoadStillWaiting,
    ModelLoadWaiting,
    OutputTick,
    RunComplete,
    RunStart,
    ThinkingTick,
)

_FORMAT_FILE_LINE: Final[str] = (
    "[{idx}/{total}] auditing {path} — {n} findings "
    "(high={h}, medium={m}, low={l})"
)


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    return f"{minutes}m{sec:02d}s"


def _exit_status_label(code: int) -> str:
    return {
        0: "success",
        1: "partial",
        2: "config_error",
        3: "external_error",
        130: "interrupted",
    }.get(code, f"code_{code}")


class HeadlessSubscriber:
    """Concise stdout progress + final summary (spec §5.7).

    Suppresses ``*Tick`` events; prints one line per ``FileComplete`` /
    ``FileError`` / ``RunStart`` / ``RunComplete``.
    """

    name: str = "headless"
    queue_capacity: int = 1024
    drop_policy: Literal["block", "drop_oldest", "drop_token_only"] = "drop_oldest"

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream: IO[str] = stream if stream is not None else sys.stdout
        # Track per-file index for FileStart -> FileComplete pairing.
        self._current_idx: int = 0
        self._current_total: int = 0

    async def consume(self, event: BaseEvent) -> None:
        # Drop ticks (drop_oldest semantics applied at bus dispatch; this is
        # belt-and-braces for safety).
        if isinstance(event, (ThinkingTick, OutputTick)):
            return
        line = self._format_event(event)
        if line is None:
            return
        await asyncio.to_thread(self._write, line)

    def _format_event(self, event: BaseEvent) -> str | None:
        if isinstance(event, RunStart):
            return f"--- senex audit: {event.repo} (run {event.run_id[:8]}) ---"
        if isinstance(event, FileStart):
            self._current_idx = event.idx
            self._current_total = event.total
            return None
        if isinstance(event, FileComplete):
            counts = event.finding_counts
            n = sum(int(counts.get(k, 0)) for k in ("high", "medium", "low"))
            return _FORMAT_FILE_LINE.format(
                idx=self._current_idx or 0,
                total=self._current_total or 0,
                path=event.path,
                n=n,
                h=counts.get("high", 0),
                m=counts.get("medium", 0),
                l=counts.get("low", 0),
            )
        if isinstance(event, FileError):
            return f"ERROR {event.path}: {event.error_kind} — {event.error_message}"
        if isinstance(event, ModelLoadWaiting):
            # Multi-line block: the user needs to know auto_load failed AND
            # what to do. Returning a single string keeps the subscriber's
            # one-write-per-event contract intact.
            return (
                f"[lifecycle] auto_load failed: {event.reason}\n"
                f"[lifecycle] Waiting up to {event.timeout_seconds}s for "
                f"model `{event.model_id}` to be loaded manually.\n"
                f"[lifecycle] Open LM Studio and load the model, or run: "
                f"lms load {event.model_id}\n"
                f"[lifecycle] Press Ctrl+C to abort."
            )
        if isinstance(event, ModelLoadStillWaiting):
            return (
                f"[lifecycle] Still waiting ({event.elapsed_seconds}s elapsed, "
                f"{event.remaining_seconds}s remaining)..."
            )
        if isinstance(event, ModelLoadCompleteAfterWait):
            return (
                f"[lifecycle] Model loaded successfully after "
                f"{event.wait_seconds}s wait."
            )
        if isinstance(event, RunComplete):
            return self._format_run_complete(event)
        return None

    def _format_run_complete(self, event: RunComplete) -> str:
        dur = _format_duration(event.duration_seconds)
        t = event.totals
        audited = t.get("files_audited", 0)
        errored = t.get("files_errored", 0)
        skipped = t.get("files_skipped", 0)
        high = t.get("high", 0)
        medium = t.get("medium", 0)
        low = t.get("low", 0)
        healthy = t.get("healthy", 0)
        return (
            "\n--- Run complete ---\n"
            f"Duration: {dur}\n"
            f"Files: {audited} audited, {errored} errors, {skipped} skipped\n"
            f"Findings: HIGH={high} MEDIUM={medium} LOW={low} HEALTHY={healthy}\n"
            f"Exit status: {_exit_status_label(event.exit_status)}"
        )

    def _write(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()

    async def shutdown(self) -> None:
        # Stdout is owned by the process; nothing to close.
        return None


__all__ = ["HeadlessSubscriber"]
