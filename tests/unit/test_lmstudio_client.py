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


def test_lmstudio_client_implements_llm_client_protocol() -> None:
    """LMStudioClient satisfies the senex.llm_client.LLMClient structural protocol."""
    from senex.llm_client import LLMClient

    cfg = LmStudioCfg(
        base_url="http://localhost:1234/v1",
        api_key="x",
        connect_timeout=1,
        read_timeout=1,
        http_retries=0,
        backoff_seconds=[],
        model="m",
        context_window=1024,
        token_budget_pct=0.9,
        fingerprint_recheck_interval_s=60.0,
    )
    inst = LMStudioClient(config=cfg, bus=EventBus(), redactor=SecretRedactor())
    assert isinstance(inst, LLMClient)


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
    # M11 bug 4: inline <think> blocks captured into reasoning_content so
    # ``save_traces`` can write them to ``*.thinking.md`` for models that
    # emit thinking inline (e.g. gemma) instead of via a sidecar
    # ``reasoning_content`` field.
    assert "x\nmulti\nline" in resp.reasoning_content
    assert "y" in resp.reasoning_content


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
    """First request gets 400 schema error; client falls back to json_object.

    v1.0.2: this strict-mode probe-then-fallback behavior is gated on
    ``strict_json_schema=True`` (the default flipped to ``False`` in v1.0.2
    because Gemma is unreliable in strict mode). Opt back in here so the
    fallback decision tree is still exercised.
    """
    client._config.strict_json_schema = True  # type: ignore[attr-defined]
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
    """After first fallback, subsequent calls skip the json_schema probe.

    v1.0.2: opt into strict mode so the probe-then-fallback path runs.
    """
    client._config.strict_json_schema = True  # type: ignore[attr-defined]
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
    """A 400 unrelated to schema (e.g. context length) propagates as HTTPStatusError.

    v1.0.2: opt into strict mode so the json_schema probe actually runs and
    can surface the unrelated 400.
    """
    client._config.strict_json_schema = True  # type: ignore[attr-defined]
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
    """Both json_schema and json_object 4xx → SchemaNegotiationFailed.

    v1.0.2: opt into strict mode so the probe-then-fallback decision tree
    is exercised. (The default ``strict_json_schema=False`` skips the
    initial json_schema probe and would never observe the negotiation
    failure on a 4xx-then-4xx sequence.)
    """
    client._config.strict_json_schema = True  # type: ignore[attr-defined]
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


# --- Task 3.6: tools= parameter (single round-trip) -------------------------

_READ_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}


@pytest.mark.asyncio
async def test_chat_with_tools_returns_tool_calls(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """tool_calls populated; finish_reason=tool_calls; no recursion."""
    payload = {
        "id": "x",
        "model": "gemma",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"a.py"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(json=payload)

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="audit a.py")],
        schema=audit_schema,
        tools=[_READ_FILE_TOOL],
    )

    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].function.name == "read_file"
    assert resp.tool_calls[0].function.arguments == '{"path":"a.py"}'
    # No JSON content yet — schema validation skipped on tool_calls finish.
    assert resp.content_dict is None


@pytest.mark.asyncio
async def test_chat_with_tools_passes_tools_and_tool_choice_to_request(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """Request body MUST contain tools verbatim and tool_choice='auto'."""
    captured_bodies: list[dict[str, Any]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_success_payload(_valid_audit_json()))

    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(side_effect=_record)

    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=[_READ_FILE_TOOL],
    )

    assert len(captured_bodies) == 1
    body = captured_bodies[0]
    assert body.get("tools") == [_READ_FILE_TOOL]
    assert body.get("tool_choice") == "auto"


@pytest.mark.asyncio
async def test_chat_with_tools_and_schema_omits_response_format(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """v1.0.1 fix: when tools AND schema are both supplied, the client must
    pre-emptively switch to a no-server-enforcement mode. Background:

    * Gemma + ``response_format=json_schema`` + ``tools`` returns empty
      content (the model picks one path or the other, drops the schema one).
    * LM Studio (current) 400s on ``response_format={"type":"json_object"}``
      — its OpenAI-compat surface only accepts ``json_schema`` or ``text``.

    Workaround: omit ``response_format`` entirely on this path, prepend a
    system-prompt JSON reminder, then validate the response post-hoc with
    Pydantic. Tools and tool_choice still go on the wire so the model can
    request file reads, etc.
    """
    captured_bodies: list[dict[str, Any]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_success_payload(_valid_audit_json()))

    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(side_effect=_record)

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=[_READ_FILE_TOOL],
    )

    # Exactly one round-trip — no probe-then-fallback.
    assert len(captured_bodies) == 1
    body = captured_bodies[0]
    assert body.get("tools") == [_READ_FILE_TOOL]
    assert body.get("tool_choice") == "auto"
    # Critical: response_format MUST be omitted when tools are present —
    # LM Studio rejects "json_object" outright and "json_schema" empties
    # the response on Gemma. Validation is post-hoc via Pydantic instead.
    assert "response_format" not in body
    # System-prompt reminder is the JSON-coercion mechanism on this path.
    system_msgs = [m for m in body.get("messages", []) if m.get("role") == "system"]
    assert system_msgs, "JSON reminder must be injected as a system message"
    assert "JSON" in str(system_msgs[0].get("content", ""))
    # The session-wide cache MUST NOT be flipped by this per-call adaptation:
    # subsequent tool-less calls may still use json_schema where supported.
    assert client._schema_mode is None  # type: ignore[attr-defined]
    # And the response was still parsed + validated post-hoc.
    assert resp.content_dict is not None
    assert resp.content_dict["schema_version"] == 1


@pytest.mark.asyncio
async def test_strict_json_schema_false_uses_json_object_when_tools_none(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """v1.0.2 fix: with ``strict_json_schema=False`` (the default) and
    ``tools=None``, the request MUST go out without
    ``response_format=json_schema`` — i.e., go through the json_object +
    post-hoc Pydantic validation path. Background:

    * Gemma + LM Studio + ``response_format=json_schema`` returns empty
      content even on tools-less calls (the strict structured-output
      enforcement is fragile across both tools-bearing and tools-less
      paths). The v1.0.1 fix only covered tools+schema; v1.0.2 extends
      it to ALL Gemma calls.
    * LM Studio's OpenAI-compat surface 400s on ``response_format`` of
      ``json_object`` literal, so the workaround is to omit
      ``response_format`` entirely and rely on the system-prompt JSON
      reminder + post-hoc validation.

    Backends that honor strict schema reliably (OpenAI / Together / Groq)
    can opt back in with ``[lmstudio].strict_json_schema = true`` — that
    path is covered by ``test_schema_fallback_on_400_schema_error`` and
    friends.
    """
    # Sanity: default config has strict mode off (v1.0.2).
    assert client._config.strict_json_schema is False  # type: ignore[attr-defined]

    captured_bodies: list[dict[str, Any]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_success_payload(_valid_audit_json()))

    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(side_effect=_record)

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )

    # Exactly one round-trip — no probe-then-fallback (the v1.0.1 path
    # would have done two: json_schema attempt then json_object retry).
    assert len(captured_bodies) == 1
    body = captured_bodies[0]
    # Critical assertion: response_format MUST NOT be json_schema. Either
    # omitted entirely or set to "text"; the empty-response bug is
    # specifically triggered by json_schema strict mode on Gemma.
    rf = body.get("response_format")
    if rf is not None:
        assert rf.get("type") != "json_schema", (
            "strict_json_schema=False must not send response_format=json_schema "
            "on tools-less calls (Gemma returns empty content)"
        )
    # System-prompt reminder is the JSON-coercion mechanism on this path.
    system_msgs = [m for m in body.get("messages", []) if m.get("role") == "system"]
    assert system_msgs, "JSON reminder must be injected as a system message"
    assert "JSON" in str(system_msgs[0].get("content", ""))
    # No tools / tool_choice when tools=None.
    assert "tools" not in body
    assert "tool_choice" not in body
    # The session-wide cache is NOT flipped — strict_json_schema is the
    # config-level switch, the cache is only for hard 400 fallbacks.
    assert client._schema_mode is None  # type: ignore[attr-defined]
    # Post-hoc validation still runs and succeeds on the valid payload.
    assert resp.content_dict is not None
    assert resp.content_dict["schema_version"] == 1


@pytest.mark.asyncio
async def test_chat_without_tools_omits_tools_field(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """When tools=None or [] the request MUST NOT include 'tools' or 'tool_choice'."""
    captured_bodies: list[dict[str, Any]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_success_payload(_valid_audit_json()))

    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(side_effect=_record)

    # tools=None
    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    # tools=[]
    await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=[],
    )

    assert len(captured_bodies) == 2
    for body in captured_bodies:
        assert "tools" not in body
        assert "tool_choice" not in body


@pytest.mark.asyncio
async def test_chat_streamed_tool_calls_assembled_correctly(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """SSE delivers tool_calls deltas progressively; final arguments must be assembled."""
    chunks = [
        _delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function"}]),
        _delta(tool_calls=[{"index": 0, "function": {"name": "read_file"}}]),
        _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"path":'}}]),
        _delta(tool_calls=[{"index": 0, "function": {"arguments": '"a.py"}'}}]),
        _delta(finish_reason="tool_calls"),
    ]
    respx_mock.post("http://localhost:1234/v1/chat/completions").mock(
        return_value=_sse_response(chunks)
    )

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=[_READ_FILE_TOOL],
    )

    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "call_1"
    assert resp.tool_calls[0].function.name == "read_file"
    assert resp.tool_calls[0].function.arguments == '{"path":"a.py"}'


# --- Task 3.7: fingerprint computation + per-call verification --------------

import hashlib  # noqa: E402

from senex.events import ModelFingerprintChanged  # noqa: E402
from senex.lmstudio_client import LoadedModelInfo  # noqa: E402
from senex.lmstudio_errors import FingerprintChanged  # noqa: E402


def test_compute_fingerprint_canonical(client: LMStudioClient) -> None:
    """Byte-exact: sha256 of compact JSON list ['gemma-4','Q4_K_M','abc123']."""
    info = LoadedModelInfo(
        id="gemma-4", quantization="Q4_K_M", path="/m", digest="abc123"
    )
    expected_payload = '["gemma-4","Q4_K_M","abc123"]'
    expected_hash = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert client.compute_fingerprint(info) == expected_hash


def test_compute_fingerprint_missing_quant_uses_empty_string(
    client: LMStudioClient,
) -> None:
    """quantization='' produces a stable hash; no crash."""
    info = LoadedModelInfo(id="m", quantization="", path="/m", digest="d")
    fp1 = client.compute_fingerprint(info)
    fp2 = client.compute_fingerprint(info)
    assert fp1 == fp2
    expected_payload = '["m","","d"]'
    expected_hash = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert fp1 == expected_hash


def test_compute_fingerprint_missing_digest_uses_unknown_sentinel(
    client: LMStudioClient,
) -> None:
    """When digest is empty, 'unknown' is baked into the canonical tuple."""
    info = LoadedModelInfo(id="m", quantization="Q4", path="", digest="")
    expected_payload = '["m","Q4","unknown"]'
    expected_hash = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert client.compute_fingerprint(info) == expected_hash


@pytest.mark.asyncio
async def test_per_call_fingerprint_mismatch_raises_FingerprintChanged(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
    captured_events: list[Any],
) -> None:
    """Pin a fingerprint; observed differs → ModelFingerprintChanged event then raise."""
    # Re-subscribe captured_events to ModelFingerprintChanged too.
    fp_events: list[Any] = []

    async def _cb(event: Any) -> None:
        fp_events.append(event)

    client._bus.subscribe_local(  # type: ignore[attr-defined]
        "fp", (ModelFingerprintChanged,), _cb
    )

    client._fingerprint_pinned = "deadbeef" * 8  # type: ignore[attr-defined]

    # /v1/models returns a model with a different digest → different fingerprint.
    respx_mock.get("http://localhost:1234/v1/models").respond(
        json={
            "data": [
                {
                    "id": "google/gemma-4-26b-a4b",
                    "quantization": "Q4_K_M",
                    "path": "/models/gemma",
                    "digest": "different-digest",
                }
            ]
        }
    )
    # Even though chat would respond, fingerprint check fires first.
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_success_payload(_valid_audit_json())
    )

    with pytest.raises(FingerprintChanged):
        await client.chat(
            task="file_audit",
            messages=[ChatMessage(role="user", content="hi")],
            schema=audit_schema,
            tools=None,
        )

    # ModelFingerprintChanged event MUST have been published BEFORE raise.
    assert any(
        isinstance(e, ModelFingerprintChanged) for e in fp_events
    ), "ModelFingerprintChanged event should have been published before raise"


@pytest.mark.asyncio
async def test_per_call_fingerprint_match_proceeds_silently(
    respx_mock: Any,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """Pinned matches probe → no event published; chat returns normally."""
    fp_events: list[Any] = []

    async def _cb(event: Any) -> None:
        fp_events.append(event)

    client._bus.subscribe_local(  # type: ignore[attr-defined]
        "fp", (ModelFingerprintChanged,), _cb
    )

    info = LoadedModelInfo(
        id="google/gemma-4-26b-a4b",
        quantization="Q4_K_M",
        path="/models/gemma",
        digest="abc",
    )
    expected_fp = client.compute_fingerprint(info)
    client._fingerprint_pinned = expected_fp  # type: ignore[attr-defined]

    respx_mock.get("http://localhost:1234/v1/models").respond(
        json={
            "data": [
                {
                    "id": "google/gemma-4-26b-a4b",
                    "quantization": "Q4_K_M",
                    "path": "/models/gemma",
                    "digest": "abc",
                }
            ]
        }
    )
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_success_payload(_valid_audit_json())
    )

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="hi")],
        schema=audit_schema,
        tools=None,
    )
    assert resp.content_dict is not None
    assert resp.fingerprint == expected_fp
    assert not [
        e for e in fp_events if isinstance(e, ModelFingerprintChanged)
    ], "match should not publish ModelFingerprintChanged"


# --- Task 3.8: list_loaded_models + probe_capabilities ----------------------


@pytest.mark.asyncio
async def test_list_loaded_models_parses_response(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """GET /v1/models → list[LoadedModelInfo]; preserves id/quantization/path/digest."""
    respx_mock.get("http://localhost:1234/v1/models").respond(
        json={
            "data": [
                {
                    "id": "google/gemma-4-26b-a4b",
                    "quantization": "Q4_K_M",
                    "path": "/models/gemma",
                    "digest": "abc123",
                },
                {
                    "id": "qwen/qwen3-30b",
                    "quantization": "Q5_K_M",
                    "path": "/models/qwen",
                },
            ]
        }
    )
    models = await client.list_loaded_models()
    assert len(models) == 2
    assert models[0].id == "google/gemma-4-26b-a4b"
    assert models[0].quantization == "Q4_K_M"
    assert models[0].path == "/models/gemma"
    assert models[0].digest == "abc123"
    assert models[1].id == "qwen/qwen3-30b"
    assert models[1].digest == ""  # missing field defaults to empty


@pytest.mark.asyncio
async def test_list_loaded_models_unreachable_raises_LMSConnectionLost(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """ConnectError on /v1/models → LMSConnectionLost."""
    respx_mock.get("http://localhost:1234/v1/models").mock(
        side_effect=httpx.ConnectError("server down")
    )
    with pytest.raises(LMSConnectionLost):
        await client.list_loaded_models()


@pytest.mark.asyncio
async def test_probe_capabilities_full_support(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """200 SSE → all four capability flags True."""
    sse_chunks = (
        'data: {"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        status_code=200,
        headers={"content-type": "text/event-stream"},
        text=sse_chunks,
    )
    caps = await client.probe_capabilities("google/gemma-4-26b-a4b")
    assert caps.supports_tools is True
    assert caps.supports_schema_with_tools is True
    assert caps.supports_streaming is True
    assert caps.supports_reasoning_effort is True


@pytest.mark.asyncio
async def test_probe_capabilities_tools_unsupported(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """400 mentioning 'tools' (not schema) → supports_tools=False."""
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        status_code=400,
        json={"error": {"message": "tools parameter not supported by this model"}},
    )
    caps = await client.probe_capabilities("some-model")
    assert caps.supports_tools is False
    assert caps.supports_schema_with_tools is False


@pytest.mark.asyncio
async def test_probe_capabilities_schema_with_tools_unsupported(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """400 mentioning response_format/json_schema → tools True, schema_with_tools False."""
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        status_code=400,
        json={
            "error": {
                "message": "response_format json_schema cannot combine with tools"
            }
        },
    )
    caps = await client.probe_capabilities("some-model")
    assert caps.supports_tools is True
    assert caps.supports_schema_with_tools is False


@pytest.mark.asyncio
async def test_probe_capabilities_cached(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """Second probe call hits zero requests (cache)."""
    sse_chunks = (
        'data: {"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'
        "data: [DONE]\n\n"
    )
    route = respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        status_code=200,
        headers={"content-type": "text/event-stream"},
        text=sse_chunks,
    )
    caps1 = await client.probe_capabilities("model-a")
    first_count = route.call_count
    caps2 = await client.probe_capabilities("model-a")
    assert caps1 == caps2
    assert first_count == 1
    assert route.call_count == 1  # second call did not re-issue


# --- Task 3.10: ANSI strip + secret redaction integration -------------------


def _payload_with_content(
    content: str,
    *,
    reasoning_content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content is not None:
        msg["reasoning_content"] = reasoning_content
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {
        "id": "x",
        "model": "m",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": msg}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


@pytest.mark.asyncio
async def test_ansi_stripped_from_content(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """\\x1b[31m...\\x1b[0m CSI sequences must be stripped from content."""
    raw = "\x1b[31mERROR\x1b[0m: failed"
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content(raw)
    )
    resp = await client.chat(
        task="compaction",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=None,
    )
    assert resp.content == "ERROR: failed"


@pytest.mark.asyncio
async def test_ansi_osc_sequence_stripped_from_content(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """OSC sequences (\\x1b]...\\x07) must also be stripped."""
    raw = "\x1b]0;title\x07hello"
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content(raw)
    )
    resp = await client.chat(
        task="compaction",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=None,
    )
    assert resp.content == "hello"


@pytest.mark.asyncio
async def test_ansi_NOT_stripped_from_reasoning_content(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """reasoning_content is informational; ANSI should survive (per §SEC-7)."""
    raw_reasoning = "thinking \x1b[33mhighlighted\x1b[0m phase"
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content("safe", reasoning_content=raw_reasoning)
    )
    resp = await client.chat(
        task="compaction",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=None,
    )
    assert resp.reasoning_content == raw_reasoning  # byte-for-byte preserved


@pytest.mark.asyncio
async def test_ansi_NOT_stripped_from_tool_call_arguments(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """Tool call arguments are structured input; do not corrupt them."""
    raw_args = '{"path": "x\\u001b[31my\\u001b[0m"}'
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content(
            "",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": raw_args},
                }
            ],
            finish_reason="tool_calls",
        )
    )
    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=[_READ_FILE_TOOL],
    )
    assert resp.tool_calls is not None
    assert resp.tool_calls[0].function.arguments == raw_args


@pytest.mark.asyncio
async def test_aws_key_redacted_from_content(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """AWS access keys in content must be replaced before reaching the caller."""
    raw = "key=AKIAIOSFODNN7EXAMPLE"
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content(raw)
    )
    resp = await client.chat(
        task="compaction",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=None,
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in resp.content
    assert "[REDACTED:aws_access_key]" in resp.content


@pytest.mark.asyncio
async def test_secret_redacted_from_reasoning_content(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """Secrets in reasoning_content must also be redacted."""
    raw_reasoning = "considering AKIAIOSFODNN7EXAMPLE for the example"
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content("safe", reasoning_content=raw_reasoning)
    )
    resp = await client.chat(
        task="compaction",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=None,
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in resp.reasoning_content
    assert "[REDACTED:aws_access_key]" in resp.reasoning_content


@pytest.mark.asyncio
async def test_secret_redacted_from_tool_call_arguments(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """Tool-call arguments containing secrets must be redacted before return."""
    raw_args = '{"token": "ghp_abcdefghijklmnopqrstuvwxyzABCDEF0123"}'
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content(
            "",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "post_secret", "arguments": raw_args},
                }
            ],
            finish_reason="tool_calls",
        )
    )
    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=[_READ_FILE_TOOL],
    )
    assert resp.tool_calls is not None
    args = resp.tool_calls[0].function.arguments
    assert "ghp_abcdefghijklmnopqrstuvwxyzABCDEF0123" not in args
    assert "[REDACTED:github_pat]" in args


@pytest.mark.asyncio
async def test_secret_redacted_before_caller_observes_response(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """ChatResponse fields are post-redaction; the caller never sees raw secrets."""
    raw = "snippet AKIAIOSFODNN7EXAMPLE in body"
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content(raw)
    )
    resp = await client.chat(
        task="compaction",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=None,
    )
    # The post-redaction guarantee for everything in ChatResponse.
    assert "AKIAIOSFODNN7EXAMPLE" not in resp.content
    assert resp.content.endswith("in body")


@pytest.mark.asyncio
async def test_redaction_order_strip_then_redact(
    respx_mock: Any, client: LMStudioClient
) -> None:
    """<think>...</think> is stripped first; ANSI then; redaction last."""
    # Secret hidden inside <think> tags should be removed by the strip; the
    # output content must therefore still be clean ('safe') with no leakage.
    raw = "<think>secret=AKIAIOSFODNN7EXAMPLE\x1b[31m</think>safe"
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(
        json=_payload_with_content(raw)
    )
    resp = await client.chat(
        task="compaction",
        messages=[ChatMessage(role="user", content="x")],
        schema=None,
        tools=None,
    )
    assert resp.content == "safe"
    assert "AKIAIOSFODNN7EXAMPLE" not in resp.content


# ---------------------------------------------------------------------------
# M11 Schema sanitizer for LM Studio compat
# ---------------------------------------------------------------------------


def test_sanitize_schema_drops_conditional_anyof_required() -> None:
    """LM Studio rejects ``anyOf: [{required: [...]}, {required: [...]}]``;
    the sanitizer drops it but preserves all other shape constraints."""
    from senex.lmstudio_client import _sanitize_schema_for_lmstudio

    schema = {
        "type": "object",
        "properties": {
            "location": {
                "type": "object",
                "properties": {
                    "line_start": {"type": "integer"},
                    "symbol": {"type": "string"},
                },
                "anyOf": [
                    {"required": ["symbol"]},
                    {"required": ["line_start"]},
                ],
            }
        },
    }
    out = _sanitize_schema_for_lmstudio(schema)
    assert "anyOf" not in out["properties"]["location"]
    # Property shapes preserved.
    assert out["properties"]["location"]["properties"]["line_start"] == {
        "type": "integer"
    }


def test_sanitize_schema_preserves_richer_anyof() -> None:
    """``anyOf`` with shape constraints (not just ``required``) is kept;
    only the conditional-required pattern is dropped."""
    from senex.lmstudio_client import _sanitize_schema_for_lmstudio

    schema = {
        "type": "object",
        "anyOf": [
            {"properties": {"a": {"type": "string"}}},
            {"properties": {"b": {"type": "integer"}}},
        ],
    }
    out = _sanitize_schema_for_lmstudio(schema)
    assert "anyOf" in out
    assert len(out["anyOf"]) == 2


def test_sanitize_schema_drops_oneof_required_too() -> None:
    """Same rule applies to ``oneOf`` — both surface the conditional-required
    pattern senex's audit_response.schema.json uses."""
    from senex.lmstudio_client import _sanitize_schema_for_lmstudio

    schema = {
        "oneOf": [
            {"required": ["symbol"]},
            {"required": ["line_start"]},
        ]
    }
    assert _sanitize_schema_for_lmstudio(schema) == {}


def test_sanitize_schema_recurses_into_array_items() -> None:
    """Conditional-required inside ``items`` is dropped (this is the actual
    pattern in audit_response.schema.json — findings[i].location.anyOf)."""
    from senex.lmstudio_client import _sanitize_schema_for_lmstudio

    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "anyOf": [{"required": ["a"]}, {"required": ["b"]}],
        },
    }
    out = _sanitize_schema_for_lmstudio(schema)
    assert "anyOf" not in out["items"]
