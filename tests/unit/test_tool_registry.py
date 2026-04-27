"""Tests for senex.tools.registry — register, openai_tools, dispatch.

Implements M5 Task 5.1 step 5.1.6 — strict pipeline (validation, timeout,
redaction, truncation, error mapping).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from senex.secret_redactor import SecretRedactor
from senex.tools.context import ToolContext
from senex.tools.registry import ToolError, ToolRegistry, ToolResult


class _NoopInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        redactor=SecretRedactor(),
        npx_path=Path("/usr/bin/npx"),
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
    )


async def _noop_handler(inp: _NoopInput, ctx: ToolContext) -> str:
    return f"hello {inp.name}"


def test_register_then_openai_tools_returns_schema(registry: ToolRegistry) -> None:
    registry.register(
        "noop",
        _NoopInput,
        _noop_handler,  # type: ignore[arg-type]
        description="trivial probe",
    )
    out = registry.openai_tools(["noop"])
    assert isinstance(out, list)
    assert out[0]["type"] == "function"
    fn = out[0]["function"]
    assert fn["name"] == "noop"
    assert fn["description"] == "trivial probe"
    assert "parameters" in fn  # JSON schema


def test_openai_tools_unknown_name_raises(registry: ToolRegistry) -> None:
    with pytest.raises(KeyError):
        registry.openai_tools(["does_not_exist"])


def test_openai_tools_orders_by_enabled_names(registry: ToolRegistry) -> None:
    registry.register("a", _NoopInput, _noop_handler, description="a")  # type: ignore[arg-type]
    registry.register("b", _NoopInput, _noop_handler, description="b")  # type: ignore[arg-type]
    out = registry.openai_tools(["b", "a"])
    assert [t["function"]["name"] for t in out] == ["b", "a"]


@pytest.mark.asyncio
async def test_dispatch_validates_input(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    registry.register("noop", _NoopInput, _noop_handler, description="x")  # type: ignore[arg-type]
    # Missing required key -> schema_invalid.
    result = await registry.dispatch("call-1", "noop", '{"wrong": "field"}', ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_dispatch_runs_handler_and_redacts(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    async def handler(inp: _NoopInput, ctx: ToolContext) -> str:
        return f"key={inp.name}: AKIAIOSFODNN7EXAMPLE"

    registry.register("noop", _NoopInput, handler, description="x")  # type: ignore[arg-type]
    result = await registry.dispatch("call-2", "noop", '{"name": "x"}', ctx)
    assert isinstance(result, ToolResult)
    assert "[REDACTED:aws_access_key]" in result.content
    assert "AKIAIOSFODNN7EXAMPLE" not in result.content


@pytest.mark.asyncio
async def test_dispatch_truncates_oversize_output(
    tmp_path: Path,
) -> None:
    reg = ToolRegistry()

    async def handler(inp: _NoopInput, ctx: ToolContext) -> str:
        return "word " * 10_000  # ~10000 tokens

    ctx = ToolContext(
        repo_root=tmp_path.resolve(),
        repo_name="r",
        redactor=SecretRedactor(),
        npx_path=Path("/usr/bin/npx"),
        tool_timeout_seconds=2.0,
        max_result_tokens=100,
    )
    reg.register("noop", _NoopInput, handler, description="x")  # type: ignore[arg-type]
    result = await reg.dispatch("c", "noop", '{"name": "x"}', ctx)
    assert isinstance(result, ToolResult)
    assert "truncated:" in result.content
    # Token count of the truncated content ≤ max_result_tokens + marker (allow some slack).
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    assert len(enc.encode(result.content)) <= 200  # 100 cap + marker


@pytest.mark.asyncio
async def test_dispatch_handler_timeout(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    async def slow_handler(inp: _NoopInput, ctx: ToolContext) -> str:
        await asyncio.sleep(5.0)
        return "never"

    registry.register("slow", _NoopInput, slow_handler, description="x")  # type: ignore[arg-type]
    ctx = ToolContext(
        repo_root=tmp_path.resolve(),
        repo_name="r",
        redactor=SecretRedactor(),
        npx_path=Path("/usr/bin/npx"),
        tool_timeout_seconds=0.1,
        max_result_tokens=2048,
    )
    result = await registry.dispatch("c", "slow", '{"name": "x"}', ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "timeout"


@pytest.mark.asyncio
async def test_dispatch_handler_exception(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    async def raises(inp: _NoopInput, ctx: ToolContext) -> str:
        raise RuntimeError("boom AKIAIOSFODNN7EXAMPLE oops")

    registry.register("bad", _NoopInput, raises, description="x")  # type: ignore[arg-type]
    result = await registry.dispatch("c", "bad", '{"name": "x"}', ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"
    # Redaction must apply to the message too.
    assert "[REDACTED:aws_access_key]" in result.message
    assert "AKIAIOSFODNN7EXAMPLE" not in result.message


@pytest.mark.asyncio
async def test_dispatch_unknown_tool(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    result = await registry.dispatch("c", "nope", "{}", ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "unknown_tool"


@pytest.mark.asyncio
async def test_dispatch_bad_json(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    registry.register("noop", _NoopInput, _noop_handler, description="x")  # type: ignore[arg-type]
    result = await registry.dispatch("c", "noop", '{not valid json', ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"
