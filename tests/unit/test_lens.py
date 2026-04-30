"""Tests for senex.lens — Lens dataclass + Lens.load(name)."""
from __future__ import annotations

from pathlib import Path

import pytest

from senex.lens import Lens, LensNotFound, LensValidationError


def test_lens_load_correctness_returns_lens_with_nine_tools() -> None:
    lens = Lens.load("correctness")
    assert lens.name == "correctness"
    assert lens.version == "1.0.0"
    assert lens.tools == [
        "gitnexus_query", "gitnexus_context", "gitnexus_impact",
        "read_file", "grep", "search_code",
        "list_dir", "list_symbols", "run_semgrep",
    ]


def test_lens_load_resolves_prompt_paths() -> None:
    lens = Lens.load("correctness")
    assert lens.system_prompt_path.name == "system_senior_dev.md"
    assert lens.system_prompt_path.exists()
    assert lens.crosscut_prompt_path.exists()
    assert lens.renderer_template_path.exists()


def test_lens_load_computes_stable_fingerprint() -> None:
    a = Lens.load("correctness")
    b = Lens.load("correctness")
    assert a.fingerprint == b.fingerprint
    # Sanity: must look like a sha256 hex digest.
    assert len(a.fingerprint) == 64


def test_lens_load_unknown_name_raises_lensnotfound() -> None:
    with pytest.raises(LensNotFound):
        Lens.load("does_not_exist")


def test_lens_load_invalid_lens_toml_raises_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = tmp_path / "bad" / "lens.toml"
    bad.parent.mkdir()
    bad.write_text("schema_version = 99\n", encoding="utf-8")
    (tmp_path / "bad" / "tools.toml").write_text("enabled_tools = []\n", encoding="utf-8")

    from senex import lens as lens_mod
    monkeypatch.setattr(lens_mod, "_lens_root", lambda: tmp_path)
    with pytest.raises(LensValidationError):
        Lens.load("bad")


def test_lens_module_has_nonempty_docstring() -> None:
    from senex import lens as lens_mod
    assert lens_mod.__doc__ and lens_mod.__doc__.strip() != ""
