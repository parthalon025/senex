"""Integration tests for ToolLoop + real LMStudioClient + respx-mocked LMS.

Implements M5 Task 5.8 step 5.8.4. Demonstrates the M3 <-> M5 boundary by
wiring a real ToolLoop on top of a real LMStudioClient with mocked HTTP
responses; verifies budget enforcement reaches both layers correctly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from senex.config import LmStudioCfg
from senex.events import EventBus
from senex.events import ToolBudgetExhausted as ToolBudgetExhaustedEvent
from senex.inference_client import LMStudioClient
from senex.secret_redactor import SecretRedactor
from senex.tools.loop import ToolLoop
from senex.tools.read_file import register_read_file
from senex.tools.registry import ToolRegistry


@pytest.fixture
def lms_config() -> LmStudioCfg:
    return LmStudioCfg(
        base_url="http://localhost:1234/v1",
        api_key="lm-studio",
        connect_timeout=2,
        read_timeout=10,
        http_retries=0,
        backoff_seconds=[1],
        model="test-model",
        context_window=32768,
        token_budget_pct=0.9,
        fingerprint_recheck_interval_s=60.0,
    )


def _sse_response(chunks: list[dict[str, Any]]) -> httpx.Response:
    body_parts: list[str] = []
    for c in chunks:
        body_parts.append(f"data: {json.dumps(c)}\n\n")
    body_parts.append("data: [DONE]\n\n")
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="".join(body_parts),
    )


def _tool_call_chunk(call_id: str, name: str, args_json: str) -> dict[str, Any]:
    """Single SSE chunk emitting a tool_call delta + finish_reason=tool_calls."""
    return {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": args_json},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _content_chunk(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None,
            }
        ]
    }


def _stop_chunk() -> dict[str, Any]:
    return {
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"},
        ]
    }


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (root / name).write_text(f"print('{name}')\n", encoding="utf-8")
    return root.resolve()


@pytest.mark.asyncio
async def test_real_client_with_real_loop_respects_budget(
    respx_mock: Any,
    lms_config: LmStudioCfg,
    repo_root: Path,
) -> None:
    bus = EventBus()
    redactor = SecretRedactor()
    client = LMStudioClient(config=lms_config, bus=bus, redactor=redactor)

    registry = ToolRegistry()
    register_read_file(registry)

    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.subscribe_local("test", ToolBudgetExhaustedEvent, _capture)

    # Mock LMS: turn 1 -> read_file(a.py), turn 2 -> read_file(b.py),
    # turn 3 (final no-tools, after budget exhausted) -> final content.
    # With max_calls=2, the loop dispatches 2 tool turns then makes the
    # budget-exhaustion final turn = exactly 3 HTTP calls.
    responses = [
        _sse_response([
            _tool_call_chunk("c1", "read_file", '{"relpath": "a.py"}'),
        ]),
        _sse_response([
            _tool_call_chunk("c2", "read_file", '{"relpath": "b.py"}'),
        ]),
        _sse_response([
            _content_chunk('{"final": true}'),
            _stop_chunk(),
        ]),
    ]
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.side_effect = responses

    async def _no_compact(_msgs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        return None

    loop = ToolLoop(
        client=client,
        registry=registry,
        repo_root=repo_root,
        repo_name="testrepo",
        secret_redactor=redactor,
        compactor=_no_compact,
        max_calls=2,
        tool_timeout_seconds=5.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": "audit a.py"}]
    out = await loop.run(messages, schema={"type": "object"}, lens_tools=["read_file"])

    # 3 LMS HTTP calls: 2 tool turns + 1 final no-tools turn.
    assert route.call_count == 3
    # ToolBudgetExhausted emitted exactly once.
    budget_evs = [e for e in captured if isinstance(e, ToolBudgetExhaustedEvent)]
    assert len(budget_evs) == 1
    assert budget_evs[0].calls_made == 2
    # Final response returned with content.
    assert "final" in out.content

    await client.aclose()


@pytest.mark.asyncio
async def test_real_client_terminates_early_on_no_tool_calls(
    respx_mock: Any,
    lms_config: LmStudioCfg,
    repo_root: Path,
) -> None:
    bus = EventBus()
    redactor = SecretRedactor()
    client = LMStudioClient(config=lms_config, bus=bus, redactor=redactor)

    registry = ToolRegistry()
    register_read_file(registry)

    captured: list[Any] = []

    async def _capture(ev: Any) -> None:
        captured.append(ev)

    bus.subscribe_local("test", ToolBudgetExhaustedEvent, _capture)

    route = respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=_sse_response([
            _content_chunk('{"final": true}'),
            _stop_chunk(),
        ])
    )

    async def _no_compact(_msgs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        return None

    loop = ToolLoop(
        client=client,
        registry=registry,
        repo_root=repo_root,
        repo_name="testrepo",
        secret_redactor=redactor,
        compactor=_no_compact,
        max_calls=2,
        tool_timeout_seconds=5.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    out = await loop.run(
        [{"role": "user", "content": "go"}],
        schema={"type": "object"},
        lens_tools=["read_file"],
    )

    assert route.call_count == 1
    # No budget-exhaustion event because the loop terminated early.
    budget_evs = [e for e in captured if isinstance(e, ToolBudgetExhaustedEvent)]
    assert len(budget_evs) == 0
    assert "final" in out.content

    await client.aclose()
