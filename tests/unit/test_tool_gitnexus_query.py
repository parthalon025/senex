"""Tests for senex.tools.gitnexus_query — subprocess-mocked.

Implements M5 Task 5.2 step tests. Mocks ``asyncio.create_subprocess_exec``
to avoid requiring an actual ``npx gitnexus`` binary.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from senex.secret_redactor import SecretRedactor
from senex.tools.context import ToolContext
from senex.tools.gitnexus_query import register_gitnexus_query
from senex.tools.registry import ToolError, ToolRegistry, ToolResult


def _ctx(tmp_path: Path, *, timeout: float = 2.0, max_tokens: int = 2048) -> ToolContext:
    return ToolContext(
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        redactor=SecretRedactor(),
        npx_path=Path("/usr/bin/npx"),
        tool_timeout_seconds=timeout,
        max_result_tokens=max_tokens,
    )


def _mock_subprocess(stdout: bytes, stderr: bytes = b"", rc: int = 0) -> AsyncMock:
    """Build an AsyncMock that mimics asyncio.create_subprocess_exec result."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = rc
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()

    async def _factory(*args: object, **kwargs: object) -> AsyncMock:
        return proc

    return AsyncMock(side_effect=_factory)


@pytest.fixture
def registry() -> ToolRegistry:
    r = ToolRegistry()
    register_gitnexus_query(r)
    return r


@pytest.mark.asyncio
async def test_happy_path_returns_hits(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    payload = {
        "hits": [
            {"process": "auth_login", "summary": "validate user", "files": ["a.py"]},
        ],
    }
    sub = _mock_subprocess(json.dumps(payload).encode("utf-8"))
    with patch("senex.tools.gitnexus_query.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c", "gitnexus_query", '{"query": "auth login"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert parsed["hits"][0]["process"] == "auth_login"


@pytest.mark.asyncio
async def test_invalid_input_query_too_long(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    args = json.dumps({"query": "x" * 501})
    result = await registry.dispatch("c", "gitnexus_query", args, _ctx(tmp_path))
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_invalid_input_limit_too_high(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    args = json.dumps({"query": "auth", "limit": 10})
    result = await registry.dispatch("c", "gitnexus_query", args, _ctx(tmp_path))
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_subprocess_failure_nonzero_exit(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    sub = _mock_subprocess(b"", stderr=b"graph not indexed", rc=1)
    with patch("senex.tools.gitnexus_query.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c", "gitnexus_query", '{"query": "x"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"
    assert "graph not indexed" in result.message


@pytest.mark.asyncio
async def test_subprocess_timeout(registry: ToolRegistry, tmp_path: Path) -> None:
    async def hang(*_a: object, **_kw: object) -> None:
        await asyncio.sleep(5.0)

    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=hang)
    proc.returncode = 0
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()

    async def _factory(*args: object, **kwargs: object) -> AsyncMock:
        return proc

    with patch(
        "senex.tools.gitnexus_query.asyncio.create_subprocess_exec",
        AsyncMock(side_effect=_factory),
    ):
        result = await registry.dispatch(
            "c", "gitnexus_query", '{"query": "x"}', _ctx(tmp_path, timeout=0.1)
        )
    assert isinstance(result, ToolError)
    assert result.kind == "timeout"


@pytest.mark.asyncio
async def test_empty_results_returns_empty_hits(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    sub = _mock_subprocess(b'{"hits": []}')
    with patch("senex.tools.gitnexus_query.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c", "gitnexus_query", '{"query": "x"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert parsed["hits"] == []


@pytest.mark.asyncio
async def test_oversize_result_truncated(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    big_payload = {
        "hits": [
            {
                "process": f"p{i}",
                "summary": "x " * 1000,
                "files": [f"f{i}.py"],
            }
            for i in range(50)
        ]
    }
    sub = _mock_subprocess(json.dumps(big_payload).encode("utf-8"))
    with patch("senex.tools.gitnexus_query.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c",
            "gitnexus_query",
            '{"query": "x"}',
            _ctx(tmp_path, max_tokens=100),
        )
    assert isinstance(result, ToolResult)
    assert "truncated:" in result.content
