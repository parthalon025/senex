"""Tests for senex.tools.loop - bounded ToolLoop iteration controller.

Implements M5 Task 5.8 step tests. Mocks LMStudioClient.chat via AsyncMock;
verifies budget enforcement, event order, compaction hook, tool failure
tolerance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict, Field

from senex.events import BaseEvent, EventBus
from senex.events import ToolBudgetExhausted as ToolBudgetExhaustedEvent
from senex.events import ToolCall as ToolCallEvent
from senex.events import ToolError as ToolErrorEvent
from senex.events import ToolResult as ToolResultEvent
from senex.inference_client import (
    ChatResponse,
    ToolCall,
    ToolCallFunction,
)
from senex.secret_redactor import SecretRedactor
from senex.tools.context import ToolContext
from senex.tools.loop import ToolLoop
from senex.tools.registry import ToolRegistry


class _NoopInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="x", min_length=1, max_length=64)


async def _noop_handler(inp: _NoopInput, ctx: ToolContext) -> str:
    return f"hello {inp.name}"


def _make_tool_call(call_id: str, name: str, args_json: str = '{"name": "x"}') -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        function=ToolCallFunction(name=name, arguments=args_json),
    )


def _chat_response(
    *,
    content: str = "",
    tool_calls: list[ToolCall] | None = None,
    finish_reason: str = "stop",
) -> ChatResponse:
    return ChatResponse(
        content=content,
        content_dict=None if not content else {"ok": True},
        reasoning_content="",
        tool_calls=tool_calls,
        finish_reason=finish_reason,  # type: ignore[arg-type]
        latency_ms=10,
        prompt_tokens=10,
        completion_tokens=10,
        fingerprint="",
    )


@pytest.fixture
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register("noop", _NoopInput, _noop_handler, description="probe")  # type: ignore[arg-type]
    return r


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def captured_events(bus: EventBus) -> list[BaseEvent]:
    captured: list[BaseEvent] = []

    async def _capture(ev: BaseEvent) -> None:
        captured.append(ev)

    bus.subscribe_local(
        "test",
        (ToolCallEvent, ToolResultEvent, ToolErrorEvent, ToolBudgetExhaustedEvent),
        _capture,
    )
    return captured


def _make_loop(
    bus: EventBus,
    registry: ToolRegistry,
    *,
    chat_responses: list[ChatResponse],
    tmp_path: Path,
    max_calls: int = 5,
    compactor: Any = None,
) -> tuple[ToolLoop, AsyncMock]:
    client = AsyncMock()
    client.chat = AsyncMock(side_effect=chat_responses)

    if compactor is None:
        async def _noop_compactor(_msgs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
            return None
        compactor = _noop_compactor

    loop = ToolLoop(
        client=client,
        registry=registry,
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        secret_redactor=SecretRedactor(),
        compactor=compactor,
        max_calls=max_calls,
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    return loop, client


@pytest.mark.asyncio
async def test_terminates_when_no_tool_calls(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    final = _chat_response(content='{"finding": "done"}')
    loop, client = _make_loop(
        bus, registry, chat_responses=[final], tmp_path=tmp_path
    )
    out = await loop.run([{"role": "user", "content": "go"}], schema={"type": "object"}, lens_tools=["noop"])
    assert out is final
    assert client.chat.call_count == 1


@pytest.mark.asyncio
async def test_dispatches_tool_calls_and_appends_results(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path,
    captured_events: list[BaseEvent],
) -> None:
    tc1 = _make_tool_call("c1", "noop")
    tc2 = _make_tool_call("c2", "noop")
    first = _chat_response(tool_calls=[tc1, tc2], finish_reason="tool_calls")
    final = _chat_response(content='{"done": true}')
    loop, client = _make_loop(
        bus, registry, chat_responses=[first, final], tmp_path=tmp_path
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await loop.run(messages, schema={"type": "object"}, lens_tools=["noop"])
    assert out is final
    assert client.chat.call_count == 2

    # Events: 2 ToolCall + 2 ToolResult.
    tool_call_evs = [e for e in captured_events if isinstance(e, ToolCallEvent)]
    tool_result_evs = [e for e in captured_events if isinstance(e, ToolResultEvent)]
    assert len(tool_call_evs) == 2
    assert len(tool_result_evs) == 2

    # Two tool messages appended to history.
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert tool_msgs[1]["tool_call_id"] == "c2"


@pytest.mark.asyncio
async def test_event_order_per_turn(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path,
    captured_events: list[BaseEvent],
) -> None:
    first = _chat_response(
        tool_calls=[_make_tool_call("a", "noop"), _make_tool_call("b", "noop")],
        finish_reason="tool_calls",
    )
    final = _chat_response(content='{"x": 1}')
    loop, _ = _make_loop(
        bus, registry, chat_responses=[first, final], tmp_path=tmp_path
    )
    await loop.run([{"role": "user", "content": "go"}], schema={"type": "object"}, lens_tools=["noop"])

    relevant = [
        e for e in captured_events
        if isinstance(e, (ToolCallEvent, ToolResultEvent, ToolErrorEvent))
    ]
    # Expected: ToolCall(a) -> ToolResult(a) -> ToolCall(b) -> ToolResult(b).
    assert len(relevant) == 4
    assert isinstance(relevant[0], ToolCallEvent) and relevant[0].call_id == "a"
    assert isinstance(relevant[1], ToolResultEvent) and relevant[1].call_id == "a"
    assert isinstance(relevant[2], ToolCallEvent) and relevant[2].call_id == "b"
    assert isinstance(relevant[3], ToolResultEvent) and relevant[3].call_id == "b"


@pytest.mark.asyncio
async def test_budget_exhaustion_at_max_calls(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path,
    captured_events: list[BaseEvent],
) -> None:
    # max_calls=2; loop should dispatch 2 then enforce budget.
    turn1 = _chat_response(
        tool_calls=[_make_tool_call("c1", "noop")], finish_reason="tool_calls"
    )
    turn2 = _chat_response(
        tool_calls=[_make_tool_call("c2", "noop")], finish_reason="tool_calls"
    )
    # Budget-exhaustion final turn (no tools). Even if model would call more,
    # we do not pass tools=, so it returns content.
    final = _chat_response(content='{"final": true}')
    loop, client = _make_loop(
        bus, registry,
        chat_responses=[turn1, turn2, final],
        tmp_path=tmp_path,
        max_calls=2,
    )
    out = await loop.run([{"role": "user", "content": "go"}], schema={"type": "object"}, lens_tools=["noop"])
    assert out is final
    # 3 chat calls: 2 tool turns + 1 budget-exhaustion final turn.
    assert client.chat.call_count == 3

    # ToolBudgetExhausted emitted.
    budget_evs = [e for e in captured_events if isinstance(e, ToolBudgetExhaustedEvent)]
    assert len(budget_evs) == 1
    assert budget_evs[0].calls_made == 2

    # Final turn called WITHOUT tools.
    final_call_kwargs = client.chat.call_args_list[-1].kwargs
    assert final_call_kwargs.get("tools") is None


@pytest.mark.asyncio
async def test_budget_exhaustion_returns_final_response_regardless(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    turn1 = _chat_response(
        tool_calls=[_make_tool_call("c1", "noop")], finish_reason="tool_calls"
    )
    # Even malformed/empty content on the final turn — we still return.
    final = _chat_response(content="not valid json")
    loop, _ = _make_loop(
        bus, registry,
        chat_responses=[turn1, final],
        tmp_path=tmp_path,
        max_calls=1,
    )
    out = await loop.run([{"role": "user", "content": "go"}], schema={"type": "object"}, lens_tools=["noop"])
    assert out is final


@pytest.mark.asyncio
async def test_tool_failure_does_not_break_loop(
    bus: EventBus, tmp_path: Path,
    captured_events: list[BaseEvent],
) -> None:
    registry = ToolRegistry()

    async def explode(inp: _NoopInput, ctx: ToolContext) -> str:
        raise RuntimeError("boom")

    registry.register("noop", _NoopInput, explode, description="probe")  # type: ignore[arg-type]
    turn1 = _chat_response(
        tool_calls=[_make_tool_call("c1", "noop")], finish_reason="tool_calls"
    )
    final = _chat_response(content='{"done": true}')
    loop, _ = _make_loop(
        bus, registry, chat_responses=[turn1, final], tmp_path=tmp_path,
        max_calls=5,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    await loop.run(messages, schema={"type": "object"}, lens_tools=["noop"])

    # ToolError event emitted; loop continued to final turn.
    tool_errs = [e for e in captured_events if isinstance(e, ToolErrorEvent)]
    assert len(tool_errs) == 1
    # Tool message still appended (with error content).
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1


@pytest.mark.asyncio
async def test_compaction_hook_does_not_count_against_budget(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    turn1 = _chat_response(
        tool_calls=[_make_tool_call("c1", "noop")], finish_reason="tool_calls"
    )
    turn2 = _chat_response(
        tool_calls=[_make_tool_call("c2", "noop")], finish_reason="tool_calls"
    )
    final = _chat_response(content='{"x": 1}')

    rewritten_messages = [{"role": "system", "content": "compacted"}]
    compaction_calls: list[int] = []

    async def compactor(msgs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        compaction_calls.append(len(msgs))
        # Trigger on the first invocation; no-op afterward.
        if len(compaction_calls) == 1:
            return list(rewritten_messages)
        return None

    loop, client = _make_loop(
        bus, registry,
        chat_responses=[turn1, turn2, final],
        tmp_path=tmp_path,
        max_calls=5,
        compactor=compactor,
    )
    out = await loop.run(
        [{"role": "user", "content": "go"}],
        schema={"type": "object"},
        lens_tools=["noop"],
    )
    assert out is final
    # 2 tool turns + final = 3 chat calls; compaction is not a chat call.
    assert client.chat.call_count == 3
    # The second chat() received the compacted messages (as typed ChatMessage list).
    second_call_kwargs = client.chat.call_args_list[1].kwargs
    second_messages = second_call_kwargs["messages"]
    # ChatMessage list — look for the marker we injected.
    assert any(
        getattr(m, "content", None) == "compacted" for m in second_messages
    )
    # Compactor was invoked at least once.
    assert len(compaction_calls) >= 1


@pytest.mark.asyncio
async def test_compaction_hook_no_op_returns_none(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    turn1 = _chat_response(
        tool_calls=[_make_tool_call("c1", "noop")], finish_reason="tool_calls"
    )
    final = _chat_response(content='{"x": 1}')

    async def compactor(_msgs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        return None

    loop, _ = _make_loop(
        bus, registry,
        chat_responses=[turn1, final],
        tmp_path=tmp_path,
        max_calls=5,
        compactor=compactor,
    )
    msgs: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await loop.run(msgs, schema={"type": "object"}, lens_tools=["noop"])
    assert out is final


@pytest.mark.asyncio
async def test_unknown_tool_returns_dispatch_error(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path,
    captured_events: list[BaseEvent],
) -> None:
    # Model emits a tool call for an unregistered name.
    turn1 = _chat_response(
        tool_calls=[_make_tool_call("c1", "shell")], finish_reason="tool_calls"
    )
    final = _chat_response(content='{"x": 1}')
    loop, _ = _make_loop(
        bus, registry, chat_responses=[turn1, final], tmp_path=tmp_path,
        max_calls=5,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    out = await loop.run(messages, schema={"type": "object"}, lens_tools=["noop"])
    assert out is final

    tool_errs = [e for e in captured_events if isinstance(e, ToolErrorEvent)]
    assert len(tool_errs) == 1
    # The event ``kind`` Literal in senex.events is restricted; "unknown_tool"
    # is mapped to "internal" by the loop.
    assert tool_errs[0].kind == "internal"
    # The shell tool's tool message appended with error content.
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "[tool error: unknown_tool]" in tool_msgs[0]["content"]
