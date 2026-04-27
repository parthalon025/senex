"""Tests for senex.config — TOML loader + strict pydantic validation + deep-merge."""
from __future__ import annotations

from pathlib import Path

import pytest

from senex.config import (
    SenexConfig,
    UnknownConfigKey,
    load_config,
    resolve_config,
)

EXAMPLE = Path("senex.config.toml.example")


def test_load_config_example_returns_senexconfig() -> None:
    cfg = load_config(EXAMPLE)
    assert isinstance(cfg, SenexConfig)
    assert cfg.lens.name == "correctness"
    assert cfg.lmstudio.model == "google/gemma-4-26b-a4b"


def test_load_config_unknown_key_raises_with_hint(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[lens]\nnaem = 'correctness'\n", encoding="utf-8")
    with pytest.raises(UnknownConfigKey) as exc:
        load_config(bad)
    msg = str(exc.value)
    assert "naem" in msg
    assert "name" in msg  # Levenshtein hint suggests the closest valid key.


def test_load_config_temperature_out_of_range_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text(
        "[lmstudio.sampling]\ntemperature = 3.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(bad)


def test_load_config_seed_random_default_true_when_omitted(tmp_path: Path) -> None:
    f = tmp_path / "ok.toml"
    f.write_text("", encoding="utf-8")
    cfg = load_config(f)
    assert cfg.lmstudio.sampling.seed_random is True


def test_load_config_extra_top_level_key_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("foobar = 1\n", encoding="utf-8")
    with pytest.raises(UnknownConfigKey):
        load_config(bad)


def test_resolve_config_per_repo_overrides_shadow_global() -> None:
    base = load_config(EXAMPLE)
    repo_cfg = resolve_config(
        base,
        repo_path=Path("E:/pensiv"),
        cli_overrides={},
        tui_overrides={},
    )
    # The example puts temperature=0.5 in the pensiv repo override.
    assert repo_cfg.lmstudio.tasks.file_audit.temperature == 0.5


def test_resolve_config_cli_overrides_shadow_repo() -> None:
    base = load_config(EXAMPLE)
    cli = {"lmstudio": {"sampling": {"temperature": 0.9}}}
    out = resolve_config(base, repo_path=None, cli_overrides=cli, tui_overrides={})
    assert out.lmstudio.sampling.temperature == 0.9


def test_resolve_config_tui_overrides_shadow_cli() -> None:
    base = load_config(EXAMPLE)
    cli = {"lmstudio": {"sampling": {"temperature": 0.9}}}
    tui = {"lmstudio": {"sampling": {"temperature": 0.7}}}
    out = resolve_config(base, repo_path=None, cli_overrides=cli, tui_overrides=tui)
    assert out.lmstudio.sampling.temperature == 0.7


def test_resolve_config_explicit_empty_replaces_inherited() -> None:
    # Per §6.1: null/""/[] REPLACE; only omission inherits.
    base = load_config(EXAMPLE)
    cli = {"walker": {"extensions": []}}
    out = resolve_config(base, repo_path=None, cli_overrides=cli, tui_overrides={})
    assert out.walker.extensions == []


def test_config_module_has_nonempty_docstring() -> None:
    from senex import config as config_mod
    assert config_mod.__doc__ is not None and config_mod.__doc__.strip() != ""


def test_walker_cfg_defaults_match_spec_section_6() -> None:
    # Per R8: WalkerCfg defaults are part of the spec §6 contract.
    from senex.config import WalkerCfg
    cfg = WalkerCfg()
    assert cfg.max_size_bytes == 524_288
    assert cfg.respect_gitignore is True
    assert cfg.include_tests is False
    assert cfg.extensions == [
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
        ".java", ".kt", ".cs", ".cpp", ".c", ".h", ".hpp",
        ".swift", ".rb", ".php", ".sh", ".ps1", ".scala",
        ".ex", ".exs", ".dart", ".lua", ".zig", ".nim",
    ]
    assert cfg.default_excludes == [
        "node_modules", ".venv", "venv", "dist", "build",
        "__pycache__", "*.min.*", ".git", "vendor",
    ]
