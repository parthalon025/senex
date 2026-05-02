"""Tests for HeadlessSubscriber (M9 Task 9.2)."""
from __future__ import annotations

from datetime import datetime, UTC
from io import StringIO

import pytest

from senex.events import (
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
from senex.subscribers.headless_subscriber import HeadlessSubscriber


def _now() -> datetime:
    return datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_headless_drop_policy_is_drop_oldest() -> None:
    sub = HeadlessSubscriber()
    assert sub.drop_policy == "drop_oldest"
    assert sub.name == "headless"
    assert sub.queue_capacity == 1024


@pytest.mark.asyncio
async def test_headless_prints_one_line_per_file_complete() -> None:
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    await sub.consume(
        RunStart(
            ts=_now(),
            run_id="r1",
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
    )
    for i in (1, 2, 3):
        await sub.consume(
            FileStart(ts=_now(), run_id="r1", path=f"src/f{i}.py", idx=i, total=3)
        )
        await sub.consume(
            FileComplete(
                ts=_now(),
                run_id="r1",
                path=f"src/f{i}.py",
                finding_counts={"high": 1, "medium": 1, "low": 0, "healthy": 0},
            )
        )
    out = buf.getvalue()
    file_lines = [line for line in out.splitlines() if "auditing" in line]
    assert len(file_lines) == 3
    for i in (1, 2, 3):
        assert f"[{i}/3] auditing src/f{i}.py" in out


@pytest.mark.asyncio
async def test_headless_suppresses_tick_events() -> None:
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    for i in range(100):
        await sub.consume(
            ThinkingTick(
                ts=_now(),
                run_id="r1",
                path="src/x.py",
                tokens_so_far=i,
                delta_since_last_tick=1,
            )
        )
        await sub.consume(
            OutputTick(
                ts=_now(),
                run_id="r1",
                path="src/x.py",
                tokens_so_far=i,
                delta_since_last_tick=1,
            )
        )
    assert buf.getvalue() == ""


@pytest.mark.asyncio
async def test_headless_run_complete_prints_final_summary() -> None:
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    await sub.consume(
        RunComplete(
            ts=_now(),
            run_id="r1",
            duration_seconds=754.0,
            totals={
                "files_audited": 87,
                "files_errored": 0,
                "files_skipped": 2,
                "high": 4,
                "medium": 17,
                "low": 22,
                "healthy": 44,
            },
            exit_status=0,
        )
    )
    out = buf.getvalue()
    assert "Run complete" in out
    assert "12m" in out  # duration formatted
    assert "Files: 87 audited" in out
    assert "HIGH=4" in out
    assert "MEDIUM=17" in out
    assert "LOW=22" in out
    assert "Exit status: success" in out


@pytest.mark.asyncio
async def test_headless_file_error_prints_error_line() -> None:
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    await sub.consume(
        FileError(
            ts=_now(),
            run_id="r1",
            path="src/bad.py",
            phase="thinking",
            error_kind="schema_invalid",
            error_message="boom",
        )
    )
    assert "ERROR src/bad.py: schema_invalid" in buf.getvalue()


@pytest.mark.asyncio
async def test_headless_handles_unicode_paths() -> None:
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    await sub.consume(
        FileStart(ts=_now(), run_id="r1", path="src/héllo.py", idx=1, total=1)
    )
    await sub.consume(
        FileComplete(
            ts=_now(),
            run_id="r1",
            path="src/héllo.py",
            finding_counts={"high": 0, "medium": 0, "low": 0, "healthy": 1},
        )
    )
    assert "héllo.py" in buf.getvalue()


@pytest.mark.asyncio
async def test_headless_run_complete_nonzero_exit_status_label() -> None:
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    await sub.consume(
        RunComplete(
            ts=_now(),
            run_id="r1",
            duration_seconds=10.0,
            totals={"files_audited": 1, "files_errored": 1, "files_skipped": 0},
            exit_status=1,
        )
    )
    assert "Exit status: partial" in buf.getvalue()


@pytest.mark.asyncio
async def test_headless_renders_model_load_waiting_block() -> None:
    """M11: ``ModelLoadWaiting`` prints the manual-load instructions."""
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    await sub.consume(
        ModelLoadWaiting(
            ts=_now(),
            run_id="r1",
            model_id="google/gemma-4-26b-a4b",
            timeout_seconds=600,
            reason="lms load rejected: insufficient RAM (resource guardrail)",
        )
    )
    out = buf.getvalue()
    assert "auto_load failed" in out
    assert "Waiting up to 600s" in out
    assert "google/gemma-4-26b-a4b" in out
    assert "lms load google/gemma-4-26b-a4b" in out
    assert "Press Ctrl+C to abort" in out


@pytest.mark.asyncio
async def test_headless_renders_model_load_still_waiting_heartbeat() -> None:
    """M11: heartbeat lines surface elapsed/remaining for live progress."""
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    await sub.consume(
        ModelLoadStillWaiting(
            ts=_now(),
            run_id="r1",
            model_id="m",
            elapsed_seconds=120,
            remaining_seconds=480,
        )
    )
    out = buf.getvalue()
    assert "Still waiting" in out
    assert "120s elapsed" in out
    assert "480s remaining" in out


@pytest.mark.asyncio
async def test_headless_renders_model_load_complete_after_wait() -> None:
    """M11: success after manual load prints a confirmation."""
    buf = StringIO()
    sub = HeadlessSubscriber(stream=buf)
    await sub.consume(
        ModelLoadCompleteAfterWait(
            ts=_now(),
            run_id="r1",
            model_id="m",
            fingerprint="sha256:abc",
            wait_seconds=42,
        )
    )
    out = buf.getvalue()
    assert "Model loaded successfully" in out
    assert "42s wait" in out
