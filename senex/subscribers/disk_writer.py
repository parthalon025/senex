"""senex.subscribers.disk_writer — write-ahead persistence (M9 Task 9.1).

Implements spec §5.6.1 (DiskWriter "block" policy — events.jsonl MUST be
lossless). Per conventions §3 (no sync I/O on async paths) writes happen
through ``asyncio.to_thread``; per §8 (atomic / append-only NDJSON) each
event is one ``write+flush+fsync`` so a crash truncates at line boundaries.

The dispatcher in ``senex.events.EventBus._dispatch`` enforces the
"block" policy when a Subscriber is registered under the conventional
name ``"DiskWriter"``. This module is a passive consumer: a separate
pump task (or test harness) drains the bounded queue and feeds each
event into ``DiskWriterSubscriber.consume``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import IO, Final, Literal

from senex.events import BaseEvent
from senex.subscribers.base import SubscriberShutdownError

log = logging.getLogger(__name__)

_EVENTS_FILENAME: Final[str] = "events.jsonl"


class DiskWriterSubscriber:
    """Append-only NDJSON writer for ``events.jsonl`` (spec §5.6.1).

    Attributes:
        name: ``"disk_writer"``.
        queue_capacity: 1024 (per conventions §3).
        drop_policy: ``"block"`` — publisher MUST suspend when the bus
            queue for this subscriber is full. The dispatcher reads this
            to apply the rule; the subscriber itself never drops.
    """

    name: str = "disk_writer"
    queue_capacity: int = 1024
    drop_policy: Literal["block", "drop_oldest", "drop_token_only"] = "block"

    def __init__(self, audit_dir: Path) -> None:
        audit_dir.mkdir(parents=True, exist_ok=True)
        self._path = audit_dir / _EVENTS_FILENAME
        # UTF-8 without BOM, LF endings — open in binary append + write
        # encoded bytes ourselves to keep platform-uniform line endings.
        self._fh: IO[bytes] | None = open(  # noqa: SIM115 — closed in shutdown()
            self._path, "ab", buffering=0
        )

    async def consume(self, event: BaseEvent) -> None:
        """Append one NDJSON line for ``event`` and fsync."""
        if self._fh is None:
            raise SubscriberShutdownError(
                "DiskWriterSubscriber.consume after shutdown()"
            )
        line = event.model_dump_json() + "\n"
        payload = line.encode("utf-8")
        await asyncio.to_thread(self._write_and_fsync, payload)

    def _write_and_fsync(self, payload: bytes) -> None:
        fh = self._fh
        if fh is None:
            raise SubscriberShutdownError(
                "DiskWriterSubscriber._write_and_fsync after shutdown()"
            )
        fh.write(payload)
        # Binary unbuffered: explicit flush is a no-op but kept for parity.
        fh.flush()
        os.fsync(fh.fileno())

    async def shutdown(self) -> None:
        """Close the file. Idempotent; subsequent calls no-op."""
        fh = self._fh
        if fh is None:
            return
        self._fh = None
        try:
            await asyncio.to_thread(fh.close)
        except OSError as exc:  # noqa: BLE001 — re-raised below as named.
            log.error("disk_writer close failed: %s", exc)
            raise SubscriberShutdownError(str(exc)) from exc


__all__ = ["DiskWriterSubscriber"]
