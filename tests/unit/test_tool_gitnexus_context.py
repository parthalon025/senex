"""Tests for senex.tools.gitnexus_context — subprocess-mocked.

Implements M5 Task 5.3 step tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from senex.secret_redactor import SecretRedactor
from senex.tools.context import ToolContext
from senex.tools.gitnexus_context import register_gitnexus_context
from senex.tools.registry import ToolError, ToolRegistry, ToolResult


def _ctx(tmp_path: Path, *, max_tokens: int = 2048) -> ToolContext:
    return ToolContext(
        repo_root=tmp_path.resolve(),
        repo_name="testrepo",
        redactor=SecretRedactor(),
        npx_path=Path("/usr/bin/npx"),
        tool_timeout_seconds=2.0,
        max_result_tokens=max_tokens,
    )


def _mock_subprocess(
    stdout: bytes, stderr: bytes = b"", rc: int = 0
) -> tuple[AsyncMock, AsyncMock]:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = rc
    proc.kill = AsyncMock()
    proc.wait = AsyncMock()

    captured: dict[str, object] = {}

    async def _factory(*args: object, **kwargs: object) -> AsyncMock:
        captured["args"] = args
        return proc

    factory = AsyncMock(side_effect=_factory)
    factory.captured = captured  # type: ignore[attr-defined]
    return factory, proc


@pytest.fixture
def registry() -> ToolRegistry:
    r = ToolRegistry()
    register_gitnexus_context(r)
    return r


@pytest.mark.asyncio
async def test_happy_path_returns_360_view(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    payload = {
        "callers_d1": ["a", "b"],
        "callees_d1": ["c"],
        "processes": ["login"],
        "cluster": "auth",
    }
    factory, _ = _mock_subprocess(json.dumps(payload).encode("utf-8"))
    with patch(
        "senex.tools.gitnexus_context.asyncio.create_subprocess_exec", factory
    ):
        result = await registry.dispatch(
            "c", "gitnexus_context", '{"symbol": "validateUser"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert parsed["callers_d1"] == ["a", "b"]
    assert parsed["cluster"] == "auth"


@pytest.mark.asyncio
async def test_invalid_symbol_with_special_chars(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    args = json.dumps({"symbol": "foo;rm -rf /"})
    result = await registry.dispatch("c", "gitnexus_context", args, _ctx(tmp_path))
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_invalid_file_with_dotdot(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    args = json.dumps({"symbol": "validateUser", "file": "../etc/passwd"})
    result = await registry.dispatch("c", "gitnexus_context", args, _ctx(tmp_path))
    assert isinstance(result, ToolError)
    # `..` contains `/` and `.` which are allowed by the regex on its own,
    # but our validator must additionally refuse traversal — making this a
    # schema_invalid result.
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_subprocess_symbol_not_found(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    factory, _ = _mock_subprocess(b"", stderr=b"no symbol named X", rc=2)
    with patch(
        "senex.tools.gitnexus_context.asyncio.create_subprocess_exec", factory
    ):
        result = await registry.dispatch(
            "c", "gitnexus_context", '{"symbol": "X"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"
    assert "no symbol" in result.message


@pytest.mark.asyncio
async def test_optional_file_omitted(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    payload = {
        "callers_d1": [],
        "callees_d1": [],
        "processes": [],
        "cluster": None,
    }
    factory, _ = _mock_subprocess(json.dumps(payload).encode("utf-8"))
    with patch(
        "senex.tools.gitnexus_context.asyncio.create_subprocess_exec", factory
    ):
        result = await registry.dispatch(
            "c", "gitnexus_context", '{"symbol": "validateUser"}', _ctx(tmp_path)
        )
    assert isinstance(result, ToolResult)
    # Verify the `--file` flag was NOT passed.
    args = factory.captured["args"]  # type: ignore[attr-defined]
    assert "--file" not in args


@pytest.mark.asyncio
async def test_oversize_result_truncated(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    payload = {
        "callers_d1": [f"caller_{i}" for i in range(2000)],
        "callees_d1": [],
        "processes": [],
        "cluster": "x",
    }
    factory, _ = _mock_subprocess(json.dumps(payload).encode("utf-8"))
    with patch(
        "senex.tools.gitnexus_context.asyncio.create_subprocess_exec", factory
    ):
        result = await registry.dispatch(
            "c",
            "gitnexus_context",
            '{"symbol": "validateUser"}',
            _ctx(tmp_path, max_tokens=100),
        )
    assert isinstance(result, ToolResult)
    assert "truncated:" in result.content
