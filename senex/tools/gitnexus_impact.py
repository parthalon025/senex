"""senex.tools.gitnexus_impact - npx gitnexus impact tool.

Implements spec section 5.11.1 (gitnexus_impact in v1 tool set) and section
SEC-4 (subprocess hardening). Returns blast-radius analysis at d=1, d=2,
d=3 with confidence scores; the model uses this BEFORE proposing any edit.

Risk levels (LOW/MEDIUM/HIGH/CRITICAL) are computed by gitnexus itself; we
forward them verbatim. The spec's d=1 = WILL_BREAK / d=2 = LIKELY /
d=3 = MAY_NEED_TESTING semantics live in the gitnexus binary.

Subprocess invariants enforced here mirror gitnexus_query.py:

- list-form args (no string concatenation, no implicit interpreter)
- npx absolute path pinned at preflight via ToolContext.npx_path
- mandatory subprocess timeout (registry also wraps via asyncio.wait_for)
- input regex-constrained at the pydantic boundary
"""
from __future__ import annotations

import asyncio
import json
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .context import ToolContext
from .exceptions import ToolDispatchFailed
from .registry import ToolRegistry

_TOOL_NAME: Final[str] = "gitnexus_impact"
_DESCRIPTION: Final[str] = (
    "Compute blast radius for a target symbol at depth 1/2/3. Direction "
    "'upstream' returns callers/dependents; 'downstream' returns callees. "
    "Returns risk_level (LOW/MEDIUM/HIGH/CRITICAL) and dependents_by_depth."
)

_SYMBOL_RE: Final[str] = r"^[A-Za-z_][A-Za-z0-9_.]*$"


class GitnexusImpactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1, max_length=200, pattern=_SYMBOL_RE)
    direction: Literal["upstream", "downstream"] = "upstream"
    depth: int = Field(default=1, ge=1, le=3)


class ImpactDependent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    depth: int
    confidence: float = Field(ge=0.0, le=1.0)


class GitnexusImpactOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    # gitnexus emits depth keys as JSON object keys; JSON keys are strings,
    # so we accept dict[str, list[...]] and let the consumer parse the int.
    dependents_by_depth: dict[str, list[ImpactDependent]]


async def gitnexus_impact_handler(
    inp: GitnexusImpactInput, ctx: ToolContext
) -> GitnexusImpactOutput:
    """Run npx gitnexus impact with hardened list-form arguments."""
    _argv = [
        *ctx.npx_cmd,
        "gitnexus",
        "impact",
        "--repo",
        ctx.repo_name,
        "--target",
        inp.target,
        "--direction",
        inp.direction,
        "--depth",
        str(inp.depth),
        "--json",
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
            or f"gitnexus impact exited rc={proc.returncode}"
        )

    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise ToolDispatchFailed(f"malformed gitnexus output: {exc}") from exc

    try:
        return GitnexusImpactOutput.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic validation
        raise ToolDispatchFailed(f"gitnexus output failed schema: {exc}") from exc


def register_gitnexus_impact(registry: ToolRegistry) -> None:
    """Register the ``gitnexus_impact`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        GitnexusImpactInput,
        gitnexus_impact_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "GitnexusImpactInput",
    "GitnexusImpactOutput",
    "ImpactDependent",
    "gitnexus_impact_handler",
    "register_gitnexus_impact",
]
