"""``CrosscutPhase`` tests (M8 Task 8.5).

CrosscutPhase reads ``findings.partial.jsonl``, calls ``CrossCutter.run()``,
writes ``themes.json``. Failure path: returns None (combined report shows
the gap) — does NOT raise.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from senex.config import SenexConfig
from senex.events import (
    BaseEvent,
    CommandBus,
    CrosscutComplete,
    CrosscutStart,
    EventBus,
)
from senex.findings_partial import FindingsPartialWriter
from senex.lens import Lens
from senex.phases.crosscut import CrosscutPhase
from senex.render_models import FindingRecord, LocationRecord


def _seed_partial(audit_dir: Path) -> None:
    audit_dir.mkdir(exist_ok=True, parents=True)
    rec = FindingRecord(
        id="f-aaaaaaaaaaaa",
        file="main.py",
        category="Correctness",
        priority="high",
        title="Off-by-one",
        issue="returns n+1",
        why="slice +1",
        fix="xs[:n]",
        confidence="medium",
        location=LocationRecord(line_start=4, symbol="first_n"),
        report_path="main.py.md",
        suppressed=False,
        prompt_hash="p",
        config_hash="c",
        model_fingerprint="fp",
        lens_version="1.0.0",
    )
    with FindingsPartialWriter(audit_dir) as pw:
        pw.append(rec)


class _OkClient:
    async def chat(self, *, task: str, messages: list[Any], schema: Any, tools: Any = None) -> Any:
        from senex.inference_client import ChatResponse

        body = {
            "schema_version": 1,
            "themes": [
                {
                    "id": "t-000000000000",
                    "title": "Slice errors",
                    "description": "Off-by-one in slicing",
                    "affected_files": ["main.py"],
                    "priority": "high",
                    "confidence": "medium",
                    "recommended_action": "Audit slice bounds",
                }
            ],
        }
        return ChatResponse(
            content=json.dumps(body),
            content_dict=body,
            reasoning_content="",
            tool_calls=None,
            finish_reason="stop",
            latency_ms=10,
            prompt_tokens=10,
            completion_tokens=20,
            fingerprint="fp",
        )


class _FailClient:
    async def chat(self, *, task: str, messages: list[Any], schema: Any, tools: Any = None) -> Any:
        raise RuntimeError("crosscut HTTP fail")


@pytest.mark.asyncio
async def test_crosscut_writes_themes_on_success(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    _seed_partial(audit_dir)

    bus = EventBus()
    captured: list[BaseEvent] = []

    async def cb(evt: BaseEvent) -> None:
        captured.append(evt)

    bus.subscribe_local("test", BaseEvent, cb)
    cb_bus = CommandBus()

    phase = CrosscutPhase(audit_dir=audit_dir, client=_OkClient(), run_id="r1")
    state = await phase.do_work(None, Lens.load("correctness"), SenexConfig(), bus, cb_bus)

    themes = state.get("themes")
    assert themes is not None
    assert len(themes) == 1
    assert (audit_dir / "themes.json").exists()
    starts = [e for e in captured if isinstance(e, CrosscutStart)]
    completes = [e for e in captured if isinstance(e, CrosscutComplete)]
    assert len(starts) == 1
    assert len(completes) == 1
    assert completes[0].theme_count == 1


@pytest.mark.asyncio
async def test_crosscut_returns_none_on_failure(tmp_path: Path) -> None:
    """Spec §8.3: cross-cutting failure does NOT fail the run."""
    audit_dir = tmp_path / "audit"
    _seed_partial(audit_dir)

    bus = EventBus()
    captured: list[BaseEvent] = []

    async def cb(evt: BaseEvent) -> None:
        captured.append(evt)

    bus.subscribe_local("test", BaseEvent, cb)
    cb_bus = CommandBus()

    phase = CrosscutPhase(audit_dir=audit_dir, client=_FailClient(), run_id="r1")
    state = await phase.do_work(None, Lens.load("correctness"), SenexConfig(), bus, cb_bus)

    assert state.get("themes") is None
    completes = [e for e in captured if isinstance(e, CrosscutComplete)]
    assert len(completes) == 1
    assert completes[0].theme_count == 0


@pytest.mark.asyncio
async def test_crosscut_handles_empty_partial(tmp_path: Path) -> None:
    """No findings -> CrossCutter still emits Start/Complete; themes may be empty."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    bus = EventBus()
    cb_bus = CommandBus()
    phase = CrosscutPhase(audit_dir=audit_dir, client=_OkClient(), run_id="r1")
    state = await phase.do_work(None, Lens.load("correctness"), SenexConfig(), bus, cb_bus)
    # An empty partial means CrossCutter is called with [] findings; it may
    # return [] themes (legitimate) or themes derived from empty input.
    themes = state.get("themes")
    assert themes is not None  # not the error sentinel
