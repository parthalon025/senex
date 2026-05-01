"""senex.compaction — M6 context compaction executor (spec section 5.5.1).

Implements:
  * ``CompactionResult`` — pydantic v2 strict model for the compaction LLM
    response. Validated via ``TypeAdapter(CompactionResult).validate_python``
    at the boundary; business code never re-validates.
  * ``Compactor`` — one-instance-per-file executor that:
      - Builds the slice (all messages EXCEPT system prompt at index 0,
        the last ``preserve_recent_turns`` messages, and any prior
        ``[COMPACTED]\\n`` system messages).
      - Calls ``client.chat(task="compaction", ..., tools=None)`` per
        spec section 5.5.1 mechanism (tools=None mandatory).
      - Validates the response, retries once on validation failure with a
        stricter-prompt addendum, and on second failure raises
        ``CompactionFailed``.
      - Builds the ``[COMPACTED]\\n`` system message (secret-redacted,
        ANSI-stripped) and rewrites the message list as
        ``[messages[0]] + [<COMPACTED>] + messages[-preserve_recent_turns:]``.
      - Maintains a per-instance ``compactions_so_far`` counter capped by
        ``config.max_compactions_per_file``.
  * ``should_trigger`` — pure function form for M8 / tests (mirrors the
    ``Compactor.should_trigger`` method).
  * ``CompactionFailed``, ``CompactionLoopExceeded``, ``ContextOverflow``
    — top-level named exceptions per conventions section 4.

Trust boundary: the compaction prompt itself contains the
``<UNTRUSTED_CONTENT>`` clause (see ``senex/prompts/compaction.md``); the
compaction-output schema validation is the second line of defense.

Iteration controller: M5 ``senex.tools.loop.ToolLoop`` calls
``Compactor.maybe_compact`` after each tool-result append, before the next
chat call. ``Compactor`` has no awareness of the tool loop; it only knows
about messages and a context-window budget.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from senex.config import CompactionCfg
from senex.events import (
    CompactionComplete,
    CompactionError,
    CompactionTriggered,
    EventBus,
)
from senex.inference_client import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from senex.secret_redactor import SecretRedactor

if TYPE_CHECKING:
    from senex.llm_client import LLMClient

# ANSI / control-sequence regex per conventions section 5 — mirrors the
# pattern used in ``senex.inference_client``. Compaction output flows back
# into history and must be sanitized before insertion.
_ANSI_RE: Final = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")
_CTRL_RE: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Strips leading <think>...</think> blocks that Gemma 4 E4B (and other
# reasoning models) may emit before their JSON answer when LM Studio has
# not pre-stripped the thinking section. Non-greedy + DOTALL so the regex
# does not swallow multiple blocks in the unlikely event the model emits
# more than one. Applied only on the raw-text fallback path of
# ``_parse_response``; the structured ``content_dict`` path is unaffected.
_THINK_BLOCK_RE: Final = re.compile(r"\A\s*<think>.*?</think>\s*", re.DOTALL)

_COMPACTED_PREFIX: Final = "[COMPACTED]\n"

_RETRY_ADDENDUM: Final = (
    "Re-emit ONLY valid JSON conforming to CompactionResult; previous "
    "response was invalid. No prose, no markdown fences."
)


# ---------------------------------------------------------------------------
# Public pydantic surface
# ---------------------------------------------------------------------------


class CompactionResult(BaseModel):
    """Strict response shape produced by the compaction LLM call.

    Mirrors ``senex/schema/compaction_response.schema.json`` (spec section 5.5.1).
    Extra keys are forbidden; ``evidence_summary`` is bounded at 16 KiB to
    cap memory growth from a runaway model.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_summary: Annotated[str, Field(max_length=16384)]
    key_findings_so_far: list[str] = Field(default_factory=list)
    unanswered_questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Named exceptions (top-level per conventions section 4)
# ---------------------------------------------------------------------------


class CompactionFailed(Exception):
    """Raised on LMS error or schema-invalid response after the retry."""

    def __init__(
        self,
        kind: Literal["lms_error", "schema_invalid"],
        path: Path,
        cause: BaseException | None = None,
    ) -> None:
        self.kind = kind
        self.path = path
        self.cause = cause
        super().__init__(f"compaction failed kind={kind} path={path}")


class CompactionLoopExceeded(Exception):
    """Raised when the (max_compactions_per_file + 1)-th trigger fires."""

    def __init__(self, path: Path, compactions_so_far: int, limit: int) -> None:
        self.path = path
        self.compactions_so_far = compactions_so_far
        self.limit = limit
        super().__init__(
            f"compaction loop exceeded path={path} "
            f"compactions_so_far={compactions_so_far} limit={limit}"
        )


class ContextOverflow(Exception):
    """Raised by the tool loop (NOT ``Compactor``) when compaction is disabled
    and the natural token count exceeds the model's context window. Lives in
    this module so M8 can catch it from one import (per spec section 5.5.1
    opt-out clause).
    """

    def __init__(self, path: Path, token_count: int, context_window: int) -> None:
        self.path = path
        self.token_count = token_count
        self.context_window = context_window
        super().__init__(
            f"context overflow path={path} tokens={token_count} ctx={context_window}"
        )


# ---------------------------------------------------------------------------
# Pure trigger function (re-exported for M8 / tests).
# ---------------------------------------------------------------------------


def should_trigger(
    *, token_count: int, context_window: int, config: CompactionCfg
) -> bool:
    """Return ``True`` when the message slice has crossed the trigger threshold.

    Pure function: the only state it touches is ``config``. ``Compactor``
    delegates to this for unit testability; tests can craft token counts
    directly.
    """
    if not config.enabled:
        return False
    threshold = int(config.trigger_pct * context_window)
    return token_count >= threshold


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_chat_message(m: dict[str, Any]) -> ChatMessage:
    role = m.get("role", "user")
    kwargs: dict[str, Any] = {"role": role}
    if m.get("content") is not None:
        kwargs["content"] = m["content"]
    if m.get("tool_calls") is not None:
        tcs: list[ToolCall] = []
        for tc in m["tool_calls"]:
            if isinstance(tc, ToolCall):
                tcs.append(tc)
                continue
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
    return ChatMessage(**kwargs)


def _is_compacted_block(m: dict[str, Any]) -> bool:
    content = m.get("content")
    return isinstance(content, str) and content.startswith(_COMPACTED_PREFIX)


def _strip_think_block(text: str) -> str:
    """Strip a leading ``<think>...</think>`` block from *text*, if present.

    With SGLang as the inference backend, thinking content is returned in the
    ``reasoning_content`` field of the response, NOT inline in ``content``.
    This function is therefore a no-op under normal SGLang operation. It is
    retained as a safety net for non-streaming edge cases or fallback to
    LM Studio, where Gemma 4 E4B may emit thinking blocks inline before the
    JSON answer. ``lmstudio_client._do_stream`` already strips these for the
    streaming path; this provides belt-and-suspenders coverage for the
    raw-text fallback path of ``_parse_response``.
    """
    return _THINK_BLOCK_RE.sub("", text)


def _strip_control(text: str) -> str:
    return _CTRL_RE.sub("", _ANSI_RE.sub("", text))


def _format_compacted_block(result: CompactionResult, redactor: SecretRedactor) -> str:
    findings = ", ".join(result.key_findings_so_far) if result.key_findings_so_far else "none"
    questions = (
        ", ".join(result.unanswered_questions)
        if result.unanswered_questions
        else "none"
    )
    body = (
        f"{_COMPACTED_PREFIX}"
        f"{result.evidence_summary}\n"
        f"\n"
        f"Key findings noted: {findings}\n"
        f"Unanswered: {questions}"
    )
    body = _strip_control(body)
    return redactor.redact(body)


def _parse_response(response: ChatResponse) -> CompactionResult:
    """Parse + validate a chat response into a ``CompactionResult``.

    Raises ``ValidationError`` (pydantic) or ``ValueError`` on failure;
    the caller decides whether to retry or raise ``CompactionFailed``.
    """
    payload = response.content_dict
    if payload is None:
        # Schema-less compaction call returns content as raw JSON text.
        # Strip any leading <think>...</think> block before parsing; Gemma 4
        # E4B emits thinking sections before the JSON answer in thinking mode,
        # and lmstudio_client's streaming-path strip is the primary defence,
        # but this provides belt-and-suspenders coverage for non-streaming or
        # edge-case responses where the think block survives into content.
        import json

        if not response.content:
            raise ValueError("compaction response: empty body")
        payload = json.loads(_strip_think_block(response.content))
    return TypeAdapter(CompactionResult).validate_python(payload)


# ---------------------------------------------------------------------------
# Compactor — one instance per file (M8 owns instantiation)
# ---------------------------------------------------------------------------


class Compactor:
    """Per-file compaction executor (spec section 5.5.1).

    Construction is cheap; ``run`` issues one (or, on retry, two) LLM calls
    via the injected ``LLMClient``. The instance owns the
    ``compactions_so_far`` counter; M8 instantiates a new ``Compactor`` per
    file so the budget resets implicitly.
    """

    def __init__(
        self,
        client: "LLMClient",
        prompt_path: Path,
        schema_path: Path,
        config: CompactionCfg,
        bus: EventBus,
        run_id: str,
        path: Path,
        model_id: str,
        redactor: SecretRedactor,
    ) -> None:
        self._client = client
        self._prompt_path = prompt_path
        self._schema_path = schema_path
        self._config = config
        self._bus = bus
        self._run_id = run_id
        self._path = path
        self._model_id = model_id
        self._redactor = redactor
        self._compactions_so_far: int = 0
        # Cached prompt body; the file is snapshotted at preflight (TOCTOU).
        self._prompt_body: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def compactions_so_far(self) -> int:
        return self._compactions_so_far

    @property
    def path(self) -> Path:
        return self._path

    def should_trigger(
        self, messages: list[dict[str, Any]], context_window: int
    ) -> bool:
        """Return ``True`` when the running history crosses ``trigger_pct``.

        Reuses ``client.count_tokens`` so the budget math the loop sees
        matches what the model sees (per spec section 5.5.1 mechanism).
        """
        if not self._config.enabled:
            return False
        chat_msgs = [_to_chat_message(m) for m in messages]
        used = self._client.count_tokens(chat_msgs, self._model_id)
        return should_trigger(
            token_count=used, context_window=context_window, config=self._config
        )

    async def maybe_compact(
        self, messages: list[dict[str, Any]], context_window: int
    ) -> list[dict[str, Any]]:
        """Trigger-guarded entry point wired into ``ToolLoop``.

        Returns ``messages`` unchanged when the trigger does not fire;
        otherwise emits ``CompactionTriggered``, runs ``run``, increments
        the counter, emits ``CompactionComplete``, and returns the
        rewritten history. On the (max+1)-th trigger raises
        ``CompactionLoopExceeded``. Any exception inside ``run`` emits
        ``CompactionError`` before re-raising.
        """
        if not self.should_trigger(messages, context_window):
            return messages

        if self._compactions_so_far >= self._config.max_compactions_per_file:
            raise CompactionLoopExceeded(
                path=self._path,
                compactions_so_far=self._compactions_so_far,
                limit=self._config.max_compactions_per_file,
            )

        # Pre-trigger token measurement (for the event payload).
        chat_msgs = [_to_chat_message(m) for m in messages]
        tokens_before = self._client.count_tokens(chat_msgs, self._model_id)
        threshold = int(self._config.trigger_pct * context_window)

        await self._bus.publish(
            CompactionTriggered(
                ts=_now(),
                run_id=self._run_id,
                path=str(self._path),
                message_tokens_before=tokens_before,
                threshold=threshold,
            )
        )

        try:
            new_messages = await self.run(messages)
        except asyncio.CancelledError:
            # Cancellation must propagate (conventions section 3); do not emit
            # a CompactionError event for ordinary task cancellation.
            raise
        except CompactionFailed:
            await self._bus.publish(
                CompactionError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=str(self._path),
                    error_kind="compaction_failed",
                )
            )
            raise
        except Exception:
            await self._bus.publish(
                CompactionError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=str(self._path),
                    error_kind="unknown",
                )
            )
            raise

        self._compactions_so_far += 1

        # Post-compaction token measurement (for the event payload).
        chat_after = [_to_chat_message(m) for m in new_messages]
        tokens_after = self._client.count_tokens(chat_after, self._model_id)

        await self._bus.publish(
            CompactionComplete(
                ts=_now(),
                run_id=self._run_id,
                path=str(self._path),
                message_tokens_after=tokens_after,
                kept_findings=0,  # M6 does not introspect findings.
            )
        )
        return new_messages

    async def run(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build the slice, call the model, validate, rewrite history.

        Returns the new message list. On unrecoverable failure raises
        ``CompactionFailed``; ``asyncio.CancelledError`` propagates.
        """
        if not messages:
            raise CompactionFailed(
                kind="schema_invalid",
                path=self._path,
                cause=ValueError("empty messages"),
            )

        system_msg = messages[0]
        preserve_n = self._config.preserve_recent_turns
        if preserve_n > 0:
            tail = messages[-preserve_n:]
            slice_msgs = [
                m
                for m in messages[1:-preserve_n]
                if not _is_compacted_block(m)
            ]
        else:
            tail = []
            slice_msgs = [m for m in messages[1:] if not _is_compacted_block(m)]

        # First attempt.
        try:
            result = await self._call_and_validate(slice_msgs, retry=False)
        except (ValidationError, ValueError):
            # Retry once with a stricter-prompt addendum.
            try:
                result = await self._call_and_validate(slice_msgs, retry=True)
            except (ValidationError, ValueError) as exc:
                raise CompactionFailed(
                    kind="schema_invalid", path=self._path, cause=exc
                ) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise CompactionFailed(
                kind="lms_error", path=self._path, cause=exc
            ) from exc

        # Build the [COMPACTED] system message.
        compacted_block: dict[str, Any] = {
            "role": "system",
            "content": _format_compacted_block(result, self._redactor),
        }
        return [system_msg, compacted_block, *tail]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_prompt(self) -> str:
        if self._prompt_body is None:
            self._prompt_body = self._prompt_path.read_text(encoding="utf-8")
        return self._prompt_body

    async def _call_and_validate(
        self, slice_msgs: list[dict[str, Any]], *, retry: bool
    ) -> CompactionResult:
        prompt_body = self._load_prompt()
        if retry:
            prompt_body = f"{prompt_body}\n\n{_RETRY_ADDENDUM}\n"

        wire_messages: list[ChatMessage] = [
            ChatMessage(role="system", content=prompt_body),
            *(_to_chat_message(m) for m in slice_msgs),
        ]

        # tools=None mandatory: the compaction model must not call tools.
        response = await self._client.chat(
            task="compaction",
            messages=wire_messages,
            schema=None,
            tools=None,
        )
        return _parse_response(response)


# ---------------------------------------------------------------------------
# Module-private clock — kept module-local to ease patching from tests.
# ---------------------------------------------------------------------------


def _now() -> "datetime":
    return datetime.now(tz=timezone.utc)


# Late import to avoid pulling datetime at TYPE_CHECKING time only; we need it
# at runtime here.
from datetime import datetime, timezone  # noqa: E402

__all__ = [
    "CompactionFailed",
    "CompactionLoopExceeded",
    "CompactionResult",
    "Compactor",
    "ContextOverflow",
    "should_trigger",
]


# ``Iterable`` import retained for type-narrowing helpers in future
# refactors. mypy-strict treats unused imports as errors only when the
# import lacks any reference; this comment documents intent.
_UNUSED: Iterable[Any] = ()
