"""senex.tools.search_code - claude-context MCP HTTP client.

Implements spec section 5.11.1 (search_code in v1 tool set). Best-effort:
when the claude-context MCP server is not reachable, returns
ToolError(kind="unavailable") rather than failing the audit. M4
preflight removes search_code from the lens's enabled_tools when
unreachable; the runtime check here is defense in depth.

Configuration: ``CLAUDE_CONTEXT_URL`` environment variable
(default ``http://localhost:8765``).
"""
from __future__ import annotations

import os
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .context import ToolContext
from .exceptions import ToolDispatchFailed, ToolUnavailable
from .registry import ToolRegistry

_TOOL_NAME: Final[str] = "search_code"
_DESCRIPTION: Final[str] = (
    "Semantic-search the audited repo via claude-context MCP. Returns up to "
    "`limit` hits with file path, score, and snippet. Returns ToolError "
    "(unavailable) if the MCP server is not reachable."
)

_DEFAULT_BASE_URL: Final[str] = "http://localhost:8765"


class SearchCodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)


class SearchCodeHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    score: float = Field(ge=0.0, le=1.0)
    snippet: str


class SearchCodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hits: list[SearchCodeHit]


async def search_code_handler(
    inp: SearchCodeInput, ctx: ToolContext
) -> SearchCodeOutput:
    """POST to the claude-context MCP /search_code endpoint."""
    base_url = os.environ.get("CLAUDE_CONTEXT_URL", _DEFAULT_BASE_URL)
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(connect=2.0, read=ctx.tool_timeout_seconds, write=2.0, pool=2.0),
    ) as client:
        try:
            resp = await client.post(
                "/search_code",
                json={"query": inp.query, "limit": inp.limit},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ToolUnavailable(
                f"claude-context MCP not reachable at {base_url}: {exc}"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise ToolUnavailable(
                f"claude-context MCP read timeout: {exc}"
            ) from exc

        if 500 <= resp.status_code < 600:
            raise ToolDispatchFailed(
                f"claude-context MCP returned {resp.status_code}: {resp.text[:200]}"
            )
        if 400 <= resp.status_code < 500:
            raise ToolDispatchFailed(
                f"claude-context MCP returned {resp.status_code}: {resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ToolDispatchFailed(f"malformed MCP response: {exc}") from exc

        try:
            return SearchCodeOutput.model_validate(payload)
        except ValidationError as exc:
            raise ToolDispatchFailed(f"malformed MCP response: {exc}") from exc


def register_search_code(registry: ToolRegistry) -> None:
    """Register the ``search_code`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        SearchCodeInput,
        search_code_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "SearchCodeHit",
    "SearchCodeInput",
    "SearchCodeOutput",
    "register_search_code",
    "search_code_handler",
]
