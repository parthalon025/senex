"""Test that ``senex.config.toml.example`` is in sync with ``SenexConfig`` (M10 Task 10.7).

Two assertions:

  1. The example file MUST parse as a valid SenexConfig (load_config succeeds).
  2. Every top-level pydantic field on SenexConfig MUST appear in the example
     (active key OR documenting comment), so a new field can never silently
     diverge from the example.

This test is the contract that the README quickstart remains runnable:
the user copies the example, edits a few fields, and ``senex doctor`` works.
"""
from __future__ import annotations

from pathlib import Path

from senex.config import (
    LensCfg,
    InferenceCfg,
    OutputCfg,
    SenexConfig,
    WalkerCfg,
    load_config,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "senex.config.toml.example"


def test_example_file_parses_as_senex_config() -> None:
    """The example must round-trip through ``load_config`` cleanly."""
    cfg = load_config(_EXAMPLE)
    assert isinstance(cfg, SenexConfig)


def test_example_file_includes_every_top_level_section() -> None:
    """Every top-level field on SenexConfig appears in the example."""
    body = _EXAMPLE.read_text(encoding="utf-8")
    expected_sections = list(SenexConfig.model_fields.keys())
    missing: list[str] = []
    for name in expected_sections:
        # Match either ``[name]`` (a TOML table) or ``[[name]]`` (an array)
        # or ``name = ...`` (a top-level scalar field).
        candidates = (f"[{name}]", f"[[{name}]]", f"{name} =")
        if not any(c in body for c in candidates):
            missing.append(name)
    assert not missing, (
        f"senex.config.toml.example is missing sections for: {missing} "
        f"(expected one of [name], [[name]], or `name = ...`)"
    )


def test_example_inference_subsections_present() -> None:
    """Critical [inference.*] subsections that users tune are listed."""
    body = _EXAMPLE.read_text(encoding="utf-8")
    for sub in (
        "[inference]",
        "[inference.sampling]",
        "[inference.thinking]",
        "[inference.tools]",
        "[inference.compaction]",
        "[inference.lifecycle]",
    ):
        assert sub in body, f"missing subsection: {sub}"


def test_example_walker_section_includes_default_extensions_keyword() -> None:
    """The walker section documents the file-extension list."""
    body = _EXAMPLE.read_text(encoding="utf-8")
    assert "extensions" in body


def test_example_repos_section_documented() -> None:
    """The example shows how to declare a [[repos]] entry."""
    body = _EXAMPLE.read_text(encoding="utf-8")
    assert "[[repos]]" in body
    assert "name" in body
    assert "path" in body


# Touch the imports so the linter doesn't drop them; they're here to make
# the SenexConfig surface explicit at the top of the file.
_ = (LensCfg, InferenceCfg, OutputCfg, WalkerCfg)
