"""senex.subscribers.base — Subscriber protocol + named exceptions (M9 Task 9.1).

Implements spec §5.6 (event bus + subscribers) and §5.6.1 (per-subscriber
drop policies). Per conventions §4: named exceptions only; per §10:
strict typing on protocol attrs.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from senex.events import BaseEvent


class SubscriberQueueFull(Exception):
    """Raised when a non-tick event is enqueued onto a full ``"drop_token_only"``
    queue (TuiSubscriber, see §5.6.1). DiskWriter NEVER raises this — it
    blocks the publisher instead.
    """


class SubscriberShutdownError(Exception):
    """Raised when a subscriber's ``shutdown()`` coroutine fails to flush
    pending state before the timeout (see §5.6.1).
    """


@runtime_checkable
class Subscriber(Protocol):
    """Bus consumer (spec §5.6).

    A Subscriber is a passive bus consumer: it receives events via
    ``consume(event)`` and must finalize any buffered state in
    ``shutdown()``.

    The ``drop_policy`` attribute is read by the bus dispatcher to choose
    the back-pressure rule when the per-subscriber queue is full.
    """

    name: str
    queue_capacity: int
    drop_policy: Literal["block", "drop_oldest", "drop_token_only"]

    async def consume(self, event: BaseEvent) -> None:
        """Process a single event delivered by the bus."""
        ...

    async def shutdown(self) -> None:
        """Flush any buffered state. Called once at run end (or abort)."""
        ...


__all__ = [
    "Subscriber",
    "SubscriberQueueFull",
    "SubscriberShutdownError",
]
