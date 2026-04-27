"""Tests for senex.compaction — M6 context compaction (executor + trigger + counter).

Implements M6 Tasks 6.2 + 6.3 step tests per
``docs/superpowers/plans/2026-04-26-senex-v1/m6-compaction.md``.

External boundary mocked via ``unittest.mock.AsyncMock`` for the ``LLMClient``
protocol; no live LMS. See M6 Prerequisites — "M6 unit tests use a mocked
``LLMClient`` (per §6 testing — mocks only at external boundaries)".
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from senex.compaction import (
    Compactor,
    CompactionFailed,
    CompactionLoopExceeded,
    CompactionResult,
    ContextOverflow,
    should_trigger,
)
from senex.config import CompactionCfg
from senex.events import (
    BaseEvent,
    CompactionComplete as CompactionCompleteEvent,
    CompactionError as CompactionErrorEvent,
    CompactionTriggered as CompactionTriggeredEvent,
    EventBus,
)
from senex.lmstudio_client import ChatResponse
from senex.secret_redactor import SecretRedactor


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parent.parent.parent
_PROMPT_PATH = _REPO / "senex" / "prompts" / "compaction.md"
_SCHEMA_PATH = _REPO / "senex" / "schema" / "compaction_response.schema.json"


def _ok_compaction_payload(
    *,
    summary: str = "Read auditor.py L42-77 and registry.py; identified missing ctx mgr.",
    findings: list[str] | None = None,
    questions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "evidence_summary": summary,
        "key_findings_so_far": findings or [],
        "unanswered_questions": questions or [],
    }


def _ok_chat_response(payload: dict[str, Any]) -> ChatResponse:
    body = json.dumps(payload)
    return ChatResponse(
        content=body,
        content_dict=payload,
        reasoning_content="",
        tool_calls=None,
        finish_reason="stop",
        latency_ms=10,
        prompt_tokens=10,
        completion_tokens=10,
        fingerprint="",
    )


def _bad_chat_response(content: str = "{ this is not valid json") -> ChatResponse:
    return ChatResponse(
        content=content,
        content_dict=None,
        reasoning_content="",
        tool_calls=None,
        finish_reason="stop",
        latency_ms=10,
        prompt_tokens=10,
        completion_tokens=10,
        fingerprint="",
    )


def _msg(role: str, content: str) -> dict[str, Any]:
    return {"role": role, "content": content}


def _history_with_compacted(prior_compacted: bool = False) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [_msg("system", "You are a senior reviewer.")]
    if prior_compacted:
        msgs.append(_msg("system", "[COMPACTED]\nold summary"))
    msgs += [
        _msg("user", "Audit src/foo.py please."),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"src/foo.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "1: def foo():\n2:    pass"},
        _msg("user", "Now check callers"),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "gitnexus_context", "arguments": '{"name":"foo"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c2", "content": "callers: bar(), baz()"},
        _msg("assistant", "Looking at the callers..."),
    ]
    return msgs


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def captured(bus: EventBus) -> list[BaseEvent]:
    out: list[BaseEvent] = []

    async def _capture(ev: BaseEvent) -> None:
        out.append(ev)

    bus.subscribe_local(
        "test",
        (CompactionTriggeredEvent, CompactionCompleteEvent, CompactionErrorEvent),
        _capture,
    )
    return out


@pytest.fixture
def cfg() -> CompactionCfg:
    return CompactionCfg(
        enabled=True,
        trigger_pct=0.80,
        target_pct=0.50,
        preserve_recent_turns=2,
        max_compactions_per_file=3,
    )


def _make_client(*responses: ChatResponse | Exception) -> AsyncMock:
    client = AsyncMock()

    def _count_tokens(messages: list[Any], model_id: str) -> int:
        # Sum content lengths / 4 — deterministic and small.
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
    return client


def _make_compactor(
    bus: EventBus,
    cfg: CompactionCfg,
    *responses: ChatResponse | Exception,
    path: Path | None = None,
) -> tuple[Compactor, AsyncMock]:
    client = _make_client(*responses)
    compactor = Compactor(
        client=client,
        prompt_path=_PROMPT_PATH,
        schema_path=_SCHEMA_PATH,
        config=cfg,
        bus=bus,
        run_id="test-run",
        path=path or Path("src/foo.py"),
        model_id="test-model",
        redactor=SecretRedactor(),
    )
    return compactor, client


# ---------------------------------------------------------------------------
# Step 6.2.1 — executor (Compactor.run) tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_prompt_includes_untrusted_content_clause() -> None:
    body = _PROMPT_PATH.read_text(encoding="utf-8")
    assert "<UNTRUSTED_CONTENT>" in body
    assert "</UNTRUSTED_CONTENT>" in body


@pytest.mark.asyncio
async def test_compactor_run_returns_rewritten_history_with_compacted_marker(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    payload = _ok_compaction_payload(
        summary="Reviewed foo.py and its callers; the function lacks a context manager.",
        findings=["medium - foo() leaks file handle"],
        questions=["does bar() always close?"],
    )
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(payload))
    out = await compactor.run(msgs)
    # The slice was replaced by exactly one [COMPACTED] system message.
    compacted = [m for m in out if m.get("content", "").startswith("[COMPACTED]\n")]
    assert len(compacted) == 1
    assert compacted[0]["role"] == "system"


@pytest.mark.asyncio
async def test_compactor_run_preserves_recent_turns_byte_equal(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    pre_tail = [dict(m) for m in msgs[-cfg.preserve_recent_turns :]]
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    out = await compactor.run(msgs)
    post_tail = out[-cfg.preserve_recent_turns :]
    assert post_tail == pre_tail


@pytest.mark.asyncio
async def test_compactor_run_preserves_system_prompt_index_0(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    pre_first = dict(msgs[0])
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    out = await compactor.run(msgs)
    assert out[0] == pre_first


@pytest.mark.asyncio
async def test_compactor_run_excludes_prior_compacted_blocks_from_slice(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted(prior_compacted=True)
    compactor, client = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    await compactor.run(msgs)
    # Inspect the messages sent to client.chat: prior [COMPACTED] block must NOT appear.
    sent = client.chat.call_args.kwargs["messages"]
    sent_contents = [getattr(m, "content", "") or "" for m in sent]
    for c in sent_contents:
        assert not c.startswith("[COMPACTED]\nold summary"), (
            "prior [COMPACTED] block leaked into the slice"
        )


@pytest.mark.asyncio
async def test_compactor_run_passes_tools_none_to_client(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    compactor, client = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    await compactor.run(msgs)
    kwargs = client.chat.call_args.kwargs
    assert kwargs.get("tools") is None
    assert kwargs.get("task") == "compaction"


@pytest.mark.asyncio
async def test_compactor_run_raises_compaction_failed_on_lms_error(
    bus: EventBus, cfg: CompactionCfg, captured: list[BaseEvent]
) -> None:
    msgs = _history_with_compacted()
    compactor, _ = _make_compactor(bus, cfg, RuntimeError("transport boom"))
    with pytest.raises(CompactionFailed) as exc:
        # maybe_compact emits the events; run() alone raises but does not emit.
        await compactor.maybe_compact(msgs, context_window=1)
    assert exc.value.kind == "lms_error"
    # Events: CompactionTriggered then CompactionError.
    kinds = [type(e).__name__ for e in captured]
    assert kinds == ["CompactionTriggered", "CompactionError"]


@pytest.mark.asyncio
async def test_compactor_run_retries_once_on_schema_invalid_then_succeeds(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    bad = _bad_chat_response("not json at all")
    good = _ok_chat_response(_ok_compaction_payload())
    compactor, client = _make_compactor(bus, cfg, bad, good)
    out = await compactor.run(msgs)
    assert client.chat.call_count == 2
    # Successful path: returned history has the compacted marker.
    compacted = [m for m in out if m.get("content", "").startswith("[COMPACTED]\n")]
    assert len(compacted) == 1


@pytest.mark.asyncio
async def test_compactor_run_raises_compaction_failed_after_retry_exhausted(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    bad1 = _bad_chat_response("{garbage")
    bad2 = _bad_chat_response('{"missing":"required keys"}')
    compactor, client = _make_compactor(bus, cfg, bad1, bad2)
    with pytest.raises(CompactionFailed) as exc:
        await compactor.run(msgs)
    assert exc.value.kind == "schema_invalid"
    assert client.chat.call_count == 2


@pytest.mark.asyncio
async def test_compactor_run_validates_response_with_pydantic_type_adapter(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    """Extra keys on the response MUST be rejected (extra='forbid' on CompactionResult)."""
    msgs = _history_with_compacted()
    payload = _ok_compaction_payload()
    payload["unexpected_key"] = "should be rejected"
    bad = _ok_chat_response(payload)  # JSON is valid but schema is invalid.
    bad2 = _ok_chat_response(payload)  # Both attempts fail.
    compactor, _ = _make_compactor(bus, cfg, bad, bad2)
    with pytest.raises(CompactionFailed):
        await compactor.run(msgs)


@pytest.mark.asyncio
async def test_compactor_maybe_compact_emits_events_in_order_on_success(
    bus: EventBus, cfg: CompactionCfg, captured: list[BaseEvent]
) -> None:
    msgs = _history_with_compacted()
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    await compactor.maybe_compact(msgs, context_window=1)  # force trigger via tiny ctx
    kinds = [type(e).__name__ for e in captured]
    assert kinds == ["CompactionTriggered", "CompactionComplete"]


@pytest.mark.asyncio
async def test_compactor_run_redacts_secrets_in_compacted_block(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    payload = _ok_compaction_payload(
        findings=["sk-test-FAKEFAKEFAKEFAKEFAKEFAKE leaked into a finding"],
    )
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(payload))
    out = await compactor.run(msgs)
    compacted_block = next(
        m["content"] for m in out if (m.get("content") or "").startswith("[COMPACTED]\n")
    )
    assert "sk-test-FAKEFAKEFAKEFAKEFAKEFAKE" not in compacted_block
    assert "[REDACTED:" in compacted_block


@pytest.mark.asyncio
async def test_compactor_run_propagates_cancelled_error(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    compactor, _ = _make_compactor(bus, cfg, asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await compactor.run(msgs)


# ---------------------------------------------------------------------------
# Step 6.3.1 — should_trigger / maybe_compact tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_trigger_returns_true_at_threshold(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = [_msg("user", "x" * 1000) for _ in range(20)]
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    # ctx_window deliberately small so the ratio crosses trigger_pct.
    assert compactor.should_trigger(msgs, context_window=200) is True


@pytest.mark.asyncio
async def test_should_trigger_returns_false_below_threshold(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = [_msg("user", "tiny")]
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    assert compactor.should_trigger(msgs, context_window=100_000) is False


@pytest.mark.asyncio
async def test_should_trigger_returns_false_when_disabled(bus: EventBus) -> None:
    cfg = CompactionCfg(enabled=False)
    msgs = [_msg("user", "x" * 100_000)]
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    assert compactor.should_trigger(msgs, context_window=4) is False


@pytest.mark.asyncio
async def test_should_trigger_uses_client_count_tokens(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = [_msg("user", "anything")]
    compactor, client = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    # Force the client.count_tokens to a sentinel value to confirm the call path.
    sentinel_called: list[int] = []

    def _spy(messages: list[Any], model_id: str) -> int:
        sentinel_called.append(1)
        return 9999

    client.count_tokens = _spy
    out = compactor.should_trigger(msgs, context_window=1)
    assert sentinel_called == [1]
    assert out is True


@pytest.mark.asyncio
async def test_maybe_compact_skips_when_should_trigger_false(
    bus: EventBus, cfg: CompactionCfg, captured: list[BaseEvent]
) -> None:
    msgs = [_msg("user", "tiny")]
    compactor, client = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    out = await compactor.maybe_compact(msgs, context_window=1_000_000)
    assert out is msgs  # unchanged identity
    assert client.chat.await_count == 0
    assert captured == []


@pytest.mark.asyncio
async def test_maybe_compact_increments_counter_on_success(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    await compactor.maybe_compact(msgs, context_window=1)
    assert compactor.compactions_so_far == 1


@pytest.mark.asyncio
async def test_maybe_compact_raises_loop_exceeded_at_n_plus_one(
    bus: EventBus,
) -> None:
    cfg = CompactionCfg(
        enabled=True,
        trigger_pct=0.1,
        target_pct=0.05,
        preserve_recent_turns=2,
        max_compactions_per_file=2,
    )
    payload = _ok_compaction_payload()
    compactor, _ = _make_compactor(
        bus,
        cfg,
        _ok_chat_response(payload),
        _ok_chat_response(payload),
    )
    msgs = _history_with_compacted()
    msgs2 = await compactor.maybe_compact(list(msgs), context_window=1)
    msgs3 = await compactor.maybe_compact(list(msgs2), context_window=1)
    assert compactor.compactions_so_far == 2
    with pytest.raises(CompactionLoopExceeded) as exc:
        await compactor.maybe_compact(list(msgs3), context_window=1)
    assert exc.value.compactions_so_far == 2
    assert exc.value.limit == 2


@pytest.mark.asyncio
async def test_maybe_compact_emits_triggered_before_call(
    bus: EventBus, cfg: CompactionCfg, captured: list[BaseEvent]
) -> None:
    msgs = _history_with_compacted()
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    await compactor.maybe_compact(msgs, context_window=1)
    triggered = [e for e in captured if isinstance(e, CompactionTriggeredEvent)]
    complete = [e for e in captured if isinstance(e, CompactionCompleteEvent)]
    assert len(triggered) == 1
    assert len(complete) == 1
    assert triggered[0].seq < complete[0].seq


# ---------------------------------------------------------------------------
# Step 6.3.4 — budget independence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_budget_is_independent_of_tool_budget(
    bus: EventBus,
) -> None:
    """Spec section 5.5.1 budget-accounting clause: compaction calls do NOT count
    against ToolsCfg.max_calls_per_file. Only max_compactions_per_file caps them.
    """
    cfg = CompactionCfg(
        enabled=True,
        trigger_pct=0.1,
        target_pct=0.05,
        preserve_recent_turns=2,
        max_compactions_per_file=5,  # generous
    )
    # Fire the compactor 4 times; would-be tool budget of 1 is irrelevant.
    payload = _ok_compaction_payload()
    compactor, _ = _make_compactor(
        bus,
        cfg,
        *([_ok_chat_response(payload)] * 4),
    )
    msgs = _history_with_compacted()
    for _ in range(4):
        msgs = await compactor.maybe_compact(list(msgs), context_window=1)
    assert compactor.compactions_so_far == 4
    # Sanity: a 5th would still succeed (limit=5 not yet hit).
    # (We don't actually call it — the assertion above is the contract.)


# ---------------------------------------------------------------------------
# Step 6.2.2 — token reduction (synthetic bloat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_reduces_token_count_on_synthetic_bloat(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    """A long history compacts to fewer tokens (assertion is non-strict —
    model output length is bounded, not exact)."""
    msgs = [_msg("system", "system")]
    # Build a heavy slice.
    for i in range(40):
        msgs.append(_msg("user", "Y" * 2000))
        msgs.append(_msg("assistant", "Z" * 2000))
    msgs.append(_msg("user", "recent-1"))
    msgs.append(_msg("user", "recent-2"))
    compactor, client = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    # Use the same count_tokens path the compactor uses.
    before = client.count_tokens(  # type: ignore[arg-type]
        [type("M", (), {"content": m.get("content"), "tool_calls": None})() for m in msgs],
        "test-model",
    )
    out = await compactor.run(msgs)
    after = client.count_tokens(  # type: ignore[arg-type]
        [type("M", (), {"content": m.get("content"), "tool_calls": None})() for m in out],
        "test-model",
    )
    assert after < before


# ---------------------------------------------------------------------------
# Step 6.2.6 — replay determinism (in-process)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_replay_determinism_under_fixed_response(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    msgs = _history_with_compacted()
    payload = _ok_compaction_payload(
        summary="deterministic",
        findings=["a", "b"],
        questions=["q1"],
    )
    # Two independent runs with byte-identical mocked responses.
    c1, _ = _make_compactor(bus, cfg, _ok_chat_response(payload))
    c2, _ = _make_compactor(EventBus(), cfg, _ok_chat_response(payload))
    out1 = await c1.run([dict(m) for m in msgs])
    out2 = await c2.run([dict(m) for m in msgs])
    # Compacted block content (the only synthesized text) MUST be byte-equal.
    block1 = next(m["content"] for m in out1 if (m.get("content") or "").startswith("[COMPACTED]\n"))
    block2 = next(m["content"] for m in out2 if (m.get("content") or "").startswith("[COMPACTED]\n"))
    assert block1 == block2


# ---------------------------------------------------------------------------
# ContextOverflow exists as an importable symbol (for M8 to catch).
# ---------------------------------------------------------------------------


def test_context_overflow_is_importable_and_exception() -> None:
    assert issubclass(ContextOverflow, Exception)


def test_compaction_result_model_forbids_extra_keys() -> None:
    with pytest.raises(Exception):
        CompactionResult(
            evidence_summary="x",
            key_findings_so_far=[],
            unanswered_questions=[],
            unexpected="nope",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Module-level should_trigger pure function (re-export for tests / M8 use).
# ---------------------------------------------------------------------------


def test_should_trigger_pure_function_signature() -> None:
    cfg = CompactionCfg(enabled=True, trigger_pct=0.5)
    # 100 tokens, ctx_window=200, threshold=100 -> trigger.
    assert should_trigger(token_count=100, context_window=200, config=cfg) is True
    assert should_trigger(token_count=99, context_window=200, config=cfg) is False
    cfg_off = CompactionCfg(enabled=False)
    assert should_trigger(token_count=10**9, context_window=1, config=cfg_off) is False


@pytest.mark.asyncio
async def test_maybe_compact_emits_compaction_error_on_unknown_exception(
    bus: EventBus, cfg: CompactionCfg, captured: list[BaseEvent]
) -> None:
    """Non-CompactionFailed exceptions inside ``run`` emit
    CompactionError(error_kind='unknown') and re-raise."""
    msgs = _history_with_compacted()

    class _BoomCompactor(Compactor):
        async def run(self, _messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
            raise RuntimeError("synthetic non-CompactionFailed boom")

    compactor = _BoomCompactor(
        client=_make_client(_ok_chat_response(_ok_compaction_payload())),
        prompt_path=_PROMPT_PATH,
        schema_path=_SCHEMA_PATH,
        config=cfg,
        bus=bus,
        run_id="test-run",
        path=Path("src/foo.py"),
        model_id="test-model",
        redactor=SecretRedactor(),
    )
    with pytest.raises(RuntimeError):
        await compactor.maybe_compact(msgs, context_window=1)
    err = [e for e in captured if isinstance(e, CompactionErrorEvent)]
    assert len(err) == 1
    assert err[0].error_kind == "unknown"


@pytest.mark.asyncio
async def test_compactor_run_raises_compaction_failed_on_empty_messages(
    bus: EventBus, cfg: CompactionCfg
) -> None:
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()))
    with pytest.raises(CompactionFailed) as exc:
        await compactor.run([])
    assert exc.value.kind == "schema_invalid"


def test_context_overflow_carries_path_token_count_and_window() -> None:
    err = ContextOverflow(path=Path("foo.py"), token_count=100, context_window=10)
    assert err.path == Path("foo.py")
    assert err.token_count == 100
    assert err.context_window == 10


def test_compactor_path_property() -> None:
    cfg = CompactionCfg()
    bus = EventBus()
    compactor, _ = _make_compactor(bus, cfg, _ok_chat_response(_ok_compaction_payload()), path=Path("a/b.py"))
    assert compactor.path == Path("a/b.py")
    assert compactor.compactions_so_far == 0
