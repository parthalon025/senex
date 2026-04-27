"""Tests for senex.tools.search_code - HTTP MCP tool with respx mocks.

Implements M5 Task 5.7 step tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from senex.secret_redactor import SecretRedactor
from senex.tools.context import ToolContext
from senex.tools.registry import ToolError, ToolRegistry, ToolResult
from senex.tools.search_code import register_search_code

_BASE_URL = "http://localhost:8765"


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


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> ToolRegistry:
    monkeypatch.setenv("CLAUDE_CONTEXT_URL", _BASE_URL)
    r = ToolRegistry()
    register_search_code(r)
    return r


@pytest.mark.asyncio
async def test_happy_path_returns_hits(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    payload = {
        "hits": [
            {"file": "a.py", "score": 0.9, "snippet": "def foo()"},
            {"file": "b.py", "score": 0.8, "snippet": "def bar()"},
            {"file": "c.py", "score": 0.7, "snippet": "def baz()"},
        ]
    }
    with respx.mock(base_url=_BASE_URL) as router:
        router.post("/search_code").mock(return_value=httpx.Response(200, json=payload))
        result = await registry.dispatch(
            "c", "search_code", '{"query": "auth login", "limit": 3}', ctx
        )
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert len(parsed["hits"]) == 3
    assert parsed["hits"][0]["file"] == "a.py"


@pytest.mark.asyncio
async def test_invalid_query_too_long(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    args = json.dumps({"query": "x" * 501})
    result = await registry.dispatch("c", "search_code", args, ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_invalid_limit_zero(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    args = json.dumps({"query": "x", "limit": 0})
    result = await registry.dispatch("c", "search_code", args, ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_mcp_unreachable_returns_unavailable(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    with respx.mock(base_url=_BASE_URL) as router:
        router.post("/search_code").mock(
            side_effect=httpx.ConnectError("MCP not reachable")
        )
        result = await registry.dispatch(
            "c", "search_code", '{"query": "x"}', ctx
        )
    assert isinstance(result, ToolError)
    assert result.kind == "unavailable"


@pytest.mark.asyncio
async def test_mcp_5xx_returns_dispatch_failed(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    with respx.mock(base_url=_BASE_URL) as router:
        router.post("/search_code").mock(return_value=httpx.Response(503))
        result = await registry.dispatch(
            "c", "search_code", '{"query": "x"}', ctx
        )
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"


@pytest.mark.asyncio
async def test_malformed_response_returns_dispatch_failed(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    with respx.mock(base_url=_BASE_URL) as router:
        router.post("/search_code").mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        result = await registry.dispatch(
            "c", "search_code", '{"query": "x"}', ctx
        )
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"
    assert "malformed" in result.message.lower()


@pytest.mark.asyncio
async def test_empty_results_returns_empty_hits(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    with respx.mock(base_url=_BASE_URL) as router:
        router.post("/search_code").mock(
            return_value=httpx.Response(200, json={"hits": []})
        )
        result = await registry.dispatch(
            "c", "search_code", '{"query": "x"}', ctx
        )
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert parsed["hits"] == []
