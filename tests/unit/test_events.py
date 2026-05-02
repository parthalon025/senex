"""Tests for senex.events — event taxonomy + bounded EventBus + CommandBus."""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC

import pytest
from pydantic import ValidationError

from senex.events import (
    ALL_EVENT_TYPES,
    BaseEvent,
    Command,
    CommandBus,
    EventBus,
    FileComplete,
    FileError,
    OutputTick,
    RunStart,
    ThinkingTick,
    ToolCall,  # noqa: F401  (imported per plan; verifies the export exists)
    UnknownEventType,  # noqa: F401  (imported per plan; module-level export check)
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def test_event_count_is_at_least_36() -> None:
    # Spec §5.6 declares 36+ event types (35 baseline + GraphContextUnavailable
    # added per R7 reconciliation).
    assert len(ALL_EVENT_TYPES) >= 36


def test_event_runstart_serialization_roundtrip() -> None:
    ev = RunStart(
        v=1, type="RunStart", ts=_now(), seq=0, run_id="01jz3k7b",
        repo="pensiv", audit_dir="/tmp/x", model="g/g", lens="correctness",
        lens_version="1.0.0", config_hash="sha256:abc", prompt_hash="sha256:def",
        model_fingerprint="sha256:ghi", started_at=_now(),
    )
    data = ev.model_dump_json()
    parsed = RunStart.model_validate_json(data)
    assert parsed.run_id == "01jz3k7b"


@pytest.mark.parametrize("event_cls", ALL_EVENT_TYPES)
def test_every_event_subclass_has_type_literal(event_cls: type[BaseEvent]) -> None:
    # Discriminator field MUST be a literal default per §5.6 / conventions §10.
    field = event_cls.model_fields["type"]
    assert field.default == event_cls.__name__


def test_event_unknown_type_raises_validation_error() -> None:
    with pytest.raises((ValidationError, UnknownEventType)):
        BaseEvent.model_validate(
            {"v": 1, "type": "NotARealEvent", "ts": _now().isoformat(),
             "seq": 0, "run_id": "x"}
        )


@pytest.mark.asyncio
async def test_eventbus_subscribe_returns_bounded_queue() -> None:
    bus = EventBus(default_capacity=4)
    q = bus.subscribe("test", capacity=4)
    # Conventions §3: every queue MUST declare an explicit maxsize.
    assert q.maxsize == 4


@pytest.mark.asyncio
async def test_eventbus_publish_assigns_monotonic_seq() -> None:
    bus = EventBus(default_capacity=128)
    q = bus.subscribe("test", capacity=128)
    for _ in range(10):
        await bus.publish(
            FileError(ts=_now(), seq=0, run_id="x", path="a.py",
                      phase="audit", error_kind="boom", error_message="m")
        )
    seqs = [(await q.get()).seq for _ in range(10)]
    assert seqs == list(range(10))


@pytest.mark.asyncio
async def test_eventbus_diskwriter_blocks_publisher_when_full() -> None:
    bus = EventBus(default_capacity=2)
    q = bus.subscribe("DiskWriter", capacity=2)
    # Fill the queue.
    for _ in range(2):
        await bus.publish(
            FileComplete(ts=_now(), seq=0, run_id="x", path="a.py",
                         finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0})
        )
    # Third publish must block. Race it against a 50ms timeout to assert blocking.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            bus.publish(
                FileComplete(ts=_now(), seq=0, run_id="x", path="b.py",
                             finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0})
            ),
            timeout=0.05,
        )
    # Drain to unblock.
    _ = await q.get()


@pytest.mark.asyncio
async def test_eventbus_tui_drops_thinkingtick_when_full() -> None:
    bus = EventBus(default_capacity=2)
    q = bus.subscribe("Tui", capacity=2)
    # Fill the queue with ticks.
    for _ in range(2):
        await bus.publish(ThinkingTick(ts=_now(), seq=0, run_id="x",
                                       path="a.py", tokens_so_far=1, delta_since_last_tick=1))
    # Third tick must be dropped (NOT block).
    await asyncio.wait_for(
        bus.publish(ThinkingTick(ts=_now(), seq=0, run_id="x",
                                 path="a.py", tokens_so_far=2, delta_since_last_tick=1)),
        timeout=0.05,
    )
    # Queue still has only 2 entries.
    assert q.qsize() == 2


@pytest.mark.asyncio
async def test_eventbus_tui_never_drops_runstart_filecomplete_etc() -> None:
    bus = EventBus(default_capacity=1)
    q = bus.subscribe("Tui", capacity=1)
    await bus.publish(ThinkingTick(ts=_now(), seq=0, run_id="x",
                                   path="a.py", tokens_so_far=1, delta_since_last_tick=1))
    # Publishing a non-coalesce-safe event when full MUST block, not drop.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            bus.publish(FileComplete(ts=_now(), seq=0, run_id="x", path="a.py",
                                     finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0})),
            timeout=0.05,
        )
    _ = await q.get()


@pytest.mark.asyncio
async def test_eventbus_metrics_drops_oldest_tick_when_full() -> None:
    bus = EventBus(default_capacity=2)
    q = bus.subscribe("Metrics", capacity=2)
    await bus.publish(OutputTick(ts=_now(), seq=0, run_id="x",
                                 path="a.py", tokens_so_far=1, delta_since_last_tick=1))
    await bus.publish(OutputTick(ts=_now(), seq=0, run_id="x",
                                 path="a.py", tokens_so_far=2, delta_since_last_tick=1))
    await bus.publish(OutputTick(ts=_now(), seq=0, run_id="x",
                                 path="a.py", tokens_so_far=3, delta_since_last_tick=1))
    remaining = [(await q.get()).tokens_so_far for _ in range(q.qsize())]
    # Oldest (1) was dropped.
    assert 1 not in remaining


@pytest.mark.asyncio
async def test_commandbus_publishes_command_to_subscribers() -> None:
    cb = CommandBus(capacity=8)
    q = cb.subscribe()
    assert q.maxsize == 8
    await cb.publish(Command(type="Pause", target=None, ts=_now()))
    cmd = await q.get()
    assert cmd.type == "Pause"


@pytest.mark.asyncio
async def test_eventbus_subscribe_local_only_fires_on_matching_types() -> None:
    # R9: subscribe_local fires only when the published event matches one
    # of the registered types.
    bus = EventBus(default_capacity=8)
    seen: list[BaseEvent] = []

    async def cb(ev: BaseEvent) -> None:
        seen.append(ev)

    handle = bus.subscribe_local(
        "test", (FileError,), cb,
    )
    # Matching type fires.
    await bus.publish(
        FileError(ts=_now(), seq=0, run_id="x", path="a.py",
                  phase="audit", error_kind="boom", error_message="m")
    )
    # Non-matching type does NOT fire.
    await bus.publish(
        FileComplete(ts=_now(), seq=0, run_id="x", path="a.py",
                     finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 0})
    )
    handle.unsubscribe()
    assert len(seen) == 1
    assert isinstance(seen[0], FileError)


def test_events_module_has_nonempty_docstring() -> None:
    from senex import events as ev_mod
    assert ev_mod.__doc__ and ev_mod.__doc__.strip() != ""
