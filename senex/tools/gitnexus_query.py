"""senex.tools.gitnexus_query - npx gitnexus query tool.

Implements spec section 5.11.1 (gitnexus_query in v1 tool set) and section
SEC-4 (subprocess hardening: list-form args, shell=False, absolute npx
path resolved at preflight). The tool is registered via
``register_gitnexus_query(registry)``; M8 wires this into the lens-driven
tool pack.

Subprocess invariants enforced here (mirroring senex.graph_awareness):

- list-form args (never string-concatenated, never shell=True)
- npx absolute path pinned at preflight via ToolContext.npx_path
- mandatory subprocess timeout (registry also wraps via asyncio.wait_for)
- non-zero rc raised as ToolDispatchFailed with stderr-derived message
"""
from __future__ import annotations

import asyncio
import json
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from .context import ToolContext
from .exceptions import ToolDispatchFailed
from .registry import ToolRegistry

_TOOL_NAME: Final[str] = "gitnexus_query"
_DESCRIPTION: Final[str] = (
    "Query the GitNexus knowledge graph for execution flows related to a concept. "
    "Returns up to `limit` process-grouped hits with summary and contributing files."
)


class GitnexusQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)


class GitnexusQueryHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process: str
    summary: str
    files: list[str]


class GitnexusQueryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hits: list[GitnexusQueryHit]


async def gitnexus_query_handler(
    inp: GitnexusQueryInput, ctx: ToolContext
) -> GitnexusQueryOutput:
    """Run npx gitnexus query under list-form / shell=False subprocess.

    The registry wraps this call under asyncio.wait_for(timeout=...);
    we still bound the subprocess at the same timeout for defense-in-depth
    so we can reap a stuck child process.
    """
    _argv = [
        *ctx.npx_cmd,
        "gitnexus",
        "query",
        "--repo",
        ctx.repo_name,
        "--limit",
        str(inp.limit),
        inp.query,  # positional <search_query>
    ]
    proc = await asyncio.create_subprocess_exec(
        *_argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=ctx.tool_timeout_seconds
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    if proc.returncode != 0:
        raise ToolDispatchFailed(
            stderr.decode("utf-8", errors="replace").strip()
            or f"gitnexus query exited rc={proc.returncode}"
        )

    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise ToolDispatchFailed(f"malformed gitnexus output: {exc}") from exc

    try:
        return GitnexusQueryOutput.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic validation error
        raise ToolDispatchFailed(f"gitnexus output failed schema: {exc}") from exc


def register_gitnexus_query(registry: ToolRegistry) -> None:
    """Register the ``gitnexus_query`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        GitnexusQueryInput,
        gitnexus_query_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "GitnexusQueryHit",
    "GitnexusQueryInput",
    "GitnexusQueryOutput",
    "gitnexus_query_handler",
    "register_gitnexus_query",
]
