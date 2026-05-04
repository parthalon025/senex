"""senex.tools.registry — registration + OpenAI-schema emission + dispatch.

Implements M5 plan key contracts (§``ToolRegistry``):

- ``register(name, input_model, handler, description)`` — store handler
  metadata.
- ``openai_tools(enabled_names)`` — emit OpenAI function-calling schema
  list ordered by ``enabled_names`` (raises ``KeyError`` on unknown name).
- ``dispatch(call_id, name, raw_input_json, ctx)`` — strict pipeline:
  lookup -> JSON parse -> pydantic validation -> handler under timeout
  -> serialize -> ANSI strip -> redact -> truncate to
  ``ctx.max_result_tokens``.

The registry is the single choke point for tool dispatch. Every safety
primitive (path, regex, ANSI, redaction, truncation) is enforced here so
the 6 tool handlers can be thin wrappers over the underlying operation.
Every error path produces a structured ``ToolError`` so the model
receives well-formed feedback (per spec §5.11.1: "the model sees a
schema-shaped error, not an exception").
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import tiktoken
from pydantic import BaseModel, ValidationError

from .context import ToolContext
from .safety import redact_tool_result

log = logging.getLogger(__name__)

_TRUNC_MARKER_TEMPLATE: str = "\n\n[truncated: {n} tokens omitted]"
# tiktoken encoder cache (conventions §14: cache compiled / heavy resources).
_ENCODER_CACHE: dict[str, tiktoken.Encoding | None] = {}


def _get_encoder() -> tiktoken.Encoding | None:
    """Return cached cl100k_base encoder; ``None`` if tiktoken can't load it."""
    if "cl100k_base" in _ENCODER_CACHE:
        return _ENCODER_CACHE["cl100k_base"]
    try:
        enc: tiktoken.Encoding | None = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 — tiktoken raises generic on missing encoding
        enc = None
    _ENCODER_CACHE["cl100k_base"] = enc
    return enc


# ----- result types -----------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """Successful tool dispatch outcome consumed by ``ToolLoop``.

    Distinct from ``senex.events.ToolResult`` (the event); this is the
    in-memory return shape of ``ToolRegistry.dispatch``. The loop translates
    one to the other.
    """

    call_id: str
    name: str
    content: str  # post-ANSI-strip, post-redact, post-truncate.
    truncated: bool


@dataclass(frozen=True)
class ToolError:
    """Failed tool dispatch outcome consumed by ``ToolLoop``.

    ``kind`` mirrors the table in ``exceptions.py`` plus two registry-only
    kinds (``unknown_tool`` and ``timeout``). The loop maps ``kind`` to the
    event ``ToolError.kind`` field and serializes ``message`` as the tool's
    response content for the next chat turn.
    """

    call_id: str
    name: str
    kind: str
    message: str

    @property
    def content(self) -> str:
        """Render the error as the assistant-facing tool message body."""
        return f"[tool error: {self.kind}] {self.message}"


# ----- registry ---------------------------------------------------------------


@dataclass
class _Registration:
    name: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Awaitable[Any]]
    description: str


class ToolRegistry:
    """Map tool name -> (input_model, handler, description). See module doc."""

    def __init__(self) -> None:
        self._tools: dict[str, _Registration] = {}

    def register(
        self,
        name: str,
        input_model: type[BaseModel],
        handler: Callable[[BaseModel, ToolContext], Awaitable[Any]],
        description: str,
    ) -> None:
        """Register ``handler`` under ``name``; overwrites any prior registration.

        Re-registering with a different ``input_model`` changes ``input_schema(name)``
        and therefore ``compute_tool_pack_hash`` — this is intentional and exercised
        by ``test_hash_changes_when_input_schema_changes``.
        """
        self._tools[name] = _Registration(
            name=name,
            input_model=input_model,
            handler=handler,
            description=description,
        )

    def is_registered(self, name: str) -> bool:
        return name in self._tools

    def input_schema(self, name: str) -> dict[str, Any]:
        """Return the OpenAI-format JSON schema dict for the named tool's input.

        Used by ``compute_tool_pack_hash`` (M5 Task 5.10) to detect schema-level
        drift across runs.
        """
        if name not in self._tools:
            raise KeyError(f"tool not registered: {name!r}")
        return _normalize_schema(self._tools[name].input_model.model_json_schema())

    def openai_tools(self, enabled_names: list[str]) -> list[dict[str, Any]]:
        """Return OpenAI function-calling schema list ordered by ``enabled_names``.

        Raises:
            KeyError: a name in ``enabled_names`` is not registered.
        """
        out: list[dict[str, Any]] = []
        for name in enabled_names:
            if name not in self._tools:
                raise KeyError(f"tool not registered: {name!r}")
            reg = self._tools[name]
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": reg.description,
                        "parameters": _normalize_schema(reg.input_model.model_json_schema()),
                    },
                }
            )
        return out

    async def dispatch(
        self,
        call_id: str,
        name: str,
        raw_input_json: str,
        ctx: ToolContext,
    ) -> ToolResult | ToolError:
        """Strict pipeline (each step its own try/except producing a structured error).

        See module docstring for the full pipeline description.
        """
        # 1. Lookup.
        if name not in self._tools:
            return ToolError(
                call_id=call_id,
                name=name,
                kind="unknown_tool",
                message=f"tool {name!r} is not registered",
            )
        reg = self._tools[name]

        # 2. Parse JSON arguments.
        try:
            parsed = json.loads(raw_input_json) if raw_input_json else {}
        except json.JSONDecodeError as exc:
            return ToolError(
                call_id=call_id,
                name=name,
                kind="schema_invalid",
                message=f"arguments not valid JSON: {exc}",
            )

        # 3. Pydantic validation.
        try:
            inp = reg.input_model.model_validate(parsed)
        except ValidationError as exc:
            return ToolError(
                call_id=call_id,
                name=name,
                kind="schema_invalid",
                message=ctx.redactor.redact(str(exc)),
            )

        # 4. Run handler under timeout.
        try:
            raw_result = await asyncio.wait_for(
                reg.handler(inp, ctx),
                timeout=ctx.tool_timeout_seconds,
            )
        except TimeoutError:
            return ToolError(
                call_id=call_id,
                name=name,
                kind="timeout",
                message=f"handler exceeded {ctx.tool_timeout_seconds}s",
            )
        except Exception as exc:  # noqa: BLE001 — registry must catch ALL handler errors
            # Per conventions §4: the registry IS the boundary for ToolingError-shaped
            # failures. Convert known tooling errors to their kind; all others map to
            # dispatch_failed. Redact the message before returning.
            from .exceptions import ToolingError  # avoid module-load cycle

            if isinstance(exc, ToolingError):
                return ToolError(
                    call_id=call_id,
                    name=name,
                    kind=exc.kind,
                    message=ctx.redactor.redact(str(exc)),
                )
            return ToolError(
                call_id=call_id,
                name=name,
                kind="dispatch_failed",
                message=ctx.redactor.redact(f"{type(exc).__name__}: {exc}"),
            )

        # 5. Serialize result to text.
        text = _serialize_result(raw_result)
        # 6. ANSI strip + redact (in that order — see safety.redact_tool_result).
        text = redact_tool_result(text, ctx.redactor)
        # 7. Truncate to max_result_tokens.
        truncated_text, was_truncated = _truncate_to_tokens(
            text, ctx.max_result_tokens
        )

        return ToolResult(
            call_id=call_id,
            name=name,
            content=truncated_text,
            truncated=was_truncated,
        )


# ----- helpers ----------------------------------------------------------------


def _serialize_result(raw: Any) -> str:
    """Convert a handler return value to text.

    Pydantic ``BaseModel`` -> JSON via ``model_dump_json`` (conventions §10).
    ``str`` -> as-is. Anything else -> ``json.dumps``.
    """
    if isinstance(raw, BaseModel):
        return raw.model_dump_json()
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        return str(raw)


def _truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Return ``(text_or_truncated, was_truncated)`` using cl100k_base tokens."""
    enc = _get_encoder()
    if enc is None:
        # Fallback: char-based estimate at ~4 chars/token.
        char_cap = max_tokens * 4
        if len(text) <= char_cap:
            return text, False
        omitted = (len(text) - char_cap) // 4
        return (
            text[:char_cap]
            + _TRUNC_MARKER_TEMPLATE.format(n=max(1, omitted)),
            True,
        )
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text, False
    kept = tokens[:max_tokens]
    omitted = len(tokens) - max_tokens
    return (
        enc.decode(kept) + _TRUNC_MARKER_TEMPLATE.format(n=omitted),
        True,
    )


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip pydantic-internal keys for OpenAI compatibility.

    Pydantic adds ``title``, ``$defs``, etc. that confuse some OpenAI-format
    consumers; the function-calling spec only requires ``type`` + ``properties``
    + ``required`` + ``additionalProperties``. We pass through everything but
    drop the ``title`` keys since they are not load-bearing.
    """
    out = dict(schema)
    out.pop("title", None)
    return out


__all__ = ["ToolError", "ToolRegistry", "ToolResult"]
