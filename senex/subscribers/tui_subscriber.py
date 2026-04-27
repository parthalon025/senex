"""senex.subscribers.tui_subscriber — 10 Hz rate-limited TUI fan-out (M9 Task 9.7).

Implements spec §5.6.1 (drop_token_only policy: NEVER drops boundary
events) and §5.7 (10 Hz refresh — coalesce ticks into one widget update
per 100 ms).

Architecture:
  * ``consume(event)`` enqueues to a bounded internal queue. Boundary
    events (anything that's not ``ThinkingTick`` / ``OutputTick``) are
    enqueued unconditionally; if the queue is full, the oldest tick is
    evicted to make room.
  * ``_pump`` task drains the queue every ``1 / rate_limit_hz`` seconds
    and dispatches each event to every registered widget. Widget
    exceptions are caught and logged; they NEVER bubble out.
  * ``shutdown()`` flushes pending events synchronously, cancels the
    pump, and returns. The cancellation surfaces as
    ``asyncio.CancelledError`` inside ``_pump``; per conventions §3
    cancellation is re-raised after cleanup.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Literal, Protocol

from senex.events import BaseEvent, OutputTick, ThinkingTick

log = logging.getLogger(__name__)


class _Widget(Protocol):
    def handle_audit_event(self, event: BaseEvent) -> None: ...


def _is_tick(event: BaseEvent) -> bool:
    return isinstance(event, (ThinkingTick, OutputTick))


class TuiSubscriber:
    """Rate-limited bus consumer for the Textual TUI (spec §5.6.1)."""

    name: str = "tui"
    queue_capacity: int = 1024
    drop_policy: Literal["block", "drop_oldest", "drop_token_only"] = (
        "drop_token_only"
    )

    def __init__(self, rate_limit_hz: float = 10.0) -> None:
        if rate_limit_hz <= 0:
            raise ValueError("rate_limit_hz must be > 0")
        self._rate_limit_hz = rate_limit_hz
        self._tick_period = 1.0 / rate_limit_hz
        # Internal bounded queue with manual drop logic.
        self._queue: deque[BaseEvent] = deque()
        self._widgets: list[_Widget] = []
        self._pump_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.tick_drop_count: int = 0
        self.batch_count: int = 0

    def register_widget(self, widget: _Widget) -> None:
        self._widgets.append(widget)

    async def start(self) -> None:
        """Start the rate-limit pump task. Idempotent."""
        if self._pump_task is None:
            self._stop.clear()
            self._pump_task = asyncio.create_task(self._pump())

    async def consume(self, event: BaseEvent) -> None:
        """Enqueue an event for the rate-limited pump.

        Boundary events are always enqueued; ticks may be dropped if the
        queue is full to preserve the boundary-event throughput.
        """
        if _is_tick(event):
            if len(self._queue) >= self.queue_capacity:
                self.tick_drop_count += 1
                return
            self._queue.append(event)
            return
        # Boundary event: evict the oldest tick if necessary to fit.
        while len(self._queue) >= self.queue_capacity:
            for i, e in enumerate(self._queue):
                if _is_tick(e):
                    del self._queue[i]
                    self.tick_drop_count += 1
                    break
            else:
                # No tick to evict; queue is saturated with boundary
                # events. Block briefly to allow the pump to drain.
                await asyncio.sleep(self._tick_period)
        self._queue.append(event)

    async def flush_now(self) -> None:
        """Drain pending events to widgets immediately (test helper)."""
        await self._dispatch_batch()

    async def _pump(self) -> None:
        try:
            while not self._stop.is_set():
                await asyncio.sleep(self._tick_period)
                if self._queue:
                    await self._dispatch_batch()
        except asyncio.CancelledError:
            log.debug("TuiSubscriber._pump cancelled")
            raise

    async def _dispatch_batch(self) -> None:
        if not self._queue:
            return
        # Coalesce: keep all boundary events; collapse consecutive ticks
        # of the same type+path to the latest seen.
        batch = list(self._queue)
        self._queue.clear()
        coalesced = self._coalesce(batch)
        self.batch_count += 1
        for evt in coalesced:
            for w in self._widgets:
                try:
                    w.handle_audit_event(evt)
                except Exception as exc:  # noqa: BLE001 — widget errors logged.
                    log.error("widget %r handle_audit_event raised: %s", w, exc)

    def _coalesce(self, events: list[BaseEvent]) -> list[BaseEvent]:
        """Drop consecutive ticks of the same type+path, keeping the latest."""
        out: list[BaseEvent] = []
        for evt in events:
            if (
                _is_tick(evt)
                and out
                and type(out[-1]) is type(evt)
                and getattr(out[-1], "path", None) == getattr(evt, "path", None)
            ):
                out[-1] = evt
            else:
                out.append(evt)
        return out

    async def shutdown(self) -> None:
        """Flush remaining events; cancel pump."""
        # Flush synchronously first.
        await self._dispatch_batch()
        if self._pump_task is not None and not self._pump_task.done():
            self._stop.set()
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None


__all__ = ["TuiSubscriber"]
