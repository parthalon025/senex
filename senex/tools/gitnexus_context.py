"""senex.tools.gitnexus_context - npx gitnexus context tool (the TOOL).

Distinct from senex.graph_awareness.GitNexusCLIProvider (the pre-flight
context BUILDER from M2); this module is the model-callable tool that
returns a 360-degree view of a single symbol on demand. Implements spec
section 5.11.1 (gitnexus_context in v1 tool set) and section SEC-4
(subprocess hardening).

Subprocess invariants enforced here:

- list-form args (never string-concatenated)
- npx absolute path pinned at preflight via ToolContext.npx_path
- mandatory subprocess timeout (registry also wraps via asyncio.wait_for)
- input symbol regex-constrained at the pydantic boundary
- input file regex-constrained AND traversal-rejected at the pydantic boundary
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .context import ToolContext
from .exceptions import ToolDispatchFailed
from .registry import ToolRegistry

_TOOL_NAME: Final[str] = "gitnexus_context"
_DESCRIPTION: Final[str] = (
    "Return the 360-degree view of a symbol: direct callers/callees, the "
    "execution flows it participates in, and its functional cluster (if any)."
)

# section SEC-4 path-like input regex (mirrors GitNexusCLIProvider.RELPATH_RE).
_FILE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_./\\-]+$")
# Symbol identifier: ASCII identifier with dotted member access.
_SYMBOL_RE: Final[str] = r"^[A-Za-z_][A-Za-z0-9_.]*$"


class GitnexusContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=200, pattern=_SYMBOL_RE)
    file: str | None = Field(default=None, max_length=500)

    @field_validator("file")
    @classmethod
    def validate_file_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _FILE_RE.fullmatch(v):
            raise ValueError(
                "file must match ^[A-Za-z0-9_./\\\\-]+$"
            )
        # Refuse traversal even though the regex allows the bytes.
        if ".." in v:
            raise ValueError("file must not contain '..' (traversal)")
        return v


class GitnexusContextOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callers_d1: list[str]
    callees_d1: list[str]
    processes: list[str]
    cluster: str | None


async def gitnexus_context_handler(
    inp: GitnexusContextInput, ctx: ToolContext
) -> GitnexusContextOutput:
    """Run npx gitnexus context with hardened list-form arguments."""
    args = [
        str(ctx.npx_path),
        "gitnexus",
        "context",
        "--repo",
        ctx.repo_name,
        "--name",
        inp.symbol,
    ]
    if inp.file is not None:
        args.extend(["--file", inp.file])
    args.append("--json")

    proc = await asyncio.create_subprocess_exec(
        *args,
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
            or f"gitnexus context exited rc={proc.returncode}"
        )

    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise ToolDispatchFailed(f"malformed gitnexus output: {exc}") from exc

    try:
        return GitnexusContextOutput.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic validation
        raise ToolDispatchFailed(f"gitnexus output failed schema: {exc}") from exc


def register_gitnexus_context(registry: ToolRegistry) -> None:
    """Register the ``gitnexus_context`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        GitnexusContextInput,
        gitnexus_context_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "GitnexusContextInput",
    "GitnexusContextOutput",
    "gitnexus_context_handler",
    "register_gitnexus_context",
]
