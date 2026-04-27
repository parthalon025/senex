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


# ---------------------------------------------------------------------------
# UICfg / [ui] section (M11 — scan-disk-for-repos launcher button)
# ---------------------------------------------------------------------------


def test_ui_cfg_default_values_are_none_and_six() -> None:
    """``UICfg`` defaults must match the spec: scan_root=None, scan_max_depth=6."""
    from senex.config import UICfg
    cfg = UICfg()
    assert cfg.scan_root is None
    assert cfg.scan_max_depth == 6


def test_senex_config_exposes_ui_section_with_defaults() -> None:
    """``SenexConfig.ui`` must be present and default to a fresh ``UICfg``."""
    from senex.config import SenexConfig, UICfg
    cfg = SenexConfig()
    assert isinstance(cfg.ui, UICfg)
    assert cfg.ui.scan_root is None
    assert cfg.ui.scan_max_depth == 6


def test_ui_section_parses_from_toml(tmp_path: Path) -> None:
    """A ``[ui]`` block in TOML must populate ``SenexConfig.ui`` correctly."""
    f = tmp_path / "ui.toml"
    f.write_text(
        '[ui]\nscan_root = "E:/code"\nscan_max_depth = 4\n',
        encoding="utf-8",
    )
    cfg = load_config(f)
    assert cfg.ui.scan_root == "E:/code"
    assert cfg.ui.scan_max_depth == 4


def test_ui_section_rejects_unknown_key_with_extra_forbid(tmp_path: Path) -> None:
    """Per conventions §10, ``UICfg`` must use ``extra='forbid'``."""
    bad = tmp_path / "bad_ui.toml"
    bad.write_text(
        "[ui]\nbogus_key = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(UnknownConfigKey):
        load_config(bad)


def test_ui_scan_max_depth_must_be_positive(tmp_path: Path) -> None:
    """``scan_max_depth`` is a depth bound; must be > 0."""
    bad = tmp_path / "bad_ui.toml"
    bad.write_text("[ui]\nscan_max_depth = 0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(bad)
