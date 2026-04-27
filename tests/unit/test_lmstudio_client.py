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


# --- Task 3.3: schema fallback decision tree ---------------------------------

from senex.lmstudio_errors import (  # noqa: E402
    LMSResponseInvalidJSON,
    LMSResponseSchemaInvalid,
    SchemaNegotiationFailed,
)


def _valid_audit_json() -> str:
    return (
        '{"schema_version":1,"overall_assessment":"'
        + ("ok " * 60)
        + '","findings":[],"recommendations":[]}'
    )


def _success_payload(content: str) -> dict[str, Any]:
    return {
        "id": "x",
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


@pytest.mark.asyncio
async def test_schema_fallback_on_400_schema_error(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """First request gets 400 schema error; client falls back to json_object."""
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.side_effect = [
        httpx.Response(
            400,
            json={
                "error": {"message": "response_format json_schema not supported"}
            },
        ),
        httpx.Response(200, json=_success_payload(_valid_audit_json())),
    ]

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )

    assert resp.content_dict is not None
    assert resp.content_dict["schema_version"] == 1
    # Cached decision is observable on the instance.
    assert client._schema_mode == "json_object"  # type: ignore[attr-defined]
    # Exactly two requests issued (json_schema attempt + json_object retry).
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_schema_fallback_caches_decision_per_session(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """After first fallback, subsequent calls skip the json_schema probe."""
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.side_effect = [
        httpx.Response(
            400, json={"error": {"message": "response_format unsupported"}}
        ),
        httpx.Response(200, json=_success_payload(_valid_audit_json())),
        httpx.Response(200, json=_success_payload(_valid_audit_json())),
    ]

    # Turn 1 — provokes fallback (2 requests).
    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    after_first = route.call_count
    # Turn 2 — cached json_object; exactly 1 request.
    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    assert after_first == 2
    assert route.call_count == 3


@pytest.mark.asyncio
async def test_schema_fallback_400_unrelated_does_not_fallback(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """A 400 unrelated to schema (e.g. context length) propagates as HTTPStatusError."""
    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "context length exceeded"}}
        )
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat(
            task="file_audit",
            messages=[ChatMessage(role="user", content="hi")],
            schema=audit_schema,
            tools=None,
        )
    # Mode was NOT cached.
    assert client._schema_mode is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_post_hoc_validation_failure_raises_LMSResponseSchemaInvalid(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """In json_object mode a malformed schema_version triggers LMSResponseSchemaInvalid."""
    # Pre-set fallback so we hit the json_object path directly; the strict retry
    # will fire once, returning the same bad response → final raise.
    client._schema_mode = "json_object"  # type: ignore[attr-defined]
    bad = '{"schema_version":"wrong_type","overall_assessment":"x","findings":[],"recommendations":[]}'
    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_success_payload(bad))
    )
    with pytest.raises(LMSResponseSchemaInvalid):
        await client.chat(
            task="file_audit",
            messages=[ChatMessage(role="user", content="hi")],
            schema=audit_schema,
            tools=None,
        )


@pytest.mark.asyncio
async def test_invalid_json_after_think_strip_raises_LMSResponseInvalidJSON(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """Content that isn't valid JSON after think-strip raises LMSResponseInvalidJSON."""
    client._schema_mode = "json_object"  # type: ignore[attr-defined]
    payload = _success_payload("<think>noise</think>{not valid json")
    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with pytest.raises(LMSResponseInvalidJSON):
        await client.chat(
            task="file_audit",
            messages=[ChatMessage(role="user", content="hi")],
            schema=audit_schema,
            tools=None,
        )


@pytest.mark.asyncio
async def test_schema_invalid_retried_once_with_stricter_prompt(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """First response fails schema validation; second (with strict preamble) succeeds."""
    from senex.lmstudio_client import STRICT_RETRY_PREAMBLE

    client._schema_mode = "json_object"  # type: ignore[attr-defined]
    bad = '{"schema_version":"wrong","overall_assessment":"x","findings":[],"recommendations":[]}'
    captured_bodies: list[dict[str, Any]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content.decode("utf-8")))
        # First attempt returns invalid; second returns valid.
        if len(captured_bodies) == 1:
            return httpx.Response(200, json=_success_payload(bad))
        return httpx.Response(200, json=_success_payload(_valid_audit_json()))

    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(side_effect=_record)

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    assert resp.content_dict is not None
    assert resp.content_dict["schema_version"] == 1
    # Two HTTP calls; second's system message embeds the strict preamble.
    assert len(captured_bodies) == 2
    second_messages = captured_bodies[1]["messages"]
    sys_msgs = [m for m in second_messages if m.get("role") == "system"]
    assert sys_msgs, "strict retry should inject a system message"
    assert any(STRICT_RETRY_PREAMBLE in (m.get("content") or "") for m in sys_msgs)


# --- Task 3.4: pre-LMS token counting + budget enforcement ------------------

from senex.lmstudio_errors import TokenBudgetExceeded  # noqa: E402


def test_count_tokens_returns_positive_for_nonempty_messages(
    client: LMStudioClient,
) -> None:
    msgs = [
        ChatMessage(role="system", content="be concise"),
        ChatMessage(role="user", content="hello world"),
        ChatMessage(role="assistant", content="hi"),
    ]
    assert client.count_tokens(msgs, "google/gemma-4-26b-a4b") > 0


def test_count_tokens_grows_monotonically_with_content_length(
    client: LMStudioClient,
) -> None:
    short = [ChatMessage(role="user", content="x" * 10)]
    long = [ChatMessage(role="user", content="x" * 100)]
    assert client.count_tokens(long, "m") > client.count_tokens(short, "m")


def test_count_tokens_caches_encoder_per_model(
    client: LMStudioClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two calls with the same model_id → tiktoken.get_encoding called once."""
    import tiktoken

    calls = {"n": 0}
    real = tiktoken.get_encoding

    def _spy(name: str) -> Any:
        calls["n"] += 1
        return real(name)

    monkeypatch.setattr(tiktoken, "get_encoding", _spy)
    # Reset the encoder cache so the spy actually gets the first call.
    client._encoder_cache.clear()  # type: ignore[attr-defined]
    msgs = [ChatMessage(role="user", content="hello")]
    client.count_tokens(msgs, "model-a")
    client.count_tokens(msgs, "model-a")
    assert calls["n"] == 1


def test_count_tokens_unknown_model_falls_back_to_chars_div_4(
    client: LMStudioClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When tiktoken cannot supply an encoder, count_tokens uses len/4 + per-msg overhead."""
    import tiktoken

    def _boom(_name: str) -> Any:
        raise RuntimeError("encoding unavailable")

    monkeypatch.setattr(tiktoken, "get_encoding", _boom)
    client._encoder_cache.clear()  # type: ignore[attr-defined]
    text = "x" * 100
    msgs = [ChatMessage(role="user", content=text)]
    # Expected: len(text) // 4 + 4 per-message overhead = 25 + 4 = 29.
    expected_low = len(text) // 4 + 4 - 1
    expected_high = len(text) // 4 + 4 + 1
    actual = client.count_tokens(msgs, "completely-fake-model")
    assert expected_low <= actual <= expected_high
    # And None is cached (per plan pitfall: do not retry tiktoken once it fails).
    assert client._encoder_cache["completely-fake-model"] is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_chat_raises_token_budget_exceeded_above_90_percent(
    respx_mock: Any,
    test_cfg: LmStudioCfg,
    bus: EventBus,
    redactor: SecretRedactor,
    audit_schema: dict[str, Any],
) -> None:
    """Pre-flight token budget fires BEFORE any HTTP request."""
    # context_window=1000 → budget=900. Build messages well above that.
    tight_cfg = test_cfg.model_copy(update={"context_window": 1000})
    tight_client = LMStudioClient(config=tight_cfg, bus=bus, redactor=redactor)

    # ~6000 chars of payload → ≥1500 tiktoken tokens → above 900 budget.
    huge = "lorem ipsum " * 500
    msgs = [ChatMessage(role="user", content=huge)]

    # Mock the endpoint just to detect any leak: the test asserts respx received zero hits.
    route = respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_success_payload(_valid_audit_json())
    )

    with pytest.raises(TokenBudgetExceeded):
        await tight_client.chat(
            task="file_audit",
            messages=msgs,
            schema=audit_schema,
            tools=None,
        )
    assert route.call_count == 0
    await tight_client.aclose()


# --- Task 3.5: retry + backoff policy ---------------------------------------

from senex.lmstudio_errors import LMSConnectionLost  # noqa: E402


@pytest.fixture
def sleep_recorder(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Patch asyncio.sleep to a recording fake; tests assert backoff cadence."""
    import asyncio as _asyncio

    recorded: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)
    return recorded


@pytest.mark.asyncio
async def test_5xx_retried_with_correct_backoff(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
    sleep_recorder: list[float],
) -> None:
    """503×3 then 200; backoff sleeps assert [5, 15, 45]."""
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.side_effect = [
        httpx.Response(503, json={"error": "down"}),
        httpx.Response(503, json={"error": "down"}),
        httpx.Response(503, json={"error": "down"}),
        httpx.Response(200, json=_success_payload(_valid_audit_json())),
    ]
    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    assert resp.content_dict is not None
    assert sleep_recorder == [5, 15, 45]
    assert route.call_count == 4


@pytest.mark.asyncio
async def test_5xx_exhausted_after_3_retries_reraises(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
    sleep_recorder: list[float],
) -> None:
    """503×4 → HTTPStatusError after exactly 3 retries (4 total requests)."""
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.side_effect = [httpx.Response(503, json={"e": "x"})] * 4
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat(
            task="file_audit",
            messages=[ChatMessage(role="user", content="hi")],
            schema=audit_schema,
            tools=None,
        )
    assert route.call_count == 4  # 1 initial + 3 retries
    assert sleep_recorder == [5, 15, 45]  # no sleep on the final attempt


@pytest.mark.asyncio
async def test_4xx_not_retried(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
    sleep_recorder: list[float],
) -> None:
    """401 fails fast — exactly 1 request, no sleeps."""
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.return_value = httpx.Response(401, json={"error": "unauthorized"})
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat(
            task="file_audit",
            messages=[ChatMessage(role="user", content="hi")],
            schema=audit_schema,
            tools=None,
        )
    assert route.call_count == 1
    assert sleep_recorder == []


@pytest.mark.asyncio
async def test_read_timeout_retried_like_5xx(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
    sleep_recorder: list[float],
) -> None:
    """ReadTimeout twice then 200; backoffs [5, 15]; success on third attempt."""
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.side_effect = [
        httpx.ReadTimeout("timeout 1"),
        httpx.ReadTimeout("timeout 2"),
        httpx.Response(200, json=_success_payload(_valid_audit_json())),
    ]
    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    assert resp.content_dict is not None
    assert sleep_recorder == [5, 15]
    assert route.call_count == 3


@pytest.mark.asyncio
async def test_connect_error_raises_LMSConnectionLost(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
    sleep_recorder: list[float],
) -> None:
    """httpx.ConnectError → LMSConnectionLost (not bare httpx exception)."""
    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(LMSConnectionLost):
        await client.chat(
            task="file_audit",
            messages=[ChatMessage(role="user", content="hi")],
            schema=audit_schema,
            tools=None,
        )
    # ConnectError raises immediately — no sleeps, no retries.
    assert sleep_recorder == []


@pytest.mark.asyncio
async def test_reprobe_until_alive_polls_models_every_10s(
    respx_mock: Any,
    client: LMStudioClient,
    sleep_recorder: list[float],
) -> None:
    """ConnectError ×2 then 200 on /v1/models; two sleeps of 10s, returns clean."""
    route = respx_mock.get("http://localhost:1234/v1/models")
    route.side_effect = [
        httpx.ConnectError("down"),
        httpx.ConnectError("down"),
        httpx.Response(200, json={"data": []}),
    ]
    await client._reprobe_until_alive(interval_s=10.0)  # type: ignore[attr-defined]
    # Loop iterations 1,2 sleep 10s after the failed probe; iteration 3 succeeds
    # before the sleep is reached. So exactly two sleeps of 10s.
    assert sleep_recorder == [10.0, 10.0]
    assert route.call_count == 3


@pytest.mark.asyncio
async def test_reprobe_until_alive_bounded_by_max_iterations(
    respx_mock: Any,
    client: LMStudioClient,
    sleep_recorder: list[float],
) -> None:
    """After max_iterations failed probes, raise LMSConnectionLost (§3 conventions)."""
    respx_mock.get("http://localhost:1234/v1/models").mock(
        side_effect=httpx.ConnectError("never up")
    )
    with pytest.raises(LMSConnectionLost):
        await client._reprobe_until_alive(  # type: ignore[attr-defined]
            interval_s=1.0, max_iterations=3
        )
    # 3 iterations × 1s sleep each.
    assert sleep_recorder == [1.0, 1.0, 1.0]


@pytest.mark.asyncio
async def test_schema_negotiation_failed_when_both_modes_refused(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """Both json_schema and json_object 4xx → SchemaNegotiationFailed."""
    route = respx_mock.post("http://localhost:1234/v1/chat/completions")
    route.side_effect = [
        httpx.Response(400, json={"error": {"message": "response_format invalid"}}),
        httpx.Response(400, json={"error": {"message": "json_object also rejected"}}),
    ]
    with pytest.raises(SchemaNegotiationFailed):
        await client.chat(
            task="file_audit",
            messages=[ChatMessage(role="user", content="hi")],
            schema=audit_schema,
            tools=None,
        )
