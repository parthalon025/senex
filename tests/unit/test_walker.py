"""Tests for senex.walker — repo discovery with safety guards.

Implements M2 Task 2.1 verification (spec §5.9 + §SEC-3 + §ARCH-14).
Per Conventions §6: TDD, tmp_path-isolated, no shared state.
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
from pathlib import Path

import pytest

from senex.config import WalkerCfg
from senex.events import EventBus
from senex.walker import (
    RepoPathInvalid,
    WalkResult,
    Walker,
)


def _windows_supports_symlinks() -> bool:
    """Return True iff the current Windows session can create symlinks.

    Plain user accounts cannot; admin shells and dev-mode-enabled accounts can.
    Linux / macOS always return True.
    """
    try:
        with tempfile.TemporaryDirectory() as d:
            src, lnk = Path(d) / "src", Path(d) / "lnk"
            src.write_text("x", encoding="utf-8")
            os.symlink(src, lnk)
            return True
    except OSError:
        return False


def test_walker_gitignore_skips_ignored_files(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    # Use "generated/" — gitignored but not in default_excludes, so walker
    # descends and reports files as "gitignore".  "build/" is in default_excludes
    # and is pruned outright (no skipped entry emitted).
    (tmp_path / ".gitignore").write_text(
        "ignored.py\ngenerated/\n*.log\n", encoding="utf-8"
    )
    (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "stray.log").write_text("noise\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "out.py").write_text("x = 3\n", encoding="utf-8")
    result = Walker(EventBus()).discover(tmp_path, WalkerCfg())
    kept_relpaths = {p.relative_to(tmp_path).as_posix() for p in result.kept}
    assert kept_relpaths == {"kept.py"}
    skipped_reasons = {p.name: r for p, r in result.skipped}
    assert skipped_reasons.get("ignored.py") == "gitignore"
    assert skipped_reasons.get("out.py") == "gitignore"
    # *.log: not in default extensions, but gitignore matches first; either
    # reason is acceptable.
    assert "stray.log" in skipped_reasons


def test_walker_extension_filter_default_list(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "main.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")
    result = Walker(EventBus()).discover(tmp_path, WalkerCfg())
    kept = {p.name for p in result.kept}
    assert kept == {"main.py"}
    skipped_names = {p.name for p, _ in result.skipped}
    assert "README.md" in skipped_names
    assert "data.bin" in skipped_names


@pytest.mark.parametrize(
    "excluded",
    ["node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".git", "vendor"],
)
def test_walker_default_excludes(tmp_path: Path, excluded: str) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "kept.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / excluded).mkdir(exist_ok=True)
    (tmp_path / excluded / "buried.py").write_text("x=2\n", encoding="utf-8")
    result = Walker(EventBus()).discover(tmp_path, WalkerCfg())
    relpaths = {p.relative_to(tmp_path).as_posix() for p in result.kept}
    assert "kept.py" in relpaths
    assert all(excluded not in p for p in relpaths)


def test_walker_excluded_dir_also_in_gitignore_does_not_count_toward_limit(
    tmp_path: Path,
) -> None:
    # Regression: .venv is in default_excludes AND .gitignore. Previously the
    # walker descended into it anyway, causing WalkerLimitExceeded on real repos.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    venv = tmp_path / ".venv"
    venv.mkdir()
    for i in range(10):
        (venv / f"pkg{i}.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "src.py").write_text("x=1\n", encoding="utf-8")
    cfg = WalkerCfg(max_files=5)  # would trip if walker descends into .venv
    result = Walker(EventBus()).discover(tmp_path, cfg)
    assert {p.name for p in result.kept} == {"src.py"}


def test_walker_excludes_tests_dir_when_include_tests_false(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "src.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test(): ...\n", encoding="utf-8"
    )
    cfg = WalkerCfg(include_tests=False)
    result = Walker(EventBus()).discover(tmp_path, cfg)
    assert {p.name for p in result.kept} == {"src.py"}


def test_walker_includes_tests_dir_when_include_tests_true(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "src.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test(): ...\n", encoding="utf-8"
    )
    cfg = WalkerCfg(include_tests=True)
    result = Walker(EventBus()).discover(tmp_path, cfg)
    assert {p.name for p in result.kept} == {"src.py", "test_x.py"}


def test_walker_skips_files_exceeding_max_bytes(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    cfg = WalkerCfg(max_size_bytes=524_288)  # 512 KB; spec §5.9
    large = tmp_path / "huge.py"
    large.write_bytes(b"x = 1\n" * 110_000)  # ~660 KB
    assert large.stat().st_size > 524_288
    result = Walker(EventBus()).discover(tmp_path, cfg)
    assert large not in result.kept
    reasons = {p.name: r for p, r in result.skipped}
    assert reasons.get("huge.py") == "too_large_bytes"


def test_walker_keeps_files_at_max_bytes_threshold(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    cfg = WalkerCfg(max_size_bytes=1024)
    f = tmp_path / "ok.py"
    f.write_bytes(b"x" * 1024)  # exactly the cap; kept
    result = Walker(EventBus()).discover(tmp_path, cfg)
    assert f in result.kept


@pytest.mark.skipif(
    platform.system() == "Windows" and not _windows_supports_symlinks(),
    reason="Windows symlinks require admin or developer-mode",
)
def test_walker_rejects_symlink_escape_to_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("# secret\n", encoding="utf-8")
    link = repo / "link_to_secret.py"
    os.symlink(outside / "secret.py", link)
    bus = EventBus()
    result = Walker(bus).discover(repo, WalkerCfg())
    assert link not in result.kept
    # Walker records skip reasons in WalkResult.skipped (sync API; M1 EventBus
    # is async-only; auditor M8 publishes events from result.skipped).
    assert any(
        p.name == "link_to_secret.py" and r == "symlink_escape"
        for p, r in result.skipped
    )


@pytest.mark.skipif(
    platform.system() == "Windows" and not _windows_supports_symlinks(),
    reason="Windows symlinks require admin or developer-mode",
)
def test_walker_allows_symlink_to_file_inside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    target = repo / "real.py"
    target.write_text("x = 1\n", encoding="utf-8")
    link = repo / "alias.py"
    os.symlink(target, link)
    result = Walker(EventBus()).discover(repo, WalkerCfg())
    relpaths = {p.relative_to(repo).as_posix() for p in result.kept}
    # Both "real.py" and "alias.py" are seen; alias.py resolves under repo
    # so is allowed.
    assert "real.py" in relpaths and "alias.py" in relpaths


def test_walker_invokes_os_walk_with_followlinks_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    seen: dict[str, object] = {}
    real_walk = os.walk

    def spy(top, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen["topdown"] = kwargs.get(
            "topdown", args[0] if args else True
        )
        seen["followlinks"] = kwargs.get(
            "followlinks", args[2] if len(args) >= 3 else False
        )
        return real_walk(top, *args, **kwargs)

    # Monkeypatch the symbol the walker imported (`from os import walk` would
    # bind locally; the impl calls `os.walk`).
    import senex.walker as walker_mod

    monkeypatch.setattr(walker_mod.os, "walk", spy)
    Walker(EventBus()).discover(tmp_path, WalkerCfg())
    assert seen["followlinks"] is False


@pytest.mark.skipif(
    platform.system() == "Windows" and not _windows_supports_symlinks(),
    reason="Windows symlinks require admin or developer-mode",
)
def test_walker_resolve_happens_before_is_relative_to(tmp_path: Path) -> None:
    """Regression: resolve() MUST run BEFORE is_relative_to.

    A naive impl that calls is_relative_to on the unresolved path would let a
    symlink whose literal path is under the repo escape. The synth fixture
    here is exactly that case.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "leak.py").write_text("# leak\n", encoding="utf-8")
    sneaky = repo / "looks_local.py"
    os.symlink(outside_dir / "leak.py", sneaky)
    result = Walker(EventBus()).discover(repo, WalkerCfg())
    assert sneaky not in result.kept
    assert any(r == "symlink_escape" for _, r in result.skipped)


@pytest.mark.skipif(
    sys.platform not in ("win32", "darwin"),
    reason="Case-collision repro requires case-insensitive FS (Windows / macOS HFS+)",
)
def test_walker_case_collision_assigns_hash_suffix_to_second(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    a = tmp_path / "Foo.py"
    a.write_text("x=1\n", encoding="utf-8")
    b = tmp_path / "foo.py"
    try:
        b.write_text("x=2\n", encoding="utf-8")
    except OSError:
        pytest.skip("FS rejected case-collision write")
    # On NTFS / case-insensitive FS, the second write overwrites the first
    # (both names map to one inode). os.listdir then shows only ONE entry,
    # so case-collision detection cannot trigger from a normal test repo.
    # Skip when only one entry survives.
    listing = {p for p in os.listdir(tmp_path) if p.lower() == "foo.py"}
    if len(listing) < 2:
        pytest.skip(
            "FS merged case-variant writes; cannot synthesize case-collision"
        )
    result = Walker(EventBus()).discover(tmp_path, WalkerCfg())
    report_paths = sorted(result.relpath_to_report_path.values())
    # Exactly one of the two has the "~<8hex>" suffix in its stem.
    suffixed = [
        p
        for p in report_paths
        if "~" in Path(p).stem
        and len(Path(p).stem.split("~")[-1]) == 8
    ]
    assert len(suffixed) == 1


def test_walker_deterministic_order(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    for name in ["c.py", "a.py", "b.py", "sub/d.py"]:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x=1\n", encoding="utf-8")
    r1 = Walker(EventBus()).discover(tmp_path, WalkerCfg())
    r2 = Walker(EventBus()).discover(tmp_path, WalkerCfg())
    assert [p.relative_to(tmp_path).as_posix() for p in r1.kept] == [
        p.relative_to(tmp_path).as_posix() for p in r2.kept
    ]
    assert [p.relative_to(tmp_path).as_posix() for p in r1.kept] == sorted(
        p.relative_to(tmp_path).as_posix() for p in r1.kept
    )


def test_walker_gitignore_globs_and_negation(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text(
        "*.log\ndist/\n!important.log\n", encoding="utf-8"
    )
    (tmp_path / "kept.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "noise.log").write_text("n\n", encoding="utf-8")
    (tmp_path / "important.log").write_text("i\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "out.py").write_text("o\n", encoding="utf-8")
    cfg = WalkerCfg(extensions=[".py", ".log"])  # widen to test .log behavior
    result = Walker(EventBus()).discover(tmp_path, cfg)
    kept = {p.name for p in result.kept}
    assert "kept.py" in kept and "important.log" in kept
    assert "noise.log" not in kept and "out.py" not in kept


def test_walker_raises_when_repo_missing(tmp_path: Path) -> None:
    with pytest.raises(RepoPathInvalid):
        Walker(EventBus()).discover(tmp_path / "does_not_exist", WalkerCfg())


def test_walker_raises_when_no_dot_git(tmp_path: Path) -> None:
    with pytest.raises(RepoPathInvalid):
        Walker(EventBus()).discover(tmp_path, WalkerCfg())  # no .git/


def test_walker_module_has_nonempty_docstring() -> None:
    from senex import walker as walker_mod

    assert walker_mod.__doc__ and walker_mod.__doc__.strip() != ""


def test_walker_walkresult_dataclass_shape() -> None:
    """WalkResult exposes kept, skipped, relpath_to_report_path."""
    r = WalkResult(kept=[], skipped=[], relpath_to_report_path={})
    assert r.kept == []
    assert r.skipped == []
    assert r.relpath_to_report_path == {}
