"""Tests for Lens.openai_tools_for - intersection-only enforcement.

Implements M5 Task 5.9. Spec section 6.1 says config can SUBSET but cannot
EXTEND lens-declared tools. Names in config NOT in the lens cause Exit 2
with a warning logged for EVERY violation before raising.
"""
from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from senex.config import UnknownConfigKey
from senex.lens import Lens
from senex.tools.registry import ToolRegistry


@pytest.fixture
def lens() -> Lens:
    """Build a fake Lens with three tools without going through filesystem load."""
    return Lens(
        name="testlens",
        version="1.0.0",
        description="test",
        system_prompt_path=__import__("pathlib").Path("/nonexistent/system.md"),
        response_schema_path=__import__("pathlib").Path("/nonexistent/schema.json"),
        crosscut_prompt_path=__import__("pathlib").Path("/nonexistent/cc.md"),
        crosscut_schema_path=__import__("pathlib").Path("/nonexistent/cc.json"),
        renderer_template_path=__import__("pathlib").Path("/nonexistent/render.md"),
        category_taxonomy=("safety",),
        tools=["A", "B", "C"],
        fingerprint="x",
    )


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


def test_config_none_returns_lens_tools_unchanged(
    lens: Lens, registry: ToolRegistry
) -> None:
    out = lens.openai_tools_for(registry, None)
    assert out == ["A", "B", "C"]


def test_config_subset_returns_intersection_in_lens_order(
    lens: Lens, registry: ToolRegistry
) -> None:
    out = lens.openai_tools_for(registry, ["B", "A"])
    # Order is from the LENS, not the config.
    assert out == ["A", "B"]


def test_config_with_extension_raises_with_warnings(
    lens: Lens,
    registry: ToolRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(UnknownConfigKey):
        lens.openai_tools_for(registry, ["A", "X", "Y"])
    # Exactly two warnings for X and Y; the violations should ALL be logged.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    msgs = " ".join(r.getMessage() for r in warnings)
    assert "X" in msgs
    assert "Y" in msgs


def test_config_empty_list_returns_empty(
    lens: Lens, registry: ToolRegistry
) -> None:
    # Empty subset is valid -> tools disabled entirely.
    out = lens.openai_tools_for(registry, [])
    assert out == []


def test_intersection_preserves_lens_order_not_config_order(
    lens: Lens, registry: ToolRegistry
) -> None:
    out = lens.openai_tools_for(registry, ["C", "A", "B"])
    assert out == ["A", "B", "C"]


def test_dataclass_replace_compatibility(lens: Lens) -> None:
    """Sanity: Lens is frozen but supports dataclass.replace for tests."""
    lens2 = replace(lens, tools=["A"])
    assert lens2.tools == ["A"]
