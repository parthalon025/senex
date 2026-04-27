"""Tests for senex.tools.read_file - real-fs path safety + slicing.

Implements M5 Task 5.5 step tests using the tiny_python fixture repo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from senex.secret_redactor import SecretRedactor
from senex.tools.context import ToolContext
from senex.tools.read_file import register_read_file
from senex.tools.registry import ToolError, ToolRegistry, ToolResult


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    src = root / "src"
    src.mkdir()
    main_py = src / "main.py"
    # 30-line file so we can slice line ranges.
    main_py.write_text(
        "\n".join(f"line_{i}" for i in range(1, 31)) + "\n", encoding="utf-8"
    )
    return root.resolve()


@pytest.fixture
def ctx(repo_root: Path) -> ToolContext:
    return ToolContext(
        repo_root=repo_root,
        repo_name="testrepo",
        redactor=SecretRedactor(),
        npx_path=Path("/usr/bin/npx"),
        tool_timeout_seconds=2.0,
        max_result_tokens=2048,
    )


@pytest.fixture
def registry() -> ToolRegistry:
    r = ToolRegistry()
    register_read_file(r)
    return r


@pytest.mark.asyncio
async def test_happy_path_full_file(
    registry: ToolRegistry, ctx: ToolContext, repo_root: Path
) -> None:
    args = json.dumps({"relpath": "src/main.py"})
    result = await registry.dispatch("c", "read_file", args, ctx)
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert parsed["relpath"] == "src/main.py"
    assert "line_1" in parsed["content"]


@pytest.mark.asyncio
async def test_happy_path_line_range(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    args = json.dumps({"relpath": "src/main.py", "line_start": 10, "line_end": 20})
    result = await registry.dispatch("c", "read_file", args, ctx)
    assert isinstance(result, ToolResult)
    parsed = json.loads(result.content)
    assert parsed["line_start"] == 10
    assert parsed["line_end"] == 20
    # 11 lines (10..20 inclusive).
    assert len(parsed["content"].splitlines()) == 11


@pytest.mark.asyncio
async def test_invalid_path_traversal(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    args = json.dumps({"relpath": "../etc/passwd"})
    result = await registry.dispatch("c", "read_file", args, ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "path_rejected"


@pytest.mark.asyncio
async def test_invalid_symlink(
    registry: ToolRegistry, ctx: ToolContext, repo_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("secret\n", encoding="utf-8")
    link = repo_root / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform / privilege")
    args = json.dumps({"relpath": "link.txt"})
    result = await registry.dispatch("c", "read_file", args, ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "path_rejected"


@pytest.mark.asyncio
async def test_invalid_line_range_inverted(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    args = json.dumps({"relpath": "src/main.py", "line_start": 20, "line_end": 10})
    result = await registry.dispatch("c", "read_file", args, ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_invalid_line_range_too_wide(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    args = json.dumps({"relpath": "src/main.py", "line_start": 1, "line_end": 5000})
    result = await registry.dispatch("c", "read_file", args, ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "schema_invalid"


@pytest.mark.asyncio
async def test_file_not_found(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    args = json.dumps({"relpath": "does_not_exist.py"})
    result = await registry.dispatch("c", "read_file", args, ctx)
    assert isinstance(result, ToolError)
    assert result.kind == "dispatch_failed"


@pytest.mark.asyncio
async def test_oversize_file_truncated(
    registry: ToolRegistry, repo_root: Path
) -> None:
    big = repo_root / "big.txt"
    big.write_text("word " * 10_000, encoding="utf-8")
    ctx = ToolContext(
        repo_root=repo_root,
        repo_name="r",
        redactor=SecretRedactor(),
        npx_path=Path("/usr/bin/npx"),
        tool_timeout_seconds=2.0,
        max_result_tokens=100,
    )
    args = json.dumps({"relpath": "big.txt"})
    result = await registry.dispatch("c", "read_file", args, ctx)
    assert isinstance(result, ToolResult)
    assert "truncated:" in result.content
