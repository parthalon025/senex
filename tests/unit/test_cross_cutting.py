"""Unit tests for senex.cross_cutting."""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from senex.config import CrosscutCfg
from senex.cross_cutting import (
    CrossCutter,
    _chunk_for_crosscut,
    _compress_to_tuples,
    _top_n_per_priority,
    compute_theme_id,
)
from senex.events import EventBus
from senex.render_models import FindingRecord, LocationRecord


def _finding(
    *,
    fid: str = "f-aaaaaaaaaaaa",
    file: str = "x.py",
    priority: str = "medium",
    title: str = "t",
    category: str = "cat",
) -> FindingRecord:
    return FindingRecord(
        id=fid,
        file=file,
        category=category,
        priority=priority,  # type: ignore[arg-type]
        title=title,
        issue="i",
        why="w",
        fix="f",
        confidence="medium",
        location=LocationRecord(line_start=1),
        report_path=f"{file}.md",
        suppressed=False,
        prompt_hash="sha256:p",
        config_hash="sha256:c",
        model_fingerprint="sha256:m",
        lens_version="1.0.0",
    )


def test_compute_theme_id_format() -> None:
    tid = compute_theme_id("a theme", "01JZ3K7B")
    assert re.match(r"^t-[a-f0-9]{12}$", tid), tid


def test_compute_theme_id_stable() -> None:
    a = compute_theme_id("title", "run")
    b = compute_theme_id("title", "run")
    assert a == b


def test_compute_theme_id_sensitive_to_run_id() -> None:
    a = compute_theme_id("title", "run1")
    b = compute_theme_id("title", "run2")
    assert a != b


def test_top_n_per_priority_caps_correctly() -> None:
    findings = (
        [_finding(fid=f"f-1{i:011x}", priority="high", title=f"h{i}") for i in range(60)]
        + [_finding(fid=f"f-2{i:011x}", priority="medium", title=f"m{i}") for i in range(200)]
        + [_finding(fid=f"f-3{i:011x}", priority="low", title=f"l{i}") for i in range(200)]
    )
    capped = _top_n_per_priority(findings, top_h=50, top_m=100, top_l=100)
    counts = {"high": 0, "medium": 0, "low": 0}
    for f in capped:
        counts[f.priority] += 1
    assert counts == {"high": 50, "medium": 100, "low": 100}


def test_compress_to_tuples_drops_issue_why_fix() -> None:
    findings = [
        _finding(file="a.py", priority="high", title="h1", category="C1"),
        _finding(file="b.py", priority="medium", title="m1", category="C2"),
    ]
    tuples = _compress_to_tuples(findings)
    assert len(tuples) == 2
    assert tuples[0] == ("a.py", "C1", "high", "h1")
    # No issue/why/fix anywhere in the serialized payload.
    payload = json.dumps(tuples)
    assert "issue" not in payload
    assert "why" not in payload
    assert "fix" not in payload


def test_chunk_for_crosscut_degenerate_single_level() -> None:
    findings = [_finding(fid=f"f-{i:012x}") for i in range(5)]
    chunks = _chunk_for_crosscut(findings, cluster_map=None)
    # Degenerate: one chunk = full list.
    assert len(chunks) == 1
    assert chunks[0] == findings


def test_chunk_for_crosscut_with_cluster_map() -> None:
    a = _finding(fid="f-aaaaaaaaaaaa", file="a.py")
    b = _finding(fid="f-bbbbbbbbbbbb", file="b.py")
    c = _finding(fid="f-cccccccccccc", file="c.py")
    cluster_map = {"core": [a, b], "edge": [c]}
    chunks = _chunk_for_crosscut([a, b, c], cluster_map=cluster_map)
    assert len(chunks) == 2
    contents = sorted([sorted(f.id for f in chunk) for chunk in chunks])
    assert contents == sorted(
        [["f-aaaaaaaaaaaa", "f-bbbbbbbbbbbb"], ["f-cccccccccccc"]]
    )


@pytest.mark.asyncio
async def test_crosscut_compresses_findings_to_tuples() -> None:
    # Use unique sentinel strings in issue/why/fix so we can detect leakage
    # without false positives from short common substrings.
    findings = [
        FindingRecord(
            id=f"f-{i:012x}",
            file=f"f{i}.py",
            category="cat",
            priority="medium",
            title=f"t{i}",
            issue=f"ISSUE_SENTINEL_{i}_xyz",
            why=f"WHY_SENTINEL_{i}_xyz",
            fix=f"FIX_SENTINEL_{i}_xyz",
            confidence="medium",
            location=LocationRecord(line_start=1),
            report_path=f"f{i}.py.md",
            suppressed=False,
            prompt_hash="sha256:p",
            config_hash="sha256:c",
            model_fingerprint="sha256:m",
            lens_version="1.0.0",
        )
        for i in range(10)
    ]
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response({"schema_version": 1, "themes": []}))
    bus = EventBus()
    cc = CrossCutter(bus=bus, run_id="01JZ3K7B")
    await cc.run(client=client, findings=findings, cfg=CrosscutCfg())

    # Inspect the user message that was sent.
    call_args = client.chat.await_args
    messages = call_args.kwargs["messages"]
    user_msg = next(m for m in messages if m.role == "user").content
    # The message must NOT contain issue / why / fix bodies.
    for f in findings:
        assert f.issue not in user_msg
        assert f.why not in user_msg
        assert f.fix not in user_msg


@pytest.mark.asyncio
async def test_crosscut_caps_input_per_priority() -> None:
    findings = (
        [_finding(fid=f"f-1{i:011x}", priority="high", title=f"h{i}") for i in range(60)]
        + [_finding(fid=f"f-2{i:011x}", priority="medium", title=f"m{i}") for i in range(200)]
        + [_finding(fid=f"f-3{i:011x}", priority="low", title=f"l{i}") for i in range(200)]
    )
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response({"schema_version": 1, "themes": []}))
    bus = EventBus()
    cc = CrossCutter(bus=bus, run_id="01JZ3K7B")
    await cc.run(client=client, findings=findings, cfg=CrosscutCfg())

    call_args = client.chat.await_args
    messages = call_args.kwargs["messages"]
    user_msg = next(m for m in messages if m.role == "user").content
    # Count tuple-shaped lines / titles included.
    high_count = sum(1 for line in user_msg.splitlines() if "\"high\"" in line)
    medium_count = sum(1 for line in user_msg.splitlines() if "\"medium\"" in line)
    low_count = sum(1 for line in user_msg.splitlines() if "\"low\"" in line)
    assert high_count <= 50
    assert medium_count <= 100
    assert low_count <= 100


@pytest.mark.asyncio
async def test_crosscut_returns_themes_with_stable_ids() -> None:
    findings = [_finding(file=f"f{i}.py") for i in range(3)]
    payload = {
        "schema_version": 1,
        "themes": [
            {
                "id": "t-placeholder1",  # client may emit anything; we recompute
                "title": "Theme A",
                "description": "d",
                "affected_files": ["f0.py", "f1.py"],
                "priority": "medium",
                "confidence": "medium",
                "recommended_action": "do x",
            }
        ],
    }
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response(payload))
    bus = EventBus()
    cc = CrossCutter(bus=bus, run_id="01JZ3K7B")
    themes = await cc.run(client=client, findings=findings, cfg=CrosscutCfg())
    assert themes is not None
    assert len(themes) == 1
    assert re.match(r"^t-[a-f0-9]{12}$", themes[0].id)
    # Recomputing must match.
    expected = compute_theme_id("Theme A", "01JZ3K7B")
    assert themes[0].id == expected


@pytest.mark.asyncio
async def test_crosscut_returns_none_on_lms_http_failure() -> None:
    findings = [_finding(file=f"f{i}.py") for i in range(3)]
    client = MagicMock()
    client.chat = AsyncMock(side_effect=ConnectionError("boom"))
    bus = EventBus()
    cc = CrossCutter(bus=bus, run_id="01JZ3K7B")
    result = await cc.run(client=client, findings=findings, cfg=CrosscutCfg())
    assert result is None


@pytest.mark.asyncio
async def test_crosscut_returns_none_on_schema_mismatch() -> None:
    findings = [_finding(file=f"f{i}.py") for i in range(3)]
    bad_response = _make_chat_response({"not_a_theme_object": "junk"})
    client = MagicMock()
    client.chat = AsyncMock(return_value=bad_response)
    bus = EventBus()
    cc = CrossCutter(bus=bus, run_id="01JZ3K7B")
    result = await cc.run(client=client, findings=findings, cfg=CrosscutCfg())
    assert result is None


@pytest.mark.asyncio
async def test_crosscut_passes_lens_tools_disabled() -> None:
    """Crosscut call uses NO tools (same as compaction)."""
    findings = [_finding(file=f"f{i}.py") for i in range(3)]
    client = MagicMock()
    client.chat = AsyncMock(return_value=_make_chat_response({"schema_version": 1, "themes": []}))
    bus = EventBus()
    cc = CrossCutter(bus=bus, run_id="01JZ3K7B")
    await cc.run(client=client, findings=findings, cfg=CrosscutCfg())
    call_args = client.chat.await_args
    assert call_args.kwargs.get("tools") is None


# --- helpers ----------------------------------------------------------------


def _make_chat_response(content_dict: Any) -> Any:
    """Mock a ChatResponse-like object."""
    obj = MagicMock()
    obj.content = json.dumps(content_dict)
    obj.content_dict = content_dict
    obj.reasoning_content = ""
    obj.tool_calls = None
    obj.finish_reason = "stop"
    obj.latency_ms = 100
    obj.prompt_tokens = 10
    obj.completion_tokens = 10
    obj.fingerprint = "sha256:m"
    return obj
