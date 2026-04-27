"""Tests for senex.tools.gitnexus_impact - subprocess-mocked.

Implements M5 Task 5.4 step tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from senex.secret_redactor import SecretRedactor
from senex.tools.context import ToolContext
from senex.tools.gitnexus_impact import register_gitnexus_impact
from senex.tools.registry import ToolError, ToolRegistry, ToolResult


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        redactor=SecretRedactor(),
        npx_path=Path("/usr/bin/npx"),
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
    )


def _mock_subprocess(stdout: bytes, stderr: bytes = b"", rc: int = 0) -> AsyncMock:
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
    register_gitnexus_impact(r)
    return r


@pytest.mark.asyncio
async def test_happy_path_upstream_d1(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    payload = {
        "risk_level": "HIGH",
        "dependents_by_depth": {
            "1": [{"name": "a", "depth": 1, "confidence": 0.9}],
        },
    }
    sub = _mock_subprocess(json.dumps(payload).encode("utf-8"))
    with patch("senex.tools.gitnexus_impact.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c", "gitnexus_impact", '{"target": "validateUser"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert parsed["risk_level"] == "HIGH"
    assert parsed["dependents_by_depth"]["1"][0]["name"] == "a"


@pytest.mark.asyncio
async def test_invalid_direction_value(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    args = json.dumps({"target": "x", "direction": "sideways"})
    result = await registry.dispatch("c", "gitnexus_impact", args, _ctx(tmp_path))
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_invalid_depth_too_high(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    args = json.dumps({"target": "x", "depth": 4})
    result = await registry.dispatch("c", "gitnexus_impact", args, _ctx(tmp_path))
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_invalid_target_special_chars(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    args = json.dumps({"target": "foo'bar"})
    result = await registry.dispatch("c", "gitnexus_impact", args, _ctx(tmp_path))
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_subprocess_failure(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    sub = _mock_subprocess(b"", stderr=b"target not in graph", rc=1)
    with patch("senex.tools.gitnexus_impact.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c", "gitnexus_impact", '{"target": "ghost"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"
    assert "target not in graph" in result.message


@pytest.mark.asyncio
async def test_malformed_json_output(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    """Unparseable subprocess output -> dispatch_failed."""
    sub = _mock_subprocess(b"not json")
    with patch("senex.tools.gitnexus_impact.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c", "gitnexus_impact", '{"target": "x"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"
    assert "malformed" in result.message.lower()


@pytest.mark.asyncio
async def test_output_failing_schema(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    """Output with invalid risk_level Literal -> dispatch_failed."""
    sub = _mock_subprocess(
        b'{"risk_level": "EXTREME", "dependents_by_depth": {}}'
    )
    with patch("senex.tools.gitnexus_impact.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c", "gitnexus_impact", '{"target": "x"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"


@pytest.mark.asyncio
async def test_empty_dependents_d2_d3(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    payload = {
        "risk_level": "LOW",
        "dependents_by_depth": {
            "1": [{"name": "a", "depth": 1, "confidence": 1.0}],
            # No d=2 or d=3 keys at all.
        },
    }
    sub = _mock_subprocess(json.dumps(payload).encode("utf-8"))
    with patch("senex.tools.gitnexus_impact.asyncio.create_subprocess_exec", sub):
        result = await registry.dispatch(
            "c", "gitnexus_impact", '{"target": "x"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert parsed["risk_level"] == "LOW"
    assert "1" in parsed["dependents_by_depth"]
