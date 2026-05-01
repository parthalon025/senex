"""Tests for JSONLinesHeadlessSubscriber (F-13).

Verifies that:
  1. Every event type round-trips through ``model_dump(mode="json")``.
  2. Subscriber stdout is parseable as JSONL.
  3. Tick events are dropped (drop_oldest contract).
  4. Drop policy + name + queue_capacity match the Subscriber Protocol.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from typing import Any

import pytest

from senex.events import (
    ALL_EVENT_TYPES,
    AggregateComplete,
    AggregateStart,
    BaseEvent,
    CompactionComplete,
    CompactionError,
    CompactionTriggered,
    CrosscutComplete,
    CrosscutStart,
    DiscoveryComplete,
    DiscoveryStart,
    FileComplete,
    FileContextBuilt,
    FileError,
    FileLLMCall,
    FileStart,
    FindingSummary,
    GraphContextUnavailable,
    MemoryInjected,
    ModelFingerprintChanged,
    ModelLoadComplete,
    ModelLoadCompleteAfterWait,
    ModelLoadFailed,
    ModelLoadRequested,
    ModelLoadStarted,
    ModelLoadStillWaiting,
    ModelLoadWaiting,
    ModelUnloadComplete,
    ModelUnloadFailed,
    ModelUnloadSkipped,
    ModelUnloadStarted,
    OutputComplete,
    OutputStarted,
    OutputTick,
    PreflightWarning,
    RunComplete,
    RunLockAcquired,
    RunLockReleased,
    RunStart,
    SkillsInjected,
    SuspiciousEmptyFinding,
    SymlinkSkipped,
    ThinkingComplete,
    ThinkingStarted,
    ThinkingTick,
    ToolBudgetExhausted,
    ToolCall,
    ToolError,
    ToolResult,
)
from senex.subscribers.jsonl_headless_subscriber import JSONLinesHeadlessSubscriber


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _build_one(cls: type[BaseEvent]) -> BaseEvent:
    """Construct a minimal valid instance of every event type.

    The big switch mirrors :data:`ALL_EVENT_TYPES`. Each branch supplies
    the smallest set of required fields so ``model_dump(mode="json")``
    can be exercised end-to-end.
    """
    base: dict[str, Any] = {"ts": _now(), "run_id": "r1"}
    if cls is RunStart:
        return RunStart(
            **base,
            repo="r",
            audit_dir="d",
            model="m",
            lens="correctness",
            lens_version="1",
            config_hash="c",
            prompt_hash="p",
            model_fingerprint="f",
            started_at=_now(),
        )
    if cls is PreflightWarning:
        return PreflightWarning(**base, check="x", message="y")
    if cls is DiscoveryStart:
        return DiscoveryStart(**base, repo="r")
    if cls is DiscoveryComplete:
        return DiscoveryComplete(
            **base, file_count=2, skipped=[("a.py", "ignored")]
        )
    if cls is SymlinkSkipped:
        return SymlinkSkipped(**base, path="a", target="b", reason="r")
    if cls is SuspiciousEmptyFinding:
        return SuspiciousEmptyFinding(**base, path="a", loc="L1", reason="r")
    if cls is FileStart:
        return FileStart(**base, path="a.py", idx=1, total=1)
    if cls is FileContextBuilt:
        return FileContextBuilt(**base, path="a.py", graph_context_tokens=10)
    if cls is SkillsInjected:
        return SkillsInjected(**base, path="a.py", names=["s1"])
    if cls is MemoryInjected:
        return MemoryInjected(**base, path="a.py", finding_count=2)
    if cls is GraphContextUnavailable:
        return GraphContextUnavailable(**base, path="a.py", reason="cli failed")
    if cls is FileLLMCall:
        return FileLLMCall(**base, path="a.py", prompt_tokens=100)
    if cls is ThinkingStarted:
        return ThinkingStarted(**base, path="a.py")
    if cls is ThinkingTick:
        return ThinkingTick(
            **base, path="a.py", tokens_so_far=10, delta_since_last_tick=5
        )
    if cls is ThinkingComplete:
        return ThinkingComplete(
            **base, path="a.py", total_thinking_tokens=20, latency_ms=100
        )
    if cls is OutputStarted:
        return OutputStarted(**base, path="a.py")
    if cls is OutputTick:
        return OutputTick(
            **base, path="a.py", tokens_so_far=10, delta_since_last_tick=5
        )
    if cls is OutputComplete:
        return OutputComplete(
            **base, path="a.py", total_output_tokens=20, latency_ms=100
        )
    if cls is FileComplete:
        return FileComplete(
            **base,
            path="a.py",
            finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 1},
            last_finding_summary=FindingSummary(
                priority="healthy", title="t", location=None
            ),
        )
    if cls is FileError:
        return FileError(
            **base, path="a.py", phase="thinking", error_kind="x", error_message="y"
        )
    if cls is ToolCall:
        return ToolCall(
            **base, path="a.py", tool_name="grep", tool_input="x", call_id="c1"
        )
    if cls is ToolResult:
        return ToolResult(
            **base,
            path="a.py",
            tool_name="grep",
            call_id="c1",
            result_tokens=5,
            latency_ms=10,
            truncated=False,
        )
    if cls is ToolError:
        return ToolError(
            **base,
            path="a.py",
            tool_name="grep",
            call_id="c1",
            kind="timeout",
            error_message="boom",
        )
    if cls is ToolBudgetExhausted:
        return ToolBudgetExhausted(**base, path="a.py", calls_made=8)
    if cls is CompactionTriggered:
        return CompactionTriggered(
            **base, path="a.py", message_tokens_before=1000, threshold=900
        )
    if cls is CompactionComplete:
        return CompactionComplete(
            **base, path="a.py", message_tokens_after=400, kept_findings=2
        )
    if cls is CompactionError:
        return CompactionError(**base, path="a.py", error_kind="schema")
    if cls is CrosscutStart:
        return CrosscutStart(**base)
    if cls is CrosscutComplete:
        return CrosscutComplete(**base, theme_count=3)
    if cls is AggregateStart:
        return AggregateStart(**base)
    if cls is AggregateComplete:
        return AggregateComplete(**base, finding_count=10, theme_count=2)
    if cls is RunComplete:
        return RunComplete(
            **base,
            duration_seconds=5.0,
            totals={"files_audited": 1},
            exit_status=0,
        )
    if cls is ModelLoadRequested:
        return ModelLoadRequested(**base, model_id="m", target_fingerprint="f")
    if cls is ModelLoadStarted:
        return ModelLoadStarted(**base, model_id="m")
    if cls is ModelLoadComplete:
        return ModelLoadComplete(
            **base, model_id="m", duration_seconds=1.0, fingerprint="f"
        )
    if cls is ModelLoadFailed:
        return ModelLoadFailed(
            **base, model_id="m", error_kind="x", error_message="y"
        )
    if cls is ModelLoadWaiting:
        return ModelLoadWaiting(
            **base, model_id="m", timeout_seconds=600, reason="r"
        )
    if cls is ModelLoadStillWaiting:
        return ModelLoadStillWaiting(
            **base, model_id="m", elapsed_seconds=10, remaining_seconds=590
        )
    if cls is ModelLoadCompleteAfterWait:
        return ModelLoadCompleteAfterWait(
            **base, model_id="m", fingerprint="f", wait_seconds=5
        )
    if cls is ModelUnloadStarted:
        return ModelUnloadStarted(**base, model_id="m")
    if cls is ModelUnloadComplete:
        return ModelUnloadComplete(**base, model_id="m", duration_seconds=1.0)
    if cls is ModelUnloadSkipped:
        return ModelUnloadSkipped(**base, model_id="m", reason="not_loaded_by_us")
    if cls is ModelUnloadFailed:
        return ModelUnloadFailed(
            **base, model_id="m", error_kind="x", error_message="y"
        )
    if cls is ModelFingerprintChanged:
        return ModelFingerprintChanged(
            **base,
            path="a.py",
            expected_fingerprint="e",
            observed_fingerprint="o",
        )
    if cls is RunLockAcquired:
        return RunLockAcquired(**base, model_fingerprint="f", holder_count=1)
    if cls is RunLockReleased:
        return RunLockReleased(**base, model_fingerprint="f", remaining_holders=0)
    raise NotImplementedError(f"no fixture for {cls.__name__}")


def test_subscriber_protocol_attributes() -> None:
    sub = JSONLinesHeadlessSubscriber()
    assert sub.name == "jsonl_headless"
    assert sub.queue_capacity == 1024
    assert sub.drop_policy == "drop_oldest"


@pytest.mark.asyncio
async def test_drops_thinking_and_output_ticks() -> None:
    buf = StringIO()
    sub = JSONLinesHeadlessSubscriber(stream=buf)
    await sub.consume(_build_one(ThinkingTick))
    await sub.consume(_build_one(OutputTick))
    assert buf.getvalue() == ""


@pytest.mark.asyncio
async def test_emits_one_json_line_per_event() -> None:
    buf = StringIO()
    sub = JSONLinesHeadlessSubscriber(stream=buf)
    await sub.consume(_build_one(RunStart))
    await sub.consume(_build_one(FileStart))
    await sub.consume(_build_one(FileComplete))
    await sub.consume(_build_one(RunComplete))
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    assert len(lines) == 4
    for line in lines:
        # Each line is independently JSON-parseable.
        obj = json.loads(line)
        assert isinstance(obj, dict)
        assert "type" in obj
        assert "ts" in obj
        assert "run_id" in obj


@pytest.mark.asyncio
async def test_every_event_type_round_trips_through_model_dump_mode_json() -> None:
    """F-13 acceptance: every ALL_EVENT_TYPES instance serialises cleanly.

    Iterates the canonical taxonomy and asserts that:
      - ``model_dump(mode="json")`` returns a JSON-serialisable dict
      - The subscriber's stdout for that event is parseable JSONL
      - The parsed dict's ``type`` matches the class name
    """
    failures: list[str] = []
    for cls in ALL_EVENT_TYPES:
        buf = StringIO()
        sub = JSONLinesHeadlessSubscriber(stream=buf)
        try:
            event = _build_one(cls)
        except NotImplementedError:
            failures.append(f"{cls.__name__}: no test fixture")
            continue
        await sub.consume(event)
        # Tick events are intentionally dropped.
        if cls in (ThinkingTick, OutputTick):
            assert buf.getvalue() == ""
            continue
        out = buf.getvalue().strip()
        if not out:
            failures.append(f"{cls.__name__}: subscriber emitted nothing")
            continue
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError as exc:
            failures.append(f"{cls.__name__}: stdout not valid JSON ({exc})")
            continue
        if parsed.get("type") != cls.__name__:
            failures.append(
                f"{cls.__name__}: type field mismatch -> {parsed.get('type')!r}"
            )
    assert not failures, "JSON round-trip failures:\n  " + "\n  ".join(failures)


@pytest.mark.asyncio
async def test_subscriber_stdout_is_parseable_as_jsonl() -> None:
    """A multi-event run produces stdout that is consumable by ``jq``-style tools."""
    buf = StringIO()
    sub = JSONLinesHeadlessSubscriber(stream=buf)
    await sub.consume(_build_one(RunStart))
    await sub.consume(_build_one(FileStart))
    await sub.consume(_build_one(ThinkingTick))  # dropped
    await sub.consume(_build_one(OutputTick))  # dropped
    await sub.consume(_build_one(FileComplete))
    await sub.consume(_build_one(FileError))
    await sub.consume(_build_one(RunComplete))
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    types = [p["type"] for p in parsed]
    # Ticks were filtered out; the five non-tick events appear in order.
    assert types == [
        "RunStart",
        "FileStart",
        "FileComplete",
        "FileError",
        "RunComplete",
    ]


@pytest.mark.asyncio
async def test_unicode_paths_round_trip() -> None:
    buf = StringIO()
    sub = JSONLinesHeadlessSubscriber(stream=buf)
    await sub.consume(
        FileStart(ts=_now(), run_id="r1", path="src/héllo.py", idx=1, total=1)
    )
    line = buf.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["path"] == "src/héllo.py"


@pytest.mark.asyncio
async def test_shutdown_is_a_noop() -> None:
    sub = JSONLinesHeadlessSubscriber()
    await sub.shutdown()
