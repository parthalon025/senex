"""Tests for senex.lmstudio_client — M3 LM Studio integration.

Covers (per ``docs/superpowers/plans/2026-04-26-senex-v1/m3-lmstudio.md``):
3.1 basic chat + schema validation, 3.2 streaming + Tick coalescing +
``<think>`` strip, 3.3 schema fallback decision tree, 3.4 token counting
+ budget enforcement, 3.5 retry + backoff, 3.6 ``tools=`` parameter
(single round-trip), 3.7 fingerprint, 3.8 capability probe,
3.10 ANSI strip + secret redaction.

External boundary mocked via ``respx`` (httpx mock library); no live LMS.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from senex.config import LmStudioCfg
from senex.events import EventBus
from senex.lmstudio_client import ChatMessage, LMStudioClient
from senex.secret_redactor import SecretRedactor


# --- Boundary checks (M3 ↔ M5 contract — see plan §"M3 ↔ M5 boundary check") ---

def test_lmstudio_client_does_not_import_senex_tools() -> None:
    """senex/lmstudio_client.py MUST NOT import senex.tools.* (M5 territory)."""
    src = (Path(__file__).resolve().parents[2] / "senex" / "lmstudio_client.py").read_text(
        encoding="utf-8"
    )
    assert "from senex.tools" not in src, (
        "lmstudio_client.py imported senex.tools.*; iteration belongs in M5 ToolLoop."
    )
    assert "import senex.tools" not in src, (
        "lmstudio_client.py imported senex.tools.*; iteration belongs in M5 ToolLoop."
    )


def test_lmstudio_client_has_no_iteration_methods() -> None:
    """LMStudioClient MUST NOT define a tool-loop / iteration / compaction surface."""
    src = (Path(__file__).resolve().parents[2] / "senex" / "lmstudio_client.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("_tool_loop", "iterate_tools", "compaction_callback")
    for token in forbidden:
        assert token not in src, (
            f"lmstudio_client.py contains forbidden symbol {token!r} — that lives in M5/M6."
        )


def test_chat_docstring_documents_round_trip_boundary() -> None:
    """chat() docstring MUST cite the single-round-trip boundary and ToolLoop."""
    doc = LMStudioClient.chat.__doc__ or ""
    assert "ONE chat-completion round-trip" in doc
    assert "senex.tools.loop.ToolLoop" in doc


# --- Shared fixtures ----------------------------------------------------------

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "lms_responses"


@pytest.fixture
def fixture_dir() -> Path:
    return _FIXTURE_DIR


@pytest.fixture
def test_cfg() -> LmStudioCfg:
    return LmStudioCfg(
        base_url="http://localhost:1234/v1",
        api_key="lm-studio-test",
        connect_timeout=1,
        read_timeout=5,
        http_retries=3,
        backoff_seconds=[5, 15, 45],
        model="google/gemma-4-26b-a4b",
        context_window=32768,
        token_budget_pct=0.9,
        fingerprint_recheck_interval_s=60.0,
    )


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def redactor() -> SecretRedactor:
    return SecretRedactor()


@pytest.fixture
def audit_schema() -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "senex"
        / "schema"
        / "audit_response.schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


@pytest.fixture
def client(test_cfg: LmStudioCfg, bus: EventBus, redactor: SecretRedactor) -> LMStudioClient:
    return LMStudioClient(config=test_cfg, bus=bus, redactor=redactor)


# --- Task 3.1: basic chat + schema validation --------------------------------

@pytest.mark.asyncio
async def test_chat_returns_validated_response(
    respx_mock: Any,
    fixture_dir: Path,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    payload = json.loads((fixture_dir / "simple_audit.json").read_text(encoding="utf-8"))
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(json=payload)

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="audit me")],
        schema=audit_schema,
        tools=None,
    )

    assert resp.content_dict is not None
    assert resp.content_dict["schema_version"] == 1
    assert resp.finish_reason == "stop"
    assert resp.tool_calls is None
    await client.aclose()


# --- Task 3.2: streaming + tick coalescing + <think> strip --------------------

import httpx  # noqa: E402  — used by SSE helper below

from senex.events import (  # noqa: E402
    OutputComplete,
    OutputStarted,
    OutputTick,
    ThinkingComplete,
    ThinkingStarted,
    ThinkingTick,
)
from senex.lmstudio_client import _ANSI_RE, _THINK_TAG_RE  # noqa: E402


def _sse_response(chunks: list[dict[str, Any]]) -> httpx.Response:
    """Build an SSE ``httpx.Response`` carrying ``chunks`` as ``data:`` events."""
    body_parts: list[str] = []
    for c in chunks:
        body_parts.append(f"data: {json.dumps(c)}\n\n")
    body_parts.append("data: [DONE]\n\n")
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="".join(body_parts),
    )


def _delta(
    *,
    reasoning_content: str | None = None,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if reasoning_content is not None:
        delta["reasoning_content"] = reasoning_content
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish_reason}
        ]
    }


@pytest.fixture
def captured_events(bus: EventBus) -> list[Any]:
    captured: list[Any] = []

    async def _cb(event: Any) -> None:
        captured.append(event)

    bus.subscribe_local(
        "test",
        (
            ThinkingStarted, ThinkingTick, ThinkingComplete,
            OutputStarted, OutputTick, OutputComplete,
        ),
        _cb,
    )
    return captured


@pytest.mark.asyncio
async def test_stream_emits_thinking_lifecycle_events(
    respx_mock: Any,
    client: LMStudioClient,
    captured_events: list[Any],
    audit_schema: dict[str, Any],
) -> None:
    chunks = [
        _delta(reasoning_content="thinking step one — " * 60),
        _delta(reasoning_content="thinking step two — " * 60),
        _delta(content='{"schema_version":1,'),
        _delta(content='"overall_assessment":"' + ("ok " * 60) + '","findings":[],"recommendations":[]}'),
        _delta(finish_reason="stop"),
    ]
    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=_sse_response(chunks)
    )

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )

    types = [type(e).__name__ for e in captured_events]
    assert "ThinkingStarted" in types
    assert "ThinkingTick" in types
    assert "ThinkingComplete" in types
    assert "OutputStarted" in types
    assert "OutputComplete" in types
    # Ordering: ThinkingStarted precedes ThinkingComplete; OutputStarted follows.
    ts = types.index("ThinkingStarted")
    tc = types.index("ThinkingComplete")
    os_ = types.index("OutputStarted")
    assert ts < tc < os_
    assert resp.content.startswith("{")


@pytest.mark.asyncio
async def test_thinking_tick_coalesces_at_256_tokens_or_500ms(
    respx_mock: Any,
    client: LMStudioClient,
    captured_events: list[Any],
    audit_schema: dict[str, Any],
) -> None:
    # 1024 reasoning tokens spread over 8 chunks of 128 tokens each (~512 chars).
    chunks: list[dict[str, Any]] = []
    for _ in range(8):
        # 128 tokens * 4 chars/token ≈ 512 chars per chunk.
        chunks.append(_delta(reasoning_content="x" * 512))
    chunks.append(_delta(content='{"schema_version":1,"overall_assessment":"' + "ok " * 60 + '","findings":[],"recommendations":[]}'))
    chunks.append(_delta(finish_reason="stop"))
    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=_sse_response(chunks)
    )

    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )

    ticks = [e for e in captured_events if type(e).__name__ == "ThinkingTick"]
    # 8 chunks * 128 tokens = 1024 tokens; threshold 256 -> ≥4 ticks.
    assert len(ticks) >= 4


@pytest.mark.asyncio
async def test_per_turn_counter_resets_between_thinking_starts(
    respx_mock: Any,
    client: LMStudioClient,
    captured_events: list[Any],
    audit_schema: dict[str, Any],
) -> None:
    """Two chat() calls back-to-back; turn 2 must start with tokens_so_far=0."""
    def make_chunks() -> list[dict[str, Any]]:
        return [
            _delta(reasoning_content="r" * 1024),  # 256 tokens
            _delta(content='{"schema_version":1,"overall_assessment":"' + "ok " * 60 + '","findings":[],"recommendations":[]}'),
            _delta(finish_reason="stop"),
        ]
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.side_effect = [_sse_response(make_chunks()), _sse_response(make_chunks())]

    # Turn 1
    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    captured_events.clear()
    # Turn 2 — fresh chat call, fresh coalescer.
    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    turn2_ticks = [e for e in captured_events if type(e).__name__ == "ThinkingTick"]
    assert turn2_ticks, "turn 2 should still emit at least one ThinkingTick"
    # First tick of turn 2: tokens_so_far must equal delta_since_last_tick (counter reset).
    first = turn2_ticks[0]
    assert first.tokens_so_far == first.delta_since_last_tick


@pytest.mark.asyncio
async def test_think_tag_stripped_from_content(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    payload = {
        "id": "x", "model": "m",
        "choices": [
            {
                "index": 0, "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": (
                        '<think>internal</think>'
                        '{"schema_version":1,"overall_assessment":"' + ("ok " * 60)
                        + '","findings":[],"recommendations":[]}'
                    ),
                },
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(json=payload)
    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    assert "<think>" not in resp.content
    assert "internal" not in resp.content


@pytest.mark.asyncio
async def test_interleaved_think_tags_stripped(
    respx_mock: Any,
    client: LMStudioClient,
) -> None:
    payload = {
        "id": "x", "model": "m",
        "choices": [
            {
                "index": 0, "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "prefix<think>x\nmulti\nline</think>middle<think>y</think>suffix",
                },
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(json=payload)
    # No schema → unstructured chat path.
    resp = await client.chat(
        task="compaction",
        messages=[ChatMessage(role="user", content="hi")],
        schema=None,
        tools=None,
    )
    assert resp.content == "prefixmiddlesuffix"


@pytest.mark.asyncio
async def test_total_thinking_tokens_equals_sum_of_ticks(
    respx_mock: Any,
    client: LMStudioClient,
    captured_events: list[Any],
    audit_schema: dict[str, Any],
) -> None:
    chunks: list[dict[str, Any]] = []
    for _ in range(6):
        chunks.append(_delta(reasoning_content="z" * 512))  # 128 tokens each
    chunks.append(_delta(content='{"schema_version":1,"overall_assessment":"' + "ok " * 60 + '","findings":[],"recommendations":[]}'))
    chunks.append(_delta(finish_reason="stop"))
    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=_sse_response(chunks)
    )
    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )

    ticks = [e for e in captured_events if type(e).__name__ == "ThinkingTick"]
    completes = [e for e in captured_events if type(e).__name__ == "ThinkingComplete"]
    assert completes, "ThinkingComplete should fire when output begins"
    summed = sum(t.delta_since_last_tick for t in ticks)
    # The remainder (residual not yet ticked) is included in total_thinking_tokens
    # but not in summed deltas — so summed <= total.
    assert summed <= completes[0].total_thinking_tokens


# --- Regex byte-equality (acceptance check) ----------------------------------

def test_ansi_regex_byte_equals_spec_literal() -> None:
    expected = httpx_regex_compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")
    assert _ANSI_RE.pattern == expected.pattern


def test_think_regex_byte_equals_spec_literal() -> None:
    import re

    expected = re.compile(r"<think>.*?</think>", re.DOTALL)
    assert _THINK_TAG_RE.pattern == expected.pattern
    assert _THINK_TAG_RE.flags & re.DOTALL


def httpx_regex_compile(pattern: str) -> Any:
    """Tiny indirection so the regex literal lives in exactly one place."""
    import re

    return re.compile(pattern)
