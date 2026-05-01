"""senex.llm_client — ``LLMClient`` Protocol consumed by phases / ``ToolLoop``.

Declares the structural-typing contract that ``InferenceClient`` (M3) and any
future LLM backend must implement. Phases and the M5 tool loop depend on this
protocol, never on a concrete implementation, so the LMS client can be swapped
or stubbed in tests.

Implements spec §5.5 (chat surface) and §3 conventions (`Protocol` for duck
typing without runtime cost). Per §2: no ``Any`` in the public signature.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Forward refs only: we don't import the implementation module to keep
    # this file dependency-light and avoid a circular import.
    from senex.inference_client import (
        ChatMessage,
        ChatResponse,
        LoadedModelInfo,
        ProbedCapabilities,
        ToolSchema,
    )


@runtime_checkable
class LLMClient(Protocol):
    """Structural-typing contract for an LM Studio-compatible chat client.

    Implementations MUST handle ONE chat-completion round-trip per ``chat`` call.
    The iteration controller (multi-call dispatch loop, budget enforcement, and
    compaction triggering) lives in M5 ``senex.tools.loop.ToolLoop`` and consumes
    this protocol via dependency injection.
    """

    async def chat(
        self,
        *,
        task: str,
        messages: list[ChatMessage],
        schema: dict[str, object] | None,
        tools: list[ToolSchema] | None = None,
    ) -> ChatResponse:
        """Open one streamed chat-completion round-trip.

        Args:
            task: One of ``"file_audit"``, ``"cross_cutting"``, ``"compaction"``.
            messages: OpenAI-format conversation history.
            schema: JSON Schema for ``response_format``; ``None`` for unstructured
                tasks (e.g. compaction).
            tools: OpenAI tool schemas; ``None`` (or empty) disables the tool surface.

        Returns:
            A populated :class:`ChatResponse`. When the assistant emits
            ``tool_calls`` the response carries them and ``finish_reason ==
            "tool_calls"``; the caller is responsible for dispatching the
            tools and re-invoking ``chat``.
        """
        ...

    async def list_loaded_models(self) -> list[LoadedModelInfo]:
        """Return the currently-loaded models per ``GET /v1/models``."""
        ...

    async def probe_capabilities(self, model_id: str) -> ProbedCapabilities:
        """Probe model support for tools, json_schema-with-tools, streaming."""
        ...

    def count_tokens(self, messages: list[ChatMessage], model_id: str) -> int:
        """Approximate the prompt token count using the active tokenizer."""
        ...

    def compute_fingerprint(self, model_info: LoadedModelInfo) -> str:
        """Compute the canonical sha256 model fingerprint per spec §5.5.2.3."""
        ...

    async def aclose(self) -> None:
        """Release any held HTTP/network resources."""
        ...


__all__ = ["LLMClient"]
