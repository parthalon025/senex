"""Integration tests for ``senex view`` replay handler (M9 Task 9.9)."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from senex.events import RunComplete
from senex.tui.exceptions import ReplayError
from senex.tui.view import parse_events_jsonl, run_view


_FIXTURES = Path(__file__).parent.parent / "fixtures" / "events"


@pytest.fixture
def audit_dir(tmp_path: Path) -> Path:
    src = _FIXTURES / "recorded_run.jsonl"
    dst = tmp_path / "audit"
    dst.mkdir()
    (dst / "events.jsonl").write_bytes(src.read_bytes())
    return dst


@pytest.fixture
def truncated_audit_dir(tmp_path: Path) -> Path:
    src = _FIXTURES / "recorded_run_truncated.jsonl"
    dst = tmp_path / "audit"
    dst.mkdir()
    (dst / "events.jsonl").write_bytes(src.read_bytes())
    return dst


@pytest.mark.asyncio
async def test_view_parses_recorded_run(audit_dir: Path) -> None:
    events = list(parse_events_jsonl(audit_dir / "events.jsonl"))
    assert len(events) == 6
    assert any(isinstance(e, RunComplete) for e in events)


@pytest.mark.asyncio
async def test_view_replays_events_through_monitor_headless(audit_dir: Path) -> None:
    # Headless replay path: no Textual UI; just validate the parse loop.
    await run_view(audit_dir, speed=1000.0, headless=True)


@pytest.mark.asyncio
async def test_view_replays_events_through_monitor(audit_dir: Path) -> None:
    # Speed=1000 so the test finishes quickly.
    await run_view(audit_dir, speed=1000.0)


@pytest.mark.asyncio
async def test_view_warns_on_truncated_run(truncated_audit_dir: Path) -> None:
    # Should not raise; truncation surfaces via ErrorBanner inside the UI.
    await run_view(truncated_audit_dir, speed=1000.0)


@pytest.mark.asyncio
async def test_view_speed_flag_accelerates_replay(audit_dir: Path) -> None:
    start = time.monotonic()
    await run_view(audit_dir, speed=1000.0, headless=True)
    elapsed = time.monotonic() - start
    # Headless skips UI bootstrapping but still parses; should be < 2s.
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_view_invalid_jsonl_line_raises_named_exception(
    tmp_path: Path,
) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "events.jsonl").write_text(
        "{not valid json}\n", encoding="utf-8"
    )
    with pytest.raises(ReplayError):
        await run_view(audit, speed=1.0, headless=True)


@pytest.mark.asyncio
async def test_view_unknown_event_type_raises_named(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "events.jsonl").write_text(
        '{"type":"DefinitelyNotAnEvent","ts":"2026-01-01T00:00:00Z","seq":0,"run_id":"r","v":1}\n',
        encoding="utf-8",
    )
    with pytest.raises(ReplayError):
        list(parse_events_jsonl(audit / "events.jsonl"))


@pytest.mark.asyncio
async def test_view_missing_events_jsonl_raises_named(tmp_path: Path) -> None:
    with pytest.raises(ReplayError):
        list(parse_events_jsonl(tmp_path / "nonexistent.jsonl"))


@pytest.mark.asyncio
async def test_view_offline_no_lms_call(audit_dir: Path) -> None:
    """Replay must succeed even when LMS is unreachable."""
    from senex.inference_errors import LMSConnectionLost

    async def boom(self) -> object:
        raise LMSConnectionLost("never available")

    with patch(
        "senex.inference_client.LMStudioClient.list_loaded_models", boom
    ):
        await run_view(audit_dir, speed=1000.0, headless=True)


@pytest.mark.asyncio
async def test_view_invalid_speed_raises(audit_dir: Path) -> None:
    with pytest.raises(ReplayError):
        await run_view(audit_dir, speed=0)
