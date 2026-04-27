"""senex.subscribers — bus consumers (M9 §5.6).

Exports the four always-on subscribers and the ``Subscriber`` Protocol.

Drop-policy table (per §5.6.1):

+----------------------------+------------------+-----------------------------------+
| Subscriber                 | drop_policy       | Behaviour when queue full         |
+============================+==================+===================================+
| ``DiskWriterSubscriber``   | ``"block"``      | Publisher blocks; lossless.       |
| ``HeadlessSubscriber``     | ``"drop_oldest"``| Drops oldest *Tick; phase events  |
|                            |                  | preserved.                        |
| ``MetricsCollectorSubscriber`` | ``"drop_oldest"`` | Same as Headless.            |
| ``TuiSubscriber``          | ``"drop_token_only"`` | Drops ThinkingTick/OutputTick |
|                            |                  | only; non-tick events block.      |
+----------------------------+------------------+-----------------------------------+

The bus dispatcher (``senex.events.EventBus._dispatch``) reads
``subscriber.drop_policy`` and applies the rule when the per-subscriber
queue is full. ``"drop_token_only"`` is a TuiSubscriber-specific shortcut:
drop ONLY ``ThinkingTick`` / ``OutputTick``; for any other event when the
queue is full, raise ``SubscriberQueueFull`` (loud failure).
"""
from __future__ import annotations

from senex.subscribers.base import (
    Subscriber,
    SubscriberQueueFull,
    SubscriberShutdownError,
)
from senex.subscribers.disk_writer import DiskWriterSubscriber
from senex.subscribers.headless_subscriber import HeadlessSubscriber
from senex.subscribers.metrics import Metrics, MetricsCollectorSubscriber
from senex.subscribers.tui_subscriber import TuiSubscriber

__all__ = [
    "DiskWriterSubscriber",
    "HeadlessSubscriber",
    "Metrics",
    "MetricsCollectorSubscriber",
    "Subscriber",
    "SubscriberQueueFull",
    "SubscriberShutdownError",
    "TuiSubscriber",
]
