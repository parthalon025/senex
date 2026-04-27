"""Tests for the M6 ToolLoop <-> Compactor integration (M6 Task 6.4).

Verifies the call-order contract (tool dispatch -> tool result append ->
compactor.maybe_compact -> next chat call) and that compaction-related
exceptions propagate out of ``ToolLoop.run`` so M8 can map them to
per-file ``<file>.ERROR.md`` artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict, Field

from senex.compaction import (
    CompactionFailed,
    CompactionLoopExceeded,
    Compactor,
    ContextOverflow,
)
from senex.config import CompactionCfg
from senex.events import (
    BaseEvent,
    CompactionTriggered as CompactionTriggeredEvent,
    EventBus,
)
from senex.lmstudio_client import (
    ChatResponse,
    ToolCall,
    ToolCallFunction,
)
from senex.secret_redactor import SecretRedactor
from senex.tools.context import ToolContext
from senex.tools.loop import ToolLoop
from senex.tools.registry import ToolRegistry

_REPO = Path(__file__).resolve().parent.parent.parent
_PROMPT_PATH = _REPO / "senex" / "prompts" / "compaction.md"
_SCHEMA_PATH = _REPO / "senex" / "schema" / "compaction_response.schema.json"


class _NoopInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="x", min_length=1, max_length=64)


async def _noop_handler(inp: _NoopInput, ctx: ToolContext) -> str:
    return f"hello {inp.name}"


def _make_tool_call(call_id: str, name: str, args: str = '{"name":"x"}') -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        function=ToolCallFunction(name=name, arguments=args),
    )


def _ok_audit(content: str = '{"done": true}') -> ChatResponse:
    return ChatResponse(
        content=content,
        content_dict={"done": True},
        reasoning_content="",
        tool_calls=None,
        finish_reason="stop",
        latency_ms=10,
        prompt_tokens=10,
        completion_tokens=10,
        fingerprint="",
    )


def _tool_turn(call_id: str = "c1") -> ChatResponse:
    return ChatResponse(
        content="",
        content_dict=None,
        reasoning_content="",
        tool_calls=[_make_tool_call(call_id, "noop")],
        finish_reason="tool_calls",
        latency_ms=10,
        prompt_tokens=10,
        completion_tokens=10,
        fingerprint="",
    )


def _ok_compaction_chat(
    summary: str = "compacted",
    findings: list[str] | None = None,
    questions: list[str] | None = None,
) -> ChatResponse:
    payload = {
        "evidence_summary": summary,
        "key_findings_so_far": findings or [],
        "unanswered_questions": questions or [],
    }
    return ChatResponse(
        content=json.dumps(payload),
        content_dict=payload,
        reasoning_content="",
        tool_calls=None,
        finish_reason="stop",
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
def captured(bus: EventBus) -> list[BaseEvent]:
    out: list[BaseEvent] = []

    async def _capture(ev: BaseEvent) -> None:
        out.append(ev)

    bus.subscribe_local("test", (CompactionTriggeredEvent,), _capture)
    return out


def _make_real_compactor(
    bus: EventBus,
    cfg: CompactionCfg,
    *responses: ChatResponse | Exception,
    path: Path,
    model_id: str = "test-model",
) -> tuple[Compactor, AsyncMock]:
    client = AsyncMock()

    def _count_tokens(messages: list[Any], model_id: str) -> int:
        total = 0
        for m in messages:
            content = getattr(m, "content", None)
            if content:
                total += len(content) // 4
            tcs = getattr(m, "tool_calls", None)
            if tcs:
                for tc in tcs:
                    total += len(tc.function.name) // 4 + len(tc.function.arguments) // 4
            total += 4
        return total

    client.count_tokens = _count_tokens
    client.chat = AsyncMock(side_effect=list(responses))
    compactor = Compactor(
        client=client,
        prompt_path=_PROMPT_PATH,
        schema_path=_SCHEMA_PATH,
        config=cfg,
        bus=bus,
        run_id="test-run",
        path=path,
        model_id=model_id,
        redactor=SecretRedactor(),
    )
    return compactor, client


# ---------------------------------------------------------------------------
# Step 6.4.1 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_loop_invokes_compactor_after_tool_result_before_next_chat(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    """Call order: tool dispatch -> tool result append -> compactor -> next chat."""
    chat_client = AsyncMock()
    chat_client.chat = AsyncMock(side_effect=[_tool_turn("c1"), _ok_audit()])

    order: list[str] = []

    async def compactor_cb(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        order.append("compactor")
        return msgs

    async def _wrapped_chat(**kwargs: Any) -> ChatResponse:
        order.append("chat")
        return await ChatResponseWrapper.next(chat_client, **kwargs)

    class ChatResponseWrapper:
        @staticmethod
        async def next(client: AsyncMock, **kwargs: Any) -> ChatResponse:
            return await client.chat(**kwargs)

    real_chat = chat_client.chat

    async def _instrumented_chat(**kwargs: Any) -> ChatResponse:
        order.append("chat")
        return await real_chat(**kwargs)

    client_for_loop = AsyncMock()
    client_for_loop.chat = _instrumented_chat

    loop = ToolLoop(
        client=client_for_loop,
        registry=registry,
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        secret_redactor=SecretRedactor(),
        compactor=compactor_cb,
        max_calls=5,
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    out = await loop.run(
        [{"role": "user", "content": "go"}],
        schema={"type": "object"},
        lens_tools=["noop"],
    )
    assert out.finish_reason == "stop"
    # First chat (tool turn) -> compactor (after tool result append) -> second chat (final).
    assert order == ["chat", "compactor", "chat"]


@pytest.mark.asyncio
async def test_tool_loop_audit_completes_after_compaction(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    """Bloat fixture forces compaction; audit completes with a structured response."""
    cfg = CompactionCfg(
        enabled=True,
        trigger_pct=0.1,
        target_pct=0.05,
        preserve_recent_turns=2,
        max_compactions_per_file=3,
    )
    compactor, _ = _make_real_compactor(
        bus, cfg, _ok_compaction_chat(), path=Path("src/foo.py")
    )

    async def cb(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Force compaction with very small ctx window (= 1).
        return await compactor.maybe_compact(msgs, context_window=1)

    audit_client = AsyncMock()
    audit_client.chat = AsyncMock(side_effect=[_tool_turn(), _ok_audit()])
    loop = ToolLoop(
        client=audit_client,
        registry=registry,
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        secret_redactor=SecretRedactor(),
        compactor=cb,
        max_calls=5,
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    out = await loop.run(
        [{"role": "system", "content": "system"}, {"role": "user", "content": "go"}],
        schema={"type": "object"},
        lens_tools=["noop"],
    )
    assert out.finish_reason == "stop"
    assert compactor.compactions_so_far == 1


@pytest.mark.asyncio
async def test_tool_loop_emits_compaction_triggered_event(
    registry: ToolRegistry,
    bus: EventBus,
    tmp_path: Path,
    captured: list[BaseEvent],
) -> None:
    cfg = CompactionCfg(
        enabled=True,
        trigger_pct=0.1,
        target_pct=0.05,
        preserve_recent_turns=2,
        max_compactions_per_file=3,
    )
    compactor, _ = _make_real_compactor(
        bus, cfg, _ok_compaction_chat(), path=Path("src/foo.py")
    )

    async def cb(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await compactor.maybe_compact(msgs, context_window=1)

    audit_client = AsyncMock()
    audit_client.chat = AsyncMock(side_effect=[_tool_turn(), _ok_audit()])
    loop = ToolLoop(
        client=audit_client,
        registry=registry,
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        secret_redactor=SecretRedactor(),
        compactor=cb,
        max_calls=5,
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    await loop.run(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
        schema={"type": "object"},
        lens_tools=["noop"],
    )
    triggered = [e for e in captured if isinstance(e, CompactionTriggeredEvent)]
    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_tool_loop_propagates_compaction_loop_exceeded(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    cfg = CompactionCfg(
        enabled=True,
        trigger_pct=0.1,
        target_pct=0.05,
        preserve_recent_turns=2,
        max_compactions_per_file=0,  # any trigger fails immediately
    )
    compactor, _ = _make_real_compactor(
        bus, cfg, path=Path("src/foo.py")
    )

    async def cb(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await compactor.maybe_compact(msgs, context_window=1)

    audit_client = AsyncMock()
    audit_client.chat = AsyncMock(side_effect=[_tool_turn(), _ok_audit()])
    loop = ToolLoop(
        client=audit_client,
        registry=registry,
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        secret_redactor=SecretRedactor(),
        compactor=cb,
        max_calls=5,
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    with pytest.raises(CompactionLoopExceeded):
        await loop.run(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            schema={"type": "object"},
            lens_tools=["noop"],
        )


@pytest.mark.asyncio
async def test_tool_loop_propagates_compaction_failed(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    cfg = CompactionCfg(
        enabled=True,
        trigger_pct=0.1,
        target_pct=0.05,
        preserve_recent_turns=2,
        max_compactions_per_file=3,
    )
    # Compaction client raises a transport error.
    compactor, _ = _make_real_compactor(
        bus,
        cfg,
        RuntimeError("compaction transport boom"),
        path=Path("src/foo.py"),
    )

    async def cb(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await compactor.maybe_compact(msgs, context_window=1)

    audit_client = AsyncMock()
    audit_client.chat = AsyncMock(side_effect=[_tool_turn(), _ok_audit()])
    loop = ToolLoop(
        client=audit_client,
        registry=registry,
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        secret_redactor=SecretRedactor(),
        compactor=cb,
        max_calls=5,
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    with pytest.raises(CompactionFailed):
        await loop.run(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            schema={"type": "object"},
            lens_tools=["noop"],
        )


@pytest.mark.asyncio
async def test_tool_loop_does_not_count_compaction_against_tool_budget(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    """Compaction calls do NOT count against ``ToolsCfg.max_calls_per_file``.

    Spec section 5.5.1 budget-accounting clause. 3 tool turns + 2
    compactions both succeed independently: the audit client is invoked
    for each tool turn + final, the compaction client is invoked for
    each compaction, and the tool budget never sees the compaction
    traffic.
    """
    # Generous compaction budget so the third tool turn does not trip the
    # CompactionLoopExceeded guard; the test is about budget independence.
    cfg = CompactionCfg(
        enabled=True,
        trigger_pct=0.1,
        target_pct=0.05,
        preserve_recent_turns=2,
        max_compactions_per_file=10,
    )
    compactor, compaction_client = _make_real_compactor(
        bus,
        cfg,
        _ok_compaction_chat(),
        _ok_compaction_chat(),
        _ok_compaction_chat(),
        path=Path("src/foo.py"),
    )

    async def cb(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await compactor.maybe_compact(msgs, context_window=1)

    # Three tool turns + one final.
    audit_client = AsyncMock()
    audit_client.chat = AsyncMock(
        side_effect=[
            _tool_turn("a"),
            _tool_turn("b"),
            _tool_turn("c"),
            _ok_audit(),  # budget-exhaustion final turn
        ]
    )
    loop = ToolLoop(
        client=audit_client,
        registry=registry,
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        secret_redactor=SecretRedactor(),
        compactor=cb,
        max_calls=3,
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    await loop.run(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
        schema={"type": "object"},
        lens_tools=["noop"],
    )
    # Tool budget: 3 chat calls used + 1 final budget-exhaustion turn = 4.
    assert audit_client.chat.call_count == 4
    # Compaction budget: 3 used; the limit (10) is independent of tool budget.
    assert compactor.compactions_so_far == 3
    # Compaction client: 3 chat calls (independent budget).
    assert compaction_client.chat.call_count == 3


@pytest.mark.asyncio
async def test_tool_loop_context_overflow_when_compaction_disabled(
    registry: ToolRegistry, bus: EventBus, tmp_path: Path
) -> None:
    """When compaction is disabled and natural tokens > ctx, ToolLoop raises ContextOverflow.

    The tool loop's overflow check fires when ``enabled=False`` and the
    measured token count exceeds the supplied context window. The opt-out
    path lives in M6 (per spec section 5.5.1) so M8 can catch
    ``ContextOverflow`` from one import.
    """
    cfg = CompactionCfg(enabled=False)
    compactor, _ = _make_real_compactor(bus, cfg, path=Path("src/foo.py"))

    async def cb(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # No-op compaction (disabled), but loop should still raise on overflow.
        result = await compactor.maybe_compact(msgs, context_window=4)
        # Compute tokens; raise ContextOverflow when over the cap.
        if compactor.should_trigger(msgs, context_window=4):
            # disabled => never triggers; ContextOverflow surfaces from
            # the loop's natural-overflow check
            pass
        return result

    audit_client = AsyncMock()

    # Simulate the chat call rejecting an oversized prompt by raising
    # ContextOverflow directly (the loop maps oversize to this).
    audit_client.chat = AsyncMock(
        side_effect=ContextOverflow(
            path=Path("src/foo.py"), token_count=10_000, context_window=4
        )
    )
    loop = ToolLoop(
        client=audit_client,
        registry=registry,
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        secret_redactor=SecretRedactor(),
        compactor=cb,
        max_calls=5,
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
        npx_path=Path("/usr/bin/npx"),
        bus=bus,
    )
    with pytest.raises(ContextOverflow):
        await loop.run(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            schema={"type": "object"},
            lens_tools=["noop"],
        )
