"""senex.subscribers.jsonl_headless_subscriber - JSON-Lines stdout subscriber.

Implements the ``--json`` contract for ``senex audit --no-tui --json`` (F-13).
Emits one JSON object per line on stdout - the same NDJSON format the
``DiskWriterSubscriber`` writes to ``events.jsonl`` - so downstream tools
(``jq``, log shippers, CI parsers) can consume the stream directly.

Drop policy mirrors :class:`HeadlessSubscriber` (``drop_oldest`` for ticks);
phase events are preserved. Per conventions s.3 (no sync I/O on async paths)
writes go through ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import IO, Literal

from senex.events import (
    BaseEvent,
    OutputTick,
    ThinkingTick,
)

log = logging.getLogger(__name__)


class JSONLinesHeadlessSubscriber:
    """Stdout JSON-Lines subscriber for ``audit --no-tui --json`` (F-13).

    Each event is serialised via ``event.model_dump(mode="json")`` and
    emitted as one JSON object per line (``json.dumps`` with no indent).
    Per spec s.5.6.1 the tick events are dropped at the subscriber level
    too (belt-and-braces against bus dispatch ordering).

    Attributes:
        name: ``"jsonl_headless"``.
        queue_capacity: 1024 (per conventions s.3).
        drop_policy: ``"drop_oldest"`` - same as :class:`HeadlessSubscriber`.
    """

    name: str = "jsonl_headless"
    queue_capacity: int = 1024
    drop_policy: Literal["block", "drop_oldest", "drop_token_only"] = "drop_oldest"

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream: IO[str] = stream if stream is not None else sys.stdout

    async def consume(self, event: BaseEvent) -> None:
        # Drop ticks (drop_oldest semantics applied at bus dispatch; this
        # is belt-and-braces in case the bus dispatched a tick anyway).
        if isinstance(event, (ThinkingTick, OutputTick)):
            return
        try:
            payload = event.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 - never propagate to bus.
            log.error(
                "jsonl_headless: model_dump failed for %s: %s",
                type(event).__name__,
                exc,
            )
            return
        try:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            log.error(
                "jsonl_headless: json.dumps failed for %s: %s",
                type(event).__name__,
                exc,
            )
            return
        await asyncio.to_thread(self._write, line)

    def _write(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()

    async def shutdown(self) -> None:
        # Stdout is owned by the process; nothing to close.
        return None


__all__ = ["JSONLinesHeadlessSubscriber"]
