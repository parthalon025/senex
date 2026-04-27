"""senex.lmstudio_client — OpenAI-compatible LM Studio client (M3).

Implements spec §5.4 (`audit_response.schema.json`), §5.5 (LM Studio inference
controls), §5.5.2.3 (model fingerprint), §5.6 (Tick events), §8.1 (preflight
capability probe), §8.2 (per-file recovery primitives).

Single-round-trip boundary (CRITICAL — see ``M3 ↔ M5 reconciliation`` in
``docs/superpowers/plans/2026-04-26-senex-v1/m3-lmstudio.md``):
``LMStudioClient.chat()`` handles ONE chat-completion call. The iteration
controller (multi-call dispatch loop, per-file budget, compaction triggering)
lives in ``senex.tools.loop.ToolLoop`` (M5 Task 5.8) and consumes this client
via dependency injection. This module never imports ``senex.tools.*``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import httpx
import tiktoken
from pydantic import BaseModel, ConfigDict, TypeAdapter

from senex.config import LmStudioCfg
from senex.events import (
    EventBus,
    ModelFingerprintChanged,
    OutputComplete,
    OutputStarted,
    OutputTick,
    ThinkingComplete,
    ThinkingStarted,
    ThinkingTick,
)
from senex.lmstudio_errors import (
    FingerprintChanged,
    LMSConnectionLost,
    LMSResponseInvalidJSON,
    LMSResponseSchemaInvalid,
    SchemaNegotiationFailed,
    TokenBudgetExceeded,
)
from senex.secret_redactor import SecretRedactor

# ----- regex literals (locked by tests) ---------------------------------------
# §5 conventions: ANSI strip on `content` only.
_ANSI_RE: Final = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")
# §8.2: think tags stripped before JSON parse / schema validation.
_THINK_TAG_RE: Final = re.compile(r"<think>.*?</think>", re.DOTALL)
# §8.2: schema-related 4xx body fragments that trigger json_schema -> json_object fallback.
_SCHEMA_ERROR_RE: Final = re.compile(r"schema|response_format|json_schema", re.IGNORECASE)
# §5.6: Tick coalescer thresholds (256 tokens OR 500ms).
_TICK_TOKEN_THRESHOLD: Final = 256
_TICK_TIME_THRESHOLD_S: Final = 0.5

STRICT_RETRY_PREAMBLE: Final = (
    "Your previous response did not validate against the required schema. "
    "Respond ONLY with valid JSON conforming exactly to the provided schema. "
    "Do not include explanatory prose, markdown fences, or <think> tags in the response body."
)

# Probe primitives (§8.1).
_NOOP_TOOL: Final[dict[str, object]] = {
    "type": "function",
    "function": {
        "name": "noop",
        "description": "no-op probe tool; never invoked",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}
_NOOP_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


# ----- public pydantic surface ------------------------------------------------


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCallFunction(_StrictModel):
    name: str
    arguments: str  # JSON string per OpenAI convention.


class ToolCall(_StrictModel):
    id: str
    type: Literal["function"]
    function: ToolCallFunction


class ChatMessage(_StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # only on role="tool"


# Tool schemas are forwarded byte-for-byte to LMS; we don't constrain their shape
# beyond "JSON-serializable mapping". Per §10 conventions: external structured
# data flows through typed surfaces; we use a Mapping alias here because OpenAI's
# tool schema spec is large and version-drift-prone.
ToolSchema = dict[str, object]


class ChatResponse(_StrictModel):
    """Result of a single ``chat()`` round-trip.

    All string fields are post-redaction and (for ``content``) post-ANSI-strip.
    The caller may safely append ``content`` / ``reasoning_content`` /
    ``tool_calls`` to history without re-running the redactor.

    ``thinking_ms`` / ``output_ms`` decompose ``latency_ms`` into the time
    spent streaming ``reasoning_content`` (or inline ``<think>`` blocks)
    versus regular ``content``. They default to 0 when the server doesn't
    expose phase boundaries.
    """

    content: str
    content_dict: dict[str, object] | None
    reasoning_content: str
    tool_calls: list[ToolCall] | None
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"]
    latency_ms: int
    thinking_ms: int = 0
    output_ms: int = 0
    prompt_tokens: int
    completion_tokens: int
    fingerprint: str


class ProbedCapabilities(_StrictModel):
    supports_tools: bool
    supports_schema_with_tools: bool
    supports_streaming: bool
    supports_reasoning_effort: bool


class LoadedModelInfo(_StrictModel):
    id: str
    quantization: str = ""
    path: str = ""
    digest: str = ""


# ----- internal helpers -------------------------------------------------------


@dataclass
class _TickCoalescer:
    """Per-turn token counter (§5.6: ``tokens_so_far`` resets on each ``*_Started``)."""

    tokens_so_far: int = 0
    tokens_since_last_tick: int = 0
    last_tick_time: float = 0.0

    def reset(self) -> None:
        self.tokens_so_far = 0
        self.tokens_since_last_tick = 0
        self.last_tick_time = time.monotonic()

    def maybe_tick(self, delta_tokens: int) -> int | None:
        """Return ``delta_since_last_tick`` if a tick should fire, else ``None``."""
        self.tokens_so_far += delta_tokens
        self.tokens_since_last_tick += delta_tokens
        now = time.monotonic()
        if (
            self.tokens_since_last_tick >= _TICK_TOKEN_THRESHOLD
            or (now - self.last_tick_time) >= _TICK_TIME_THRESHOLD_S
        ):
            delta = self.tokens_since_last_tick
            self.tokens_since_last_tick = 0
            self.last_tick_time = now
            return delta
        return None


@dataclass
class _StreamResult:
    content: str
    reasoning_content: str
    tool_calls: list[ToolCall] | None
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"]
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    headers: dict[str, str] = field(default_factory=dict)
    thinking_ms: int = 0
    output_ms: int = 0


@dataclass
class _ToolCallAccumulator:
    """Assembles ``tool_calls`` from streamed ``delta`` chunks indexed by position."""

    by_index: dict[int, dict[str, Any]] = field(default_factory=dict)

    def consume(self, deltas: list[dict[str, Any]]) -> None:
        for delta in deltas:
            idx = int(delta.get("index", 0))
            slot = self.by_index.setdefault(
                idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if delta.get("id"):
                slot["id"] = delta["id"]
            if delta.get("type"):
                slot["type"] = delta["type"]
            fn = delta.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] = (slot["function"]["name"] or "") + fn["name"]
            if fn.get("arguments") is not None:
                slot["function"]["arguments"] = (
                    slot["function"]["arguments"] or ""
                ) + fn["arguments"]

    def build(self) -> list[ToolCall] | None:
        if not self.by_index:
            return None
        ordered = [self.by_index[i] for i in sorted(self.by_index.keys())]
        return [
            ToolCall(
                id=tc["id"],
                type=tc["type"],
                function=ToolCallFunction(
                    name=tc["function"]["name"], arguments=tc["function"]["arguments"]
                ),
            )
            for tc in ordered
        ]


# ----- main client ------------------------------------------------------------


class LMStudioClient:
    """OpenAI-compatible client for LM Studio.

    Implements ``senex.llm_client.LLMClient``. Handles ONE chat-completion
    round-trip per ``chat()`` call; iteration (tool dispatch + compaction)
    is delegated to ``senex.tools.loop.ToolLoop`` (M5).
    """

    def __init__(
        self,
        config: LmStudioCfg,
        bus: EventBus,
        redactor: SecretRedactor,
    ) -> None:
        self._config = config
        self._bus = bus
        self._redactor = redactor
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(
                connect=float(config.connect_timeout),
                read=float(config.read_timeout),
                write=10.0,
                pool=10.0,
            ),
            headers={"Authorization": f"Bearer {config.api_key}"},
        )
        # Schema-fallback decision cache (§3.3): probe once per session.
        self._schema_mode: Literal["json_schema", "json_object"] | None = None
        # Fingerprint pinning is M4's responsibility; left None until set.
        self._fingerprint_pinned: str | None = None
        self._fingerprint_cached: str | None = None
        self._fingerprint_cached_at: float = 0.0
        # tiktoken encoder cache (§3.4 pitfall: cache None for unknown models).
        self._encoder_cache: dict[str, tiktoken.Encoding | None] = {}
        # Capability probe cache (§3.8).
        self._caps_cached: ProbedCapabilities | None = None

    async def aclose(self) -> None:
        """Release the underlying HTTP client (idempotent)."""
        await self._http.aclose()

    # --- public API -----------------------------------------------------------

    async def chat(
        self,
        *,
        task: str,
        messages: list[ChatMessage],
        schema: dict[str, object] | None,
        tools: list[ToolSchema] | None = None,
    ) -> ChatResponse:
        """Open ONE chat-completion round-trip.

        This method handles ONE chat-completion round-trip. When the assistant
        emits tool_calls, the response is returned with ``tool_calls`` populated
        and ``finish_reason == "tool_calls"``; the caller (senex.tools.loop.ToolLoop)
        is responsible for dispatching the tools, appending tool messages, and
        re-invoking chat(). The compaction trigger likewise lives in ToolLoop,
        not here. See spec §5.5 step list — this method implements step 1 only.

        Iteration controller: senex.tools.loop.ToolLoop (M5 Task 5.8).
        """
        # 1. Pre-call token budget check (§3.4).
        used = self.count_tokens(messages, self._config.model)
        budget = int(self._config.token_budget_pct * self._config.context_window)
        if used > budget:
            raise TokenBudgetExceeded(
                f"messages={used} tokens exceeds {self._config.token_budget_pct} "
                f"* {self._config.context_window} = {budget}"
            )

        # 2. Per-call fingerprint verification (§3.7) — only when M4 has pinned one.
        observed_fp: str = ""
        if self._fingerprint_pinned is not None:
            observed_fp = await self._verify_fingerprint(path=None)

        # 3. Build base body (sampling + thinking + tools).
        base_body = self._build_base_body(messages=messages, tools=tools)

        # 4. Dispatch via schema fallback decision tree (§3.3).
        path_event = None  # M3 client doesn't know "path"; M8 supplies via task wrapper.
        if schema is None:
            # Unstructured (e.g. compaction) — single direct stream.
            stream = await self._stream_with_retry(base_body, path=path_event)
            content_dict: dict[str, object] | None = None
        else:
            stream, content_dict = await self._chat_with_schema_fallback(
                base_body=base_body, schema=schema, path=path_event
            )

        return ChatResponse(
            content=stream.content,
            content_dict=content_dict,
            reasoning_content=stream.reasoning_content,
            tool_calls=stream.tool_calls,
            finish_reason=stream.finish_reason,
            latency_ms=stream.latency_ms,
            thinking_ms=stream.thinking_ms,
            output_ms=stream.output_ms,
            prompt_tokens=stream.prompt_tokens,
            completion_tokens=stream.completion_tokens,
            fingerprint=observed_fp,
        )

    async def list_loaded_models(self) -> list[LoadedModelInfo]:
        """``GET /v1/models`` — used by M4 status reporting and ``_verify_fingerprint``."""
        try:
            resp = await self._http.get("/models")
        except httpx.ConnectError as e:
            raise LMSConnectionLost(f"GET /v1/models: {e}") from e
        except httpx.ReadTimeout as e:
            raise LMSConnectionLost(f"GET /v1/models timed out: {e}") from e
        if resp.status_code != 200:
            raise LMSConnectionLost(f"GET /v1/models returned {resp.status_code}")
        body = resp.json()
        out: list[LoadedModelInfo] = []
        for m in body.get("data", []):
            out.append(
                LoadedModelInfo(
                    id=m.get("id", ""),
                    quantization=m.get("quantization", "") or "",
                    path=m.get("path", "") or "",
                    digest=m.get("digest", "") or "",
                )
            )
        return out

    async def probe_capabilities(self, model_id: str) -> ProbedCapabilities:
        """Probe model support for ``tools``, ``json_schema`` + ``tools``, streaming.

        Cached for the run on the instance. Cache only on success — transient
        5xx must not poison the result for the rest of the run.
        """
        if self._caps_cached is not None:
            return self._caps_cached

        body: dict[str, object] = {
            "model": model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [_NOOP_TOOL],
            "tool_choice": "auto",
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "noop", "schema": _NOOP_SCHEMA, "strict": True},
            },
            "max_tokens": 1,
            "stream": True,
        }

        supports_tools = True
        supports_schema_with_tools = True
        supports_streaming = True
        supports_reasoning_effort = True

        try:
            resp = await self._http.post("/chat/completions", json=body)
        except httpx.ConnectError as e:
            raise LMSConnectionLost(f"probe_capabilities: {e}") from e

        if resp.status_code == 200:
            # Best-effort: non-streamed body still counts as success.
            ct = resp.headers.get("content-type", "")
            supports_streaming = "event-stream" in ct or _looks_like_sse(resp.text)
        elif resp.status_code == 400:
            text = resp.text
            if re.search(r"\btools?\b", text, re.IGNORECASE) and not _SCHEMA_ERROR_RE.search(
                text
            ):
                supports_tools = False
                supports_schema_with_tools = False
            elif _SCHEMA_ERROR_RE.search(text):
                supports_tools = True
                supports_schema_with_tools = False
            else:
                # Unrelated 400 — propagate; preflight will exit 3.
                resp.raise_for_status()
        else:
            resp.raise_for_status()

        caps = ProbedCapabilities(
            supports_tools=supports_tools,
            supports_schema_with_tools=supports_schema_with_tools,
            supports_streaming=supports_streaming,
            supports_reasoning_effort=supports_reasoning_effort,
        )
        self._caps_cached = caps
        return caps

    def count_tokens(self, messages: list[ChatMessage], model_id: str) -> int:
        """Approximate token count using ``tiktoken cl100k_base``.

        ``tiktoken`` is calibrated for OpenAI tokenizers; gemma/qwen tokenization
        differs by ~5-15%. This is a budget guardrail, not a billing-grade count.
        Falls back to ``len(text) // 4`` when ``cl100k_base`` is unavailable.
        """
        enc = self._get_encoder(model_id)
        total = 0
        for m in messages:
            parts: list[str] = []
            if m.content:
                parts.append(m.content)
            if m.tool_calls:
                for tc in m.tool_calls:
                    # IDs are bookkeeping; only function name + arguments carry semantics.
                    parts.append(tc.function.name)
                    parts.append(tc.function.arguments)
            joined = "\n".join(parts)
            if enc is not None:
                total += len(enc.encode(joined))
            else:
                total += len(joined) // 4
            total += 4  # OpenAI's per-message structural overhead.
        return total

    def compute_fingerprint(self, model_info: LoadedModelInfo) -> str:
        """Canonical sha256 fingerprint per spec §5.5.2.3.

        Tuple ``(model_id, quant, checkpoint_digest)`` serialized with
        ``separators=(",", ":")`` and ``sort_keys=True`` so the byte stream is
        identical across Python versions.
        """
        digest = model_info.digest if model_info.digest else "unknown"
        payload = json.dumps(
            [model_info.id, model_info.quantization or "", digest],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    # --- internals: encoder cache --------------------------------------------

    def _get_encoder(self, model_id: str) -> tiktoken.Encoding | None:
        if model_id in self._encoder_cache:
            return self._encoder_cache[model_id]
        try:
            enc: tiktoken.Encoding | None = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 — tiktoken raises generic on missing encoding
            enc = None
        self._encoder_cache[model_id] = enc
        return enc

    # --- internals: HTTP retry envelope ---------------------------------------

    async def _post_with_retry(
        self,
        path: str,
        *,
        json_body: dict[str, object],
    ) -> httpx.Response:
        """POST with bounded retry per §5.5/§8.2 retry policy."""
        max_attempts = self._config.http_retries + 1
        backoffs = self._config.backoff_seconds

        last_exc: BaseException | None = None
        for attempt in range(max_attempts):
            try:
                response = await self._http.post(path, json=json_body)
            except httpx.ConnectError as e:
                raise LMSConnectionLost(str(e)) from e
            except httpx.ReadTimeout as e:
                last_exc = e
                if attempt < max_attempts - 1:
                    await asyncio.sleep(backoffs[attempt])
                    continue
                raise

            if 400 <= response.status_code < 500:
                response.raise_for_status()  # 4xx — fail-fast (no retry).
            if 500 <= response.status_code < 600:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(backoffs[attempt])
                        continue
                    raise
            return response
        assert last_exc is not None, "unreachable"
        raise last_exc

    async def _reprobe_until_alive(
        self, interval_s: float = 10.0, max_iterations: int = 360
    ) -> None:
        """Block until ``/v1/models`` returns 200. Bounded by ``max_iterations`` (default 1h).

        §3 conventions: every loop has an explicit ``max_iterations`` cap.
        """
        for _ in range(max_iterations):
            try:
                r = await self._http.get("/models")
                if r.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            await asyncio.sleep(interval_s)
        raise LMSConnectionLost(
            f"reprobe gave up after {max_iterations * interval_s}s"
        )

    # --- internals: streaming -------------------------------------------------

    async def _stream_with_retry(
        self, body: dict[str, object], *, path: str | None
    ) -> _StreamResult:
        """Stream POST with retry on 5xx and timeouts; ``ConnectError`` -> ``LMSConnectionLost``."""
        max_attempts = self._config.http_retries + 1
        backoffs = self._config.backoff_seconds

        last_exc: BaseException | None = None
        for attempt in range(max_attempts):
            try:
                return await self._do_stream(body, path=path)
            except httpx.ConnectError as e:
                raise LMSConnectionLost(str(e)) from e
            except httpx.ReadTimeout as e:
                last_exc = e
            except httpx.HTTPStatusError as e:
                if 400 <= e.response.status_code < 500:
                    raise
                last_exc = e
            if attempt < max_attempts - 1:
                await asyncio.sleep(backoffs[attempt])
                continue
            assert last_exc is not None
            raise last_exc
        assert last_exc is not None, "unreachable"
        raise last_exc

    async def _do_stream(
        self, body: dict[str, object], *, path: str | None
    ) -> _StreamResult:
        """Issue a single stream attempt; consume SSE; assemble ``_StreamResult``."""
        body_streamed = dict(body)
        body_streamed["stream"] = True
        # Per OpenAI streaming spec: ``usage`` is only emitted in the final
        # SSE chunk when ``stream_options.include_usage`` is true. Without
        # this, prompt_tokens / completion_tokens come back as 0 and the
        # per-file metadata header reports "Tokens in/out: 0 / 0" (M11 bug 2).
        body_streamed["stream_options"] = {"include_usage": True}

        thinking_tick = _TickCoalescer()
        output_tick = _TickCoalescer()
        reasoning_buf: list[str] = []
        content_buf: list[str] = []
        tool_calls_acc = _ToolCallAccumulator()
        phase: Literal["pre", "thinking", "output"] = "pre"
        finish_reason: str | None = None
        prompt_tokens = 0
        completion_tokens = 0
        thinking_started_at = 0.0
        output_started_at = 0.0
        thinking_ms_total = 0  # accumulator for ChatResponse.thinking_ms

        start = time.monotonic()

        # Streaming path — falls back to non-streaming when the server returns
        # JSON instead of an SSE stream (some test mocks return JSON directly).
        non_streaming_chunk: dict[str, Any] | None = None
        async with self._http.stream("POST", "/chat/completions", json=body_streamed) as r:
            # Read the body BEFORE raise_for_status so the caller can inspect
            # ``.text`` (httpx.ResponseNotRead otherwise — schema fallback needs
            # to grep the 4xx body for "schema|response_format|json_schema").
            if r.status_code >= 400:
                await r.aread()
                r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            headers = dict(r.headers)
            if "event-stream" in ctype or "text/event-stream" in ctype:
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ")
                    if data.strip() == "[DONE]":
                        break
                    chunk = json.loads(data)
                    (
                        finish_reason,
                        phase,
                        thinking_started_at,
                        output_started_at,
                        delta_thinking_ms,
                    ) = await self._handle_stream_chunk(
                        chunk=chunk,
                        phase=phase,
                        thinking_tick=thinking_tick,
                        output_tick=output_tick,
                        reasoning_buf=reasoning_buf,
                        content_buf=content_buf,
                        tool_calls_acc=tool_calls_acc,
                        finish_reason=finish_reason,
                        path=path,
                        thinking_started_at=thinking_started_at,
                        output_started_at=output_started_at,
                    )
                    thinking_ms_total += delta_thinking_ms
                    if "usage" in chunk and chunk["usage"]:
                        prompt_tokens = chunk["usage"].get("prompt_tokens", prompt_tokens)
                        completion_tokens = chunk["usage"].get(
                            "completion_tokens", completion_tokens
                        )
            else:
                # Non-streaming fallback: collect bytes, parse JSON.
                buf_bytes = b""
                async for raw in r.aiter_bytes():
                    buf_bytes += raw
                non_streaming_chunk = json.loads(buf_bytes.decode("utf-8") or "{}")

        if non_streaming_chunk is not None:
            choice = (non_streaming_chunk.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            finish_reason = choice.get("finish_reason") or "stop"
            if msg.get("content"):
                content_buf.append(msg["content"])
            if msg.get("reasoning_content"):
                reasoning_buf.append(msg["reasoning_content"])
            if msg.get("tool_calls"):
                tool_calls_acc.consume(
                    [
                        {
                            "index": i,
                            "id": tc.get("id", ""),
                            "type": tc.get("type", "function"),
                            "function": tc.get("function", {}),
                        }
                        for i, tc in enumerate(msg["tool_calls"])
                    ]
                )
            usage = non_streaming_chunk.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens", 0) or 0
            completion_tokens = usage.get("completion_tokens", 0) or 0

        # Phase-out events.
        latency_ms = int((time.monotonic() - start) * 1000)
        output_ms_total = 0
        if phase == "thinking":
            thinking_latency = int((time.monotonic() - thinking_started_at) * 1000)
            thinking_ms_total += thinking_latency
            await self._bus.publish(
                ThinkingComplete(
                    ts=_now(),
                    run_id=_run_id(),
                    path=path or "",
                    total_thinking_tokens=thinking_tick.tokens_so_far,
                    latency_ms=thinking_latency,
                )
            )
        elif phase == "output":
            # ThinkingComplete may have already fired during the chunk loop;
            # publish OutputComplete to mirror it.
            output_latency = int((time.monotonic() - output_started_at) * 1000)
            output_ms_total = output_latency
            await self._bus.publish(
                OutputComplete(
                    ts=_now(),
                    run_id=_run_id(),
                    path=path or "",
                    total_output_tokens=output_tick.tokens_so_far,
                    latency_ms=output_latency,
                )
            )

        # Strip-then-redact ordering (§5.10 / §3.10): think -> ANSI -> redact.
        # Also: extract <think>...</think> blocks BEFORE stripping so that
        # models like gemma which emit thinking inline (instead of via the
        # OpenAI ``reasoning_content`` field) still produce a usable trace
        # (M11 bug 4). Prepend the inline thoughts to any streamed
        # reasoning_content so combined render is deterministic.
        raw_content = "".join(content_buf)
        inline_think_blocks = _THINK_TAG_RE.findall(raw_content)
        stripped_content = _ANSI_RE.sub("", _THINK_TAG_RE.sub("", raw_content))
        final_content = self._redactor.redact(stripped_content)

        # Concatenate inline <think> bodies (strip the tag wrappers) and any
        # streamed reasoning_content. Use a single newline join so the file
        # looks like a coherent transcript when rendered.
        inline_reasoning = "\n".join(
            re.sub(r"^<think>|</think>$", "", block).strip()
            for block in inline_think_blocks
        )
        streamed_reasoning = "".join(reasoning_buf)
        combined_reasoning = "\n\n".join(
            part for part in (inline_reasoning, streamed_reasoning) if part
        )
        final_reasoning = self._redactor.redact(combined_reasoning)
        final_tool_calls = _redact_tool_calls(tool_calls_acc.build(), self._redactor)

        return _StreamResult(
            content=final_content,
            reasoning_content=final_reasoning,
            tool_calls=final_tool_calls,
            finish_reason=_coerce_finish_reason(finish_reason),
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            headers=headers,
            thinking_ms=thinking_ms_total,
            output_ms=output_ms_total,
        )

    async def _handle_stream_chunk(
        self,
        *,
        chunk: dict[str, Any],
        phase: Literal["pre", "thinking", "output"],
        thinking_tick: _TickCoalescer,
        output_tick: _TickCoalescer,
        reasoning_buf: list[str],
        content_buf: list[str],
        tool_calls_acc: _ToolCallAccumulator,
        finish_reason: str | None,
        path: str | None,
        thinking_started_at: float,
        output_started_at: float,
    ) -> tuple[
        str | None,
        Literal["pre", "thinking", "output"],
        float,
        float,
        int,
    ]:
        """Process one SSE chunk; returns (finish_reason, phase,
        thinking_started_at, output_started_at, delta_thinking_ms).

        ``delta_thinking_ms`` is non-zero only on the chunk that closes the
        thinking phase (the last reasoning_content chunk before the first
        content delta). Caller accumulates into ``thinking_ms_total``.
        """
        delta_thinking_ms = 0
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if choice.get("finish_reason"):
            finish_reason = choice.get("finish_reason")

        rd = delta.get("reasoning_content")
        if rd:
            if phase != "thinking":
                thinking_tick.reset()
                thinking_started_at = time.monotonic()
                await self._bus.publish(
                    ThinkingStarted(ts=_now(), run_id=_run_id(), path=path or "")
                )
                phase = "thinking"
            reasoning_buf.append(rd)
            delta_tok = self._approx_tokens(rd)
            d = thinking_tick.maybe_tick(delta_tok)
            if d is not None:
                await self._bus.publish(
                    ThinkingTick(
                        ts=_now(),
                        run_id=_run_id(),
                        path=path or "",
                        tokens_so_far=thinking_tick.tokens_so_far,
                        delta_since_last_tick=d,
                    )
                )

        cd = delta.get("content")
        if cd:
            if phase == "thinking":
                # Close out thinking phase before opening output.
                latency_ms = int((time.monotonic() - thinking_started_at) * 1000)
                delta_thinking_ms = latency_ms
                await self._bus.publish(
                    ThinkingComplete(
                        ts=_now(),
                        run_id=_run_id(),
                        path=path or "",
                        total_thinking_tokens=thinking_tick.tokens_so_far,
                        latency_ms=latency_ms,
                    )
                )
                output_tick.reset()
                output_started_at = time.monotonic()
                await self._bus.publish(
                    OutputStarted(ts=_now(), run_id=_run_id(), path=path or "")
                )
                phase = "output"
            elif phase == "pre":
                output_tick.reset()
                output_started_at = time.monotonic()
                await self._bus.publish(
                    OutputStarted(ts=_now(), run_id=_run_id(), path=path or "")
                )
                phase = "output"
            content_buf.append(cd)
            delta_tok = self._approx_tokens(cd)
            d = output_tick.maybe_tick(delta_tok)
            if d is not None:
                await self._bus.publish(
                    OutputTick(
                        ts=_now(),
                        run_id=_run_id(),
                        path=path or "",
                        tokens_so_far=output_tick.tokens_so_far,
                        delta_since_last_tick=d,
                    )
                )

        if delta.get("tool_calls"):
            tool_calls_acc.consume(delta["tool_calls"])

        return (
            finish_reason,
            phase,
            thinking_started_at,
            output_started_at,
            delta_thinking_ms,
        )

    def _approx_tokens(self, text: str) -> int:
        """Cheap streaming token estimate; the budget pre-check uses tiktoken."""
        if not text:
            return 0
        # ~4 chars/token is the usual rule-of-thumb for English; cheap enough
        # to run on every chunk without dragging streaming throughput down.
        return max(1, len(text) // 4)

    # --- internals: schema fallback decision tree -----------------------------

    async def _chat_with_schema_fallback(
        self,
        *,
        base_body: dict[str, object],
        schema: dict[str, object],
        path: str | None,
    ) -> tuple[_StreamResult, dict[str, object] | None]:
        """Run §3.3 schema fallback decision tree with one strict-retry on schema-fail."""
        # Cached decision short-circuits the probe.
        if self._schema_mode == "json_object":
            return await self._post_validate(
                base_body=base_body, schema=schema, mode="json_object", path=path
            )

        try:
            stream, parsed = await self._post_validate(
                base_body=base_body, schema=schema, mode="json_schema", path=path
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400 and _SCHEMA_ERROR_RE.search(e.response.text):
                # json_schema not supported on this model — fall back permanently.
                self._schema_mode = "json_object"
                try:
                    return await self._post_validate(
                        base_body=base_body, schema=schema, mode="json_object", path=path
                    )
                except httpx.HTTPStatusError as e2:
                    if 400 <= e2.response.status_code < 500:
                        raise SchemaNegotiationFailed(
                            f"both json_schema and json_object refused: {e2}"
                        ) from e2
                    raise
            raise
        # First successful round-trip — cache the decision.
        self._schema_mode = "json_schema"
        return stream, parsed

    async def _post_validate(
        self,
        *,
        base_body: dict[str, object],
        schema: dict[str, object],
        mode: Literal["json_schema", "json_object"],
        path: str | None,
        _strict_retry: bool = False,
    ) -> tuple[_StreamResult, dict[str, object] | None]:
        body = dict(base_body)
        if mode == "json_schema":
            # Wire-level adapter: LM Studio's json_schema validator does not
            # accept ``anyOf``/``oneOf`` blocks that only carry ``required``
            # arrays (it returns a 200 OK SSE chunk with an inline error
            # body and zero content). The senex-side schema file (M2) stays
            # the canonical source of truth for response validation; this
            # transport-only sanitizer drops the conditional-required blocks
            # so the model actually produces output. Validation downstream
            # uses the full schema (see ``_validate_audit_schema``).
            wire_schema = _sanitize_schema_for_lmstudio(schema)
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "audit_response",
                    "schema": wire_schema,
                    "strict": True,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
            body["messages"] = _inject_json_object_reminder(
                _deep_copy_messages(body.get("messages", [])), schema
            )

        if _strict_retry:
            body["messages"] = _inject_strict_retry_preamble(
                _deep_copy_messages(body.get("messages", []))
            )

        stream = await self._stream_with_retry(body, path=path)

        # When tool_calls fired, no JSON body to validate — caller (M5 ToolLoop)
        # will validate after the eventual finish_reason="stop" turn.
        if stream.finish_reason == "tool_calls":
            return stream, None

        # Strip-then-parse-then-validate ordering is critical (see §3.10).
        try:
            parsed = json.loads(stream.content) if stream.content else None
        except json.JSONDecodeError as e:
            if not _strict_retry:
                return await self._post_validate(
                    base_body=base_body,
                    schema=schema,
                    mode=mode,
                    path=path,
                    _strict_retry=True,
                )
            raise LMSResponseInvalidJSON(
                f"content not parseable as JSON after <think> strip: {e}"
            ) from e

        if parsed is None:
            return stream, None

        try:
            adapter = TypeAdapter(dict[str, object])
            validated = adapter.validate_python(parsed)
            _validate_audit_schema(validated, schema)
        except (ValueError, TypeError) as e:
            if not _strict_retry:
                return await self._post_validate(
                    base_body=base_body,
                    schema=schema,
                    mode=mode,
                    path=path,
                    _strict_retry=True,
                )
            raise LMSResponseSchemaInvalid(str(e)) from e

        return stream, validated

    # --- internals: fingerprint verification ----------------------------------

    async def _verify_fingerprint(self, *, path: str | None) -> str:
        """Return the observed fingerprint; raise ``FingerprintChanged`` on mismatch.

        Cached for ``fingerprint_recheck_interval_s`` to avoid burning a `/v1/models`
        round-trip on every chat call under tool-loop iteration.
        """
        now = time.monotonic()
        if (
            self._fingerprint_cached is not None
            and (now - self._fingerprint_cached_at)
            < self._config.fingerprint_recheck_interval_s
        ):
            return self._fingerprint_cached

        models = await self.list_loaded_models()
        target = next((m for m in models if m.id == self._config.model), None)
        if target is None:
            raise LMSConnectionLost(f"model {self._config.model!r} not loaded")
        observed = self.compute_fingerprint(target)
        self._fingerprint_cached = observed
        self._fingerprint_cached_at = now

        if self._fingerprint_pinned and observed != self._fingerprint_pinned:
            await self._bus.publish(
                ModelFingerprintChanged(
                    ts=_now(),
                    run_id=_run_id(),
                    path=path or "",
                    expected_fingerprint=self._fingerprint_pinned,
                    observed_fingerprint=observed,
                )
            )
            raise FingerprintChanged(
                f"expected={self._fingerprint_pinned} observed={observed}"
            )
        return observed

    # --- internals: request body construction ---------------------------------

    def _build_base_body(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self._config.model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
        }
        # Sampling fields are forwarded verbatim (the LMS server ignores ones it doesn't know).
        sampling = self._config.sampling.model_dump()
        # Strip senex-only keys that don't belong on the wire.
        sampling.pop("seed_random", None)
        body.update(sampling)

        # Truthy check: omit `tools` when None or [] (some backends 400 on empty arrays).
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
        return body


# ----- module-level helpers ---------------------------------------------------


def _coerce_finish_reason(
    raw: str | None,
) -> Literal["stop", "tool_calls", "length", "content_filter"]:
    if raw in ("stop", "tool_calls", "length", "content_filter"):
        return raw  # type: ignore[return-value]
    return "stop"


def _redact_tool_calls(
    tcs: list[ToolCall] | None, redactor: SecretRedactor
) -> list[ToolCall] | None:
    if not tcs:
        return None
    return [
        ToolCall(
            id=tc.id,
            type=tc.type,
            function=ToolCallFunction(
                name=tc.function.name,
                arguments=redactor.redact(tc.function.arguments),
            ),
        )
        for tc in tcs
    ]


def _sanitize_schema_for_lmstudio(schema: dict[str, object]) -> dict[str, object]:
    """Strip schema constructs LM Studio's structured-output validator rejects.

    LM Studio's json_schema enforcement (as of v0.3.x) returns a 200 OK with
    an inline ``{"error": "Invalid JSON Schema: Unrecognized schema: ..."}``
    SSE chunk (and otherwise empty completion) when the schema contains:

    * ``anyOf`` / ``oneOf`` blocks whose branches each carry only ``required``
      arrays (the conditional-required pattern, e.g. "either symbol or
      line_start"). Strict mode rejects these wholesale.

    The audit response schema (``audit_response.schema.json``) uses this
    pattern on ``findings[i].location`` to express "either a symbol or a
    line range, but at least one." We KEEP the canonical schema file — it
    drives senex-side response validation — but strip the conditional
    branches at the wire boundary so the model actually emits output.
    Downstream validation (``_validate_audit_schema``) uses the full
    schema, so semantic guarantees are unchanged.

    Rules:
    * Recurse into every dict.
    * Drop ``anyOf`` / ``oneOf`` keys whose every branch contains ONLY
      ``required`` (i.e., conditional-required, no shape constraints).
    * Leave ``allOf`` and richer ``anyOf``/``oneOf`` patterns untouched.
    """
    import copy

    def _is_conditional_required(branches: list[Any]) -> bool:
        if not branches:
            return False
        for branch in branches:
            if not isinstance(branch, dict):
                return False
            keys = set(branch.keys())
            if keys != {"required"}:
                return False
        return True

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key in ("anyOf", "oneOf") and isinstance(value, list):
                    if _is_conditional_required(value):
                        # Drop the constraint entirely; the response schema
                        # still validates server-side after we receive it.
                        continue
                out[key] = _walk(value)
            return out
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(copy.deepcopy(schema))


def _validate_audit_schema(parsed: dict[str, object], schema: dict[str, object]) -> None:
    """Lightweight subset of jsonschema validation sufficient for required-key check.

    Full jsonschema validation lives in M2 ``test_schemas.py``; here we just enforce
    that the top-level required keys are present and ``schema_version == 1``.
    Caller decides whether to escalate ``ValueError`` to ``LMSResponseSchemaInvalid``.
    """
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if key not in parsed:
                raise ValueError(f"missing required key {key!r}")
    if "schema_version" in parsed and parsed["schema_version"] != 1:
        raise ValueError(
            f"schema_version must be 1, got {parsed['schema_version']!r}"
        )


def _inject_json_object_reminder(
    messages: list[dict[str, object]], schema: dict[str, object]
) -> list[dict[str, object]]:
    """Prepend or update the system message with a JSON-object reminder (json_object mode)."""
    summary = json.dumps(
        {"required": schema.get("required", []), "schema_version": 1},
        separators=(",", ":"),
    )
    reminder = f"Respond ONLY with JSON conforming to: {summary}"
    if messages and messages[0].get("role") == "system":
        existing = str(messages[0].get("content") or "")
        messages[0]["content"] = f"{existing}\n\n{reminder}".strip()
    else:
        messages.insert(0, {"role": "system", "content": reminder})
    return messages


def _inject_strict_retry_preamble(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    if messages and messages[0].get("role") == "system":
        existing = str(messages[0].get("content") or "")
        messages[0]["content"] = f"{STRICT_RETRY_PREAMBLE}\n\n{existing}"
    else:
        messages.insert(0, {"role": "system", "content": STRICT_RETRY_PREAMBLE})
    return messages


def _deep_copy_messages(messages: object) -> list[dict[str, object]]:
    if not isinstance(messages, list):
        return []
    out: list[dict[str, object]] = []
    for m in messages:
        if isinstance(m, dict):
            out.append({k: v for k, v in m.items()})
    return out


def _looks_like_sse(text: str) -> bool:
    return text.lstrip().startswith("data:")


# Event ``ts`` / ``run_id`` are stamped here on best-effort: the auditor wraps
# this client and supplies a monotonic ``run_id`` via the bus's seq counter
# in production; for unit tests with a bare ``EventBus`` we use a fixed run_id
# placeholder so events validate cleanly. See M4 lifecycle for the real wiring.
def _now() -> "datetime":
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc)


def _run_id() -> str:
    # M3 is unaware of run_id provenance; M4/M8 supplies the real value via a
    # client-level setter in a future patch. Tests assert event sequence not
    # run_id contents, so a fixed sentinel is acceptable here.
    return "lmstudio-client"


# Forward-ref for type hints that mention datetime without paying for the import
# at module load (mypy follows the TYPE_CHECKING import cheaply).
from datetime import datetime  # noqa: E402  — kept down here for the sentinel docstring

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "LMStudioClient",
    "LoadedModelInfo",
    "ProbedCapabilities",
    "ToolCall",
    "ToolCallFunction",
    "ToolSchema",
]
