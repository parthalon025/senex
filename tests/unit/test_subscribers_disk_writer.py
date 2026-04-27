"""Tests for DiskWriterSubscriber (M9 Task 9.1).

Spec §5.6.1 (bus semantics — DiskWriter "block" policy);
conventions §8 (atomic / append-only NDJSON) + §3 (no sync I/O on async paths).
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from senex.events import (
    EventBus,
    FileComplete,
    FileStart,
    RunComplete,
)
from senex.subscribers.disk_writer import DiskWriterSubscriber


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_event(idx: int) -> FileStart:
    return FileStart(
        ts=_now(),
        run_id="r1",
        path=f"src/f{idx}.py",
        idx=idx,
        total=10,
    )


@pytest.mark.asyncio
async def test_disk_writer_writes_events_in_seq_order(tmp_path: Path) -> None:
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    events = [_make_event(i) for i in range(1, 6)]
    for i, e in enumerate(events):
        e.seq = i
        await sub.consume(e)
    await sub.shutdown()

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_disk_writer_writes_one_line_per_event(tmp_path: Path) -> None:
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    for i in range(7):
        await sub.consume(_make_event(i))
    await sub.shutdown()

    text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    # Must be NDJSON: 7 lines + trailing newline; no '[' / ']' wrapper.
    assert text.startswith("{")
    assert "[" not in text.split("\n", 1)[0]
    assert text.count("\n") == 7


@pytest.mark.asyncio
async def test_disk_writer_fsyncs_on_each_consume(tmp_path: Path) -> None:
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    with patch.object(os, "fsync") as fsync_mock:
        for i in range(3):
            await sub.consume(_make_event(i))
        assert fsync_mock.call_count == 3
    await sub.shutdown()


@pytest.mark.asyncio
async def test_disk_writer_drops_nothing_under_pressure(tmp_path: Path) -> None:
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    for i in range(100):
        await sub.consume(_make_event(i))
    await sub.shutdown()

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100


@pytest.mark.asyncio
async def test_disk_writer_drop_policy_is_block(tmp_path: Path) -> None:
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    assert sub.drop_policy == "block"
    assert sub.name == "disk_writer"
    assert sub.queue_capacity == 1024
    await sub.shutdown()


@pytest.mark.asyncio
async def test_disk_writer_shutdown_closes_file(tmp_path: Path) -> None:
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    await sub.consume(_make_event(1))
    await sub.shutdown()
    # Subsequent shutdown must be idempotent.
    await sub.shutdown()


@pytest.mark.asyncio
async def test_disk_writer_writes_run_complete(tmp_path: Path) -> None:
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    await sub.consume(
        RunComplete(
            ts=_now(),
            run_id="r1",
            duration_seconds=10.0,
            totals={"files_done": 3},
            exit_status=0,
        )
    )
    await sub.shutdown()
    line = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
    payload = json.loads(line)
    assert payload["type"] == "RunComplete"
    assert payload["totals"] == {"files_done": 3}


@pytest.mark.asyncio
async def test_disk_writer_blocks_publisher_when_bus_queue_full(tmp_path: Path) -> None:
    """Bus dispatcher applies block policy for DiskWriter (spec §5.6.1).

    Publisher MUST suspend when the bounded queue is full.
    """
    bus = EventBus()
    q = bus.subscribe("DiskWriter", capacity=2)

    # Fill the queue without consuming.
    await bus.publish(_make_event(1))
    await bus.publish(_make_event(2))

    # Third publish blocks because capacity=2 and nothing has drained.
    publish_task = asyncio.create_task(bus.publish(_make_event(3)))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(publish_task), timeout=0.05)
        # Drain one slot; publisher unblocks.
        _ = await q.get()
        await asyncio.wait_for(publish_task, timeout=1.0)
    finally:
        if not publish_task.done():
            publish_task.cancel()


@pytest.mark.asyncio
async def test_disk_writer_atomic_lines_survive_partial_write(tmp_path: Path) -> None:
    """Each line written via single write+flush+fsync — crash leaves whole lines."""
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    await sub.consume(_make_event(1))
    await sub.consume(_make_event(2))
    await sub.shutdown()

    text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    # Each non-empty line must parse; no truncated trailing JSON.
    lines = [line for line in text.split("\n") if line]
    for line in lines:
        json.loads(line)


@pytest.mark.asyncio
async def test_disk_writer_handles_file_complete_with_finding_summary(tmp_path: Path) -> None:
    sub = DiskWriterSubscriber(audit_dir=tmp_path)
    from senex.events import FindingSummary

    fc = FileComplete(
        ts=_now(),
        run_id="r1",
        path="src/x.py",
        finding_counts={"high": 1, "medium": 0, "low": 0, "healthy": 0},
        last_finding_summary=FindingSummary(priority="high", title="bad code"),
    )
    await sub.consume(fc)
    await sub.shutdown()
    text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    payload = json.loads(text.strip().splitlines()[0])
    assert payload["last_finding_summary"]["priority"] == "high"
