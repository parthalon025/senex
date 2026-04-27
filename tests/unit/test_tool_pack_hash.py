"""Tests for senex.tools.pack_hash - sha256 of tool name + input schemas.

Implements M5 Task 5.10. compute_tool_pack_hash is consumed by
Checkpoint, RunStart event, and findings.json metadata to detect
resume-incompatible tool drift.
"""
from __future__ import annotations

import re

import pytest
from pydantic import BaseModel, ConfigDict, Field

from senex.tools.context import ToolContext
from senex.tools.pack_hash import compute_tool_pack_hash
from senex.tools.registry import ToolRegistry


class _InputV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=100)


class _InputV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Different cap -> different schema -> different hash.
    query: str = Field(min_length=1, max_length=200)


async def _noop_handler(inp: BaseModel, ctx: ToolContext) -> str:
    return "x"


@pytest.fixture
def registry_v1() -> ToolRegistry:
    r = ToolRegistry()
    r.register("A", _InputV1, _noop_handler, description="a")  # type: ignore[arg-type]
    r.register("B", _InputV1, _noop_handler, description="b")  # type: ignore[arg-type]
    r.register("C", _InputV1, _noop_handler, description="c")  # type: ignore[arg-type]
    return r


def test_hash_stable_across_calls(registry_v1: ToolRegistry) -> None:
    h1 = compute_tool_pack_hash(["A", "B"], registry_v1)
    h2 = compute_tool_pack_hash(["A", "B"], registry_v1)
    assert h1 == h2


def test_hash_independent_of_input_order(registry_v1: ToolRegistry) -> None:
    h_abc = compute_tool_pack_hash(["A", "B", "C"], registry_v1)
    h_cba = compute_tool_pack_hash(["C", "B", "A"], registry_v1)
    assert h_abc == h_cba


def test_hash_changes_when_tool_added(registry_v1: ToolRegistry) -> None:
    h_ab = compute_tool_pack_hash(["A", "B"], registry_v1)
    h_abc = compute_tool_pack_hash(["A", "B", "C"], registry_v1)
    assert h_ab != h_abc


def test_hash_changes_when_tool_removed(registry_v1: ToolRegistry) -> None:
    h_abc = compute_tool_pack_hash(["A", "B", "C"], registry_v1)
    h_ab = compute_tool_pack_hash(["A", "B"], registry_v1)
    assert h_abc != h_ab


def test_hash_changes_when_input_schema_changes(registry_v1: ToolRegistry) -> None:
    h_v1 = compute_tool_pack_hash(["A"], registry_v1)
    # Re-register A with the v2 schema (different max_length).
    registry_v1.register("A", _InputV2, _noop_handler, description="a")  # type: ignore[arg-type]
    h_v2 = compute_tool_pack_hash(["A"], registry_v1)
    assert h_v1 != h_v2


def test_hash_is_64_hex_chars(registry_v1: ToolRegistry) -> None:
    h = compute_tool_pack_hash(["A"], registry_v1)
    assert re.fullmatch(r"^[0-9a-f]{64}$", h) is not None
