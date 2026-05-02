"""senex.tools.loop - bounded ToolLoop iteration controller (M5 supersedes M3).

Implements spec section 5.11.3 (loop semantics: bounded iteration, budget
exhaustion, post-processing) and section 5.5 step list (multi-call
dispatch loop).

M3 owns the per-call mechanics (one HTTP round-trip, streaming, schema
fallback, retry, redaction of one response). M5 owns the multi-call
iteration (registry dispatch, budget cap, compaction trigger, budget-
exhaustion injection).

ToolLoop drives the model <-> tool conversation until convergence
(``response.tool_calls`` is empty -> success) or budget exhaustion
(``calls_made >= max_calls`` -> emit ToolBudgetExhausted, run one final
no-tools turn, return). The loop is bounded by ``max_calls + 1`` outer
iterations; the final iteration is reserved for the no-tools turn so the
model can emit its structured response.

The compaction hook is called between turns and does NOT count against
``max_calls``; M6 (``senex.compaction.Compactor.maybe_compact``) supplies
the real compactor wired via a closure that captures the model's
``context_window``. M5 ships a no-op stub for testing.

M6 integration contract:

* The closure passed as ``compactor`` invokes ``Compactor.maybe_compact``,
  which (per spec section 5.5.1) emits ``CompactionTriggered`` /
  ``CompactionComplete`` / ``CompactionError`` events and rewrites the
  history when the trigger fires. The closure returns the (possibly
  unchanged) history; ``ToolLoop`` swaps it in regardless.
* ``CompactionFailed``, ``CompactionLoopExceeded``, and ``ContextOverflow``
  raised inside the hook propagate out of ``ToolLoop.run`` so M8
  ``FileAuditPhase`` can map them to per-file ``<file>.ERROR.md``
  artifacts.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from senex.events import EventBus
from senex.events import ToolBudgetExhausted as ToolBudgetExhaustedEvent
from senex.events import ToolCall as ToolCallEvent
from senex.events import ToolError as ToolErrorEvent
from senex.events import ToolResult as ToolResultEvent
from senex.inference_client import (
    ChatMessage,
    ChatResponse,
    InferenceClient,
    ToolCall,
    ToolCallFunction,
)
from senex.secret_redactor import SecretRedactor

from .context import ToolContext
from .registry import ToolRegistry, ToolResult

# Compaction hook signature: receives the message history, returns the
# rewritten list when compaction fires, or ``None`` to leave it unchanged.
MaybeCompact = Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]] | None]]


def _resolve_npx_path() -> Path:
    """Resolve the ``npx`` binary to an absolute path.

    ``shutil.which`` covers the common case.  On Windows it may return
    ``None`` when senex is launched from Git Bash (whose ``PATH`` often
    omits the Node.js directory); we fall back to well-known installation
    locations so the gitnexus tools work regardless of the launch shell.
    """
    found = shutil.which("npx")
    if found:
        return Path(found)
    if sys.platform == "win32":
        _candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npx.cmd",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs" / "npx.cmd",
            Path(os.environ.get("APPDATA", "")) / "npm" / "npx.cmd",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "npx.cmd",
        ]
        for candidate in _candidates:
            if candidate.is_file():
                return candidate
    return Path("npx")  # last resort — subprocess will raise FileNotFoundError


# Mapping from registry-result kind -> event-Literal kind. The events module
# restricts ToolError.kind to a fixed Literal; tool-side kinds are richer.
# Unknown / regex_invalid / regex_timeout / dispatch_failed all map to the
# closest event kind (defense in depth: any unexpected kind falls back to
# "internal" so the event still validates).
_KIND_TO_EVENT_KIND: dict[str, str] = {
    "schema_invalid": "schema_invalid",
    "path_rejected": "path_rejected",
    "regex_invalid": "schema_invalid",
    "regex_timeout": "timeout",
    "dispatch_failed": "subprocess",
    "unavailable": "unavailable",
    "timeout": "timeout",
    "unknown_tool": "internal",
}


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class ToolLoopMetrics:
    """Per-file metrics surfaced by ``ToolLoop.run()`` (M11).

    The model's ``ChatResponse`` carries token counts and per-call latency,
    but tools dispatched THROUGH the loop are invisible to the response.
    The renderer's per-file header (spec §7.1) wants the count + breakdown,
    so we expose it as a sibling to the final ``ChatResponse``.

    Attributes:
        tool_counts: ``{tool_name: count}`` of every tool dispatched in
            this file's turn-loop. Includes both successful results and
            errors (the spec's "Tools used: N / 5" cap counts both).
        compactions_used: read off the injected compactor after the loop.
            Populated by the caller (M8 ``FileAuditPhase``); ToolLoop has
            no handle on the compactor's counter.
    """

    tool_counts: dict[str, int] = field(default_factory=dict)
    compactions_used: int = 0

    @property
    def total_calls(self) -> int:
        return sum(self.tool_counts.values())


def _to_chat_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
    """Convert dict-shaped history to typed ChatMessage list."""
    out: list[ChatMessage] = []
    for m in messages:
        role = m.get("role", "user")
        kwargs: dict[str, Any] = {"role": role}
        if m.get("content") is not None:
            kwargs["content"] = m["content"]
        if m.get("tool_calls") is not None:
            tcs: list[ToolCall] = []
            for tc in m["tool_calls"]:
                if isinstance(tc, ToolCall):
                    tcs.append(tc)
                else:
                    tcs.append(
                        ToolCall(
                            id=tc.get("id", ""),
                            type=tc.get("type", "function"),
                            function=ToolCallFunction(
                                name=tc.get("function", {}).get("name", ""),
                                arguments=tc.get("function", {}).get("arguments", ""),
                            ),
                        )
                    )
            kwargs["tool_calls"] = tcs
        if m.get("tool_call_id") is not None:
            kwargs["tool_call_id"] = m["tool_call_id"]
        out.append(ChatMessage(**kwargs))
    return out


def _assistant_message_dict(response: ChatResponse) -> dict[str, Any]:
    """Render an assistant ChatResponse back into a history-shaped dict."""
    msg: dict[str, Any] = {"role": "assistant"}
    if response.content:
        msg["content"] = response.content
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in response.tool_calls
        ]
    return msg


class ToolLoop:
    """Bounded model <-> tool dispatch loop.

    See module docstring for the M3 <-> M5 reconciliation note.
    """

    def __init__(
        self,
        client: InferenceClient,
        registry: ToolRegistry,
        repo_root: Path,
        repo_name: str,
        secret_redactor: SecretRedactor,
        compactor: MaybeCompact,
        max_calls: int,
        tool_timeout_seconds: float = 30.0,
        max_result_tokens: int = 2048,
        npx_path: Path | None = None,
        bus: EventBus | None = None,
        cot_reasoning_turn: bool = False,
    ) -> None:
        self._client = client
        self._registry = registry
        self._repo_root = repo_root.resolve()
        self._repo_name = repo_name
        self._redactor = secret_redactor
        self._compactor = compactor
        self._max_calls = max_calls
        self._tool_timeout_seconds = tool_timeout_seconds
        self._max_result_tokens = max_result_tokens
        self._npx_path = npx_path if npx_path is not None else _resolve_npx_path()
        self._bus = bus
        self._cot_reasoning_turn = cot_reasoning_turn
        # Per-file tool dispatch counts; reset by each ``run()`` so a
        # reused loop instance reports the most recent file's tools.
        self._tool_counts: Counter[str] = Counter()

    @property
    def tool_counts(self) -> dict[str, int]:
        """Dispatched tool counts from the most recent ``run()``.

        Used by ``FileAuditPhase`` to populate ``FileMetadata.tools_used``.
        """
        return dict(self._tool_counts)

    def _ctx(self) -> ToolContext:
        return ToolContext(
            repo_root=self._repo_root,
            repo_name=self._repo_name,
            redactor=self._redactor,
            npx_path=self._npx_path,
            tool_timeout_seconds=self._tool_timeout_seconds,
            max_result_tokens=self._max_result_tokens,
        )

    async def run(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        lens_tools: list[str],
        *,
        path: str = "",
        run_id: str = "tool-loop",
        task: str = "file_audit",
    ) -> ChatResponse:
        """Drive the model <-> tool loop until convergence or budget exhaustion.

        M3 owns the per-call mechanics (one HTTP round-trip, streaming, schema
        fallback, retry, redaction of one response). M5 owns the multi-call
        iteration (registry dispatch, budget cap, compaction trigger, budget-
        exhaustion injection).

        Algorithm (bounded by max_calls + 1 outer iterations):

            for i in range(max_calls + 1):
                tools = registry.openai_tools(lens_tools) if i < max_calls else None
                response = await client.chat(task, messages, schema, tools)
                messages.append(assistant_message)
                if not response.tool_calls:
                    return response                  # success exit
                for tc in response.tool_calls:
                    publish ToolCall(...)
                    result = await registry.dispatch(tc.id, tc.name, tc.args, ctx)
                    publish ToolResult(...) or ToolError(...)
                    messages.append(tool message)
                    calls_made += 1
                compacted = await compactor(messages)
                if compacted is not None:
                    messages = compacted
                if calls_made >= max_calls:
                    publish ToolBudgetExhausted
                    messages.append(budget-exhausted system message)
                    final = await client.chat(task, messages, schema, tools=None)
                    return final
        """
        ctx = self._ctx()
        calls_made = 0
        # Reset per-file tool counts so a reused loop reports only the
        # current file's dispatches (M11 bug 2 — ``FileMetadata.tools_used``).
        self._tool_counts = Counter()

        # Pre-loop CoT reasoning turn: a prose (no schema, no tools) call whose
        # response is appended to history so the structured-output loop has the
        # model's explicit step-by-step reasoning in context. Opt-in via
        # ``ToolsCfg.cot_reasoning_turn``; default False preserves existing behaviour.
        if self._cot_reasoning_turn:
            reasoning_messages = _to_chat_messages(messages)
            reasoning_response = await self._client.chat(
                task=task,
                messages=reasoning_messages,
                schema=None,
                tools=None,
            )
            messages.append(_assistant_message_dict(reasoning_response))

        for i in range(self._max_calls + 1):
            tools_arg = (
                self._registry.openai_tools(lens_tools)
                if i < self._max_calls
                else None
            )
            chat_messages = _to_chat_messages(messages)
            # SGLang handles tools + structured output natively; pass schema
            # on every turn so the model produces a validated response
            # regardless of whether tool calls are also active.
            response = await self._client.chat(
                task=task,
                messages=chat_messages,
                schema=schema,
                tools=tools_arg,
            )
            messages.append(_assistant_message_dict(response))

            if not response.tool_calls:
                return response  # success exit.

            # Dispatch each tool call in order.
            for tc in response.tool_calls:
                self._tool_counts[tc.function.name] += 1
                if self._bus is not None:
                    await self._bus.publish(
                        ToolCallEvent(
                            ts=_now(),
                            run_id=run_id,
                            path=path,
                            tool_name=tc.function.name,
                            tool_input=tc.function.arguments[:512],
                            call_id=tc.id,
                        )
                    )
                result = await self._registry.dispatch(
                    tc.id, tc.function.name, tc.function.arguments, ctx
                )
                if self._bus is not None:
                    if isinstance(result, ToolResult):
                        # Approximate result-token count using cl100k_base.
                        from .registry import _get_encoder

                        enc = _get_encoder()
                        if enc is not None:
                            result_tokens = len(enc.encode(result.content))
                        else:
                            result_tokens = len(result.content) // 4
                        await self._bus.publish(
                            ToolResultEvent(
                                ts=_now(),
                                run_id=run_id,
                                path=path,
                                tool_name=tc.function.name,
                                call_id=tc.id,
                                result_tokens=result_tokens,
                                latency_ms=0,
                                truncated=result.truncated,
                            )
                        )
                    else:
                        # ToolError result.
                        event_kind = _KIND_TO_EVENT_KIND.get(result.kind, "internal")
                        await self._bus.publish(
                            ToolErrorEvent(
                                ts=_now(),
                                run_id=run_id,
                                path=path,
                                tool_name=tc.function.name,
                                call_id=tc.id,
                                kind=event_kind,  # type: ignore[arg-type]
                                error_message=result.message,
                            )
                        )
                # Append the tool-role message regardless of success/error.
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result.content,
                    }
                )
                calls_made += 1

            # Compaction hook (between turns; does NOT count against max_calls).
            compacted = await self._compactor(messages)
            if compacted is not None:
                messages.clear()
                messages.extend(compacted)

            if calls_made >= self._max_calls:
                # Final-turn budget-exhaustion injection.
                if self._bus is not None:
                    await self._bus.publish(
                        ToolBudgetExhaustedEvent(
                            ts=_now(),
                            run_id=run_id,
                            path=path,
                            calls_made=calls_made,
                        )
                    )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Tool budget exhausted. Emit your final structured "
                            "response now. Do not call any more tools."
                        ),
                    }
                )
                final_messages = _to_chat_messages(messages)
                final = await self._client.chat(
                    task=task,
                    messages=final_messages,
                    schema=schema,
                    tools=None,
                )
                messages.append(_assistant_message_dict(final))
                return final

        # Unreachable: loop bound is max_calls + 1, the last iteration always
        # returns inside the budget-exhaustion branch.
        raise AssertionError("ToolLoop bound exceeded - programmer error")


__all__ = ["MaybeCompact", "ToolLoop", "ToolLoopMetrics"]
