"""Tests for ``senex.cli_wizard`` — interactive pre-TUI text wizard (M11).

The wizard is invoked from ``senex audit`` with no positional path. It walks
the user through repo + model + lens + flag selection over plain stdin/stdout
(no curses, no Textual), then returns a ``RuntimeConfig`` ready to hand off
to ``SenexApp.start_audit`` or the headless subscriber.

Per conventions §6, every test asserts a single observable outcome: input
script in -> wizard branch behavior out. Streams are injected as
``io.StringIO`` so tests never touch the real terminal.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from senex.config import RepoCfg, SenexConfig
from senex.lmstudio_client import LoadedModelInfo
from senex.lmstudio_errors import LMSConnectionLost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _two_repo_config(tmp_path: Path) -> tuple[SenexConfig, Path, Path]:
    """Return a SenexConfig with two repo entries pointing at tmp_path subdirs."""
    repo_a = tmp_path / "alpha"
    repo_b = tmp_path / "beta"
    repo_a.mkdir()
    repo_b.mkdir()
    cfg = SenexConfig(
        repos=[
            RepoCfg(name="alpha", path=str(repo_a)),
            RepoCfg(name="beta", path=str(repo_b)),
        ]
    )
    return cfg, repo_a, repo_b


def _fake_client(models: list[str]) -> MagicMock:
    """Build a MagicMock LM Studio client whose ``list_loaded_models`` returns ``models``."""
    client = MagicMock()

    async def _list() -> list[LoadedModelInfo]:
        return [LoadedModelInfo(id=m) for m in models]

    client.list_loaded_models.side_effect = _list
    return client


# ---------------------------------------------------------------------------
# Repo selection
# ---------------------------------------------------------------------------


def test_wizard_lists_config_repos(tmp_path: Path) -> None:
    """Two configured repos -> menu shows both numbered."""
    from senex.cli_wizard import _select_repo

    cfg, repo_a, repo_b = _two_repo_config(tmp_path)
    stdin = io.StringIO("1\n")
    stdout = io.StringIO()
    name, path = _select_repo(cfg, stdin=stdin, stdout=stdout)
    output = stdout.getvalue()
    assert "alpha" in output
    assert "beta" in output
    assert name == "alpha"
    assert path == repo_a


def test_wizard_user_picks_repo_by_number(tmp_path: Path) -> None:
    """Numeric input selects the matching repo by 1-based index."""
    from senex.cli_wizard import _select_repo

    cfg, repo_a, repo_b = _two_repo_config(tmp_path)
    stdin = io.StringIO("2\n")
    stdout = io.StringIO()
    name, path = _select_repo(cfg, stdin=stdin, stdout=stdout)
    assert name == "beta"
    assert path == repo_b


def test_wizard_user_picks_custom_path(tmp_path: Path) -> None:
    """Picking 'custom path' option prompts for a path next."""
    from senex.cli_wizard import _select_repo

    cfg, _, _ = _two_repo_config(tmp_path)
    custom = tmp_path / "gamma"
    custom.mkdir()
    # 2 config repos -> options 1,2; "Discover" = 3; "Enter custom path" = 4.
    stdin = io.StringIO(f"4\n{custom}\n")
    stdout = io.StringIO()
    name, path = _select_repo(cfg, stdin=stdin, stdout=stdout)
    assert path == custom.resolve()
    assert name == "gamma"


def test_wizard_invalid_path_reprompts(tmp_path: Path) -> None:
    """Non-existent custom path re-prompts instead of crashing."""
    from senex.cli_wizard import _select_repo

    cfg, repo_a, _ = _two_repo_config(tmp_path)
    bogus = tmp_path / "does-not-exist"
    # 4 -> custom path; first try bogus; second try repo_a (real).
    stdin = io.StringIO(f"4\n{bogus}\n{repo_a}\n")
    stdout = io.StringIO()
    name, path = _select_repo(cfg, stdin=stdin, stdout=stdout)
    assert path == repo_a.resolve()
    output = stdout.getvalue().lower()
    assert "not" in output or "exist" in output or "invalid" in output


def test_wizard_user_picks_discover(tmp_path: Path) -> None:
    """Picking 'Discover more...' triggers discovery and re-prompts."""
    from senex.cli_wizard import _select_repo

    cfg, _, _ = _two_repo_config(tmp_path)
    found = tmp_path / "found-repo"
    found.mkdir()
    (found / ".git").mkdir()
    # 2 config + Discover (=3); after discovery, 3 candidates would be: alpha, beta, found-repo.
    # Pick number 3 (found-repo) on second pass.
    stdin = io.StringIO(f"3\n{tmp_path}\n3\n")
    stdout = io.StringIO()
    with patch(
        "senex.cli_wizard._discover_repos_on_disk",
        return_value=[found],
    ) as mock_disc:
        name, path = _select_repo(cfg, stdin=stdin, stdout=stdout)
    mock_disc.assert_called_once()
    assert path == found.resolve()


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_wizard_lists_loaded_models() -> None:
    """LM Studio returns 3 models -> menu shows them; default annotated."""
    from senex.cli_wizard import _select_model

    client = _fake_client(["m1", "m2", "m3"])
    stdin = io.StringIO("1\n")
    stdout = io.StringIO()
    chosen = _select_model(client, default_model="m2", stdin=stdin, stdout=stdout)
    output = stdout.getvalue()
    assert "m1" in output and "m2" in output and "m3" in output
    # Default annotation visible somewhere near "m2".
    assert "default" in output.lower()
    assert chosen == "m1"


def test_wizard_user_picks_model_by_number() -> None:
    """Numeric input selects model by 1-based index."""
    from senex.cli_wizard import _select_model

    client = _fake_client(["m1", "m2", "m3"])
    stdin = io.StringIO("2\n")
    stdout = io.StringIO()
    chosen = _select_model(client, default_model="m1", stdin=stdin, stdout=stdout)
    assert chosen == "m2"


def test_wizard_user_hits_enter_for_default_model() -> None:
    """Empty input returns the configured default."""
    from senex.cli_wizard import _select_model

    client = _fake_client(["m1", "m2"])
    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    chosen = _select_model(client, default_model="m2", stdin=stdin, stdout=stdout)
    assert chosen == "m2"


def test_wizard_lms_unreachable_raises_wizard_error() -> None:
    """LMSConnectionLost -> WizardError with helpful message."""
    from senex.cli_wizard import WizardError, _select_model

    client = MagicMock()

    async def _boom() -> list[LoadedModelInfo]:
        raise LMSConnectionLost("connection refused")

    client.list_loaded_models.side_effect = _boom
    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    with pytest.raises(WizardError) as exc:
        _select_model(client, default_model="m1", stdin=stdin, stdout=stdout)
    assert "LM Studio" in str(exc.value)


# ---------------------------------------------------------------------------
# Yes/no prompt
# ---------------------------------------------------------------------------


def test_wizard_yes_no_default_yes() -> None:
    from senex.cli_wizard import _yes_no

    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    assert _yes_no("Continue?", default=True, stdin=stdin, stdout=stdout) is True


def test_wizard_yes_no_default_no() -> None:
    from senex.cli_wizard import _yes_no

    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    assert _yes_no("Continue?", default=False, stdin=stdin, stdout=stdout) is False


def test_wizard_yes_no_user_overrides() -> None:
    from senex.cli_wizard import _yes_no

    stdin = io.StringIO("n\n")
    stdout = io.StringIO()
    assert _yes_no("Continue?", default=True, stdin=stdin, stdout=stdout) is False


# ---------------------------------------------------------------------------
# Full happy path
# ---------------------------------------------------------------------------


def test_wizard_confirm_yes_returns_runtime_config(tmp_path: Path) -> None:
    """Full happy path: pick repo 1, default model, defaults for include-tests + thinking,
    Y at confirm -> RuntimeConfig populated."""
    from senex.cli_wizard import interactive_audit_setup

    cfg, repo_a, _ = _two_repo_config(tmp_path)
    cfg = SenexConfig(
        repos=cfg.repos,
        lmstudio=cfg.lmstudio.model_copy(update={"model": "m1"}),
    )
    client = _fake_client(["m1", "m2"])
    # 1=repo  ""=scan_subdir  ""=default model  ""=ctx_window  ""=effort  ""=include_tests N  ""=thinking Y  ""=confirm Y
    stdin = io.StringIO("1\n\n\n\n\n\n\n\n")
    stdout = io.StringIO()
    rt = interactive_audit_setup(cfg, client, stdin=stdin, stdout=stdout)
    assert rt.repo == repo_a
    assert rt.config.lmstudio.model == "m1"


def test_wizard_confirm_no_raises_cancelled(tmp_path: Path) -> None:
    """N at the final confirm prompt -> WizardCancelled."""
    from senex.cli_wizard import WizardCancelled, interactive_audit_setup

    cfg, repo_a, _ = _two_repo_config(tmp_path)
    cfg = SenexConfig(
        repos=cfg.repos,
        lmstudio=cfg.lmstudio.model_copy(update={"model": "m1"}),
    )
    client = _fake_client(["m1"])
    # 1=repo  ""=default model  ""=ctx_window  ""=effort  ""=tests-N  ""=thinking-Y  n=confirm
    stdin = io.StringIO("1\n\n\n\n\n\n n\n")
    stdout = io.StringIO()
    with pytest.raises(WizardCancelled):
        interactive_audit_setup(cfg, client, stdin=stdin, stdout=stdout)


def test_wizard_ctrl_c_raises_cancelled(tmp_path: Path) -> None:
    """KeyboardInterrupt mid-prompt -> WizardCancelled (caught + re-raised cleanly)."""
    from senex.cli_wizard import WizardCancelled, interactive_audit_setup

    cfg, _, _ = _two_repo_config(tmp_path)
    client = _fake_client(["m1"])

    class _BoomStream:
        def readline(self) -> str:
            raise KeyboardInterrupt()

    stdout = io.StringIO()
    with pytest.raises(WizardCancelled):
        interactive_audit_setup(cfg, client, stdin=_BoomStream(), stdout=stdout)


# ---------------------------------------------------------------------------
# Context window selection
# ---------------------------------------------------------------------------


def test_select_context_window_enter_keeps_current() -> None:
    from senex.cli_wizard import _select_context_window

    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    assert _select_context_window(32768, stdin=stdin, stdout=stdout) == 32768


def test_select_context_window_pick_by_number() -> None:
    from senex.cli_wizard import _select_context_window, _CONTEXT_WINDOW_OPTIONS

    stdin = io.StringIO("1\n")
    stdout = io.StringIO()
    result = _select_context_window(_CONTEXT_WINDOW_OPTIONS[0], stdin=stdin, stdout=stdout)
    assert result == _CONTEXT_WINDOW_OPTIONS[0]


def test_select_context_window_shows_recommended_tag() -> None:
    from senex.cli_wizard import _select_context_window

    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    _select_context_window(32768, stdin=stdin, stdout=stdout)
    out = stdout.getvalue()
    assert "recommended" in out


# ---------------------------------------------------------------------------
# Effort selection
# ---------------------------------------------------------------------------


def test_select_effort_enter_keeps_current() -> None:
    from senex.cli_wizard import _select_effort

    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    assert _select_effort("high", stdin=stdin, stdout=stdout) == "high"


def test_select_effort_pick_medium_by_number() -> None:
    from senex.cli_wizard import _select_effort

    # Options are high(1), medium(2), low(3).
    stdin = io.StringIO("2\n")
    stdout = io.StringIO()
    assert _select_effort("high", stdin=stdin, stdout=stdout) == "medium"


def test_select_effort_pick_by_name() -> None:
    from senex.cli_wizard import _select_effort

    stdin = io.StringIO("low\n")
    stdout = io.StringIO()
    assert _select_effort("high", stdin=stdin, stdout=stdout) == "low"


def test_select_effort_shows_time_estimates() -> None:
    from senex.cli_wizard import _select_effort

    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    _select_effort("medium", stdin=stdin, stdout=stdout)
    out = stdout.getvalue()
    assert "min/file" in out


# ---------------------------------------------------------------------------
# Full wizard — effort and context_window propagate to RuntimeConfig
# ---------------------------------------------------------------------------


def test_wizard_effort_and_ctx_propagate_to_config(tmp_path: Path) -> None:
    """Selecting effort=low and context_window=16384 reaches RuntimeConfig."""
    from senex.cli_wizard import interactive_audit_setup

    cfg, repo_a, _ = _two_repo_config(tmp_path)
    cfg = SenexConfig(
        repos=cfg.repos,
        lmstudio=cfg.lmstudio.model_copy(update={"model": "m1"}),
    )
    client = _fake_client(["m1"])
    # 1=repo  ""=scan_subdir  ""=model  2=ctx(16384)  3=effort(low)  ""=tests  ""=thinking  ""=confirm
    stdin = io.StringIO("1\n\n\n2\n3\n\n\n\n")
    stdout = io.StringIO()
    rt = interactive_audit_setup(cfg, client, stdin=stdin, stdout=stdout)
    assert rt.config.lmstudio.context_window == 16384
    assert rt.config.lmstudio.thinking.effort == "low"


# ---------------------------------------------------------------------------
# _discover_repos_on_disk
# ---------------------------------------------------------------------------


def test_discover_repos_excludes_known_noise(tmp_path: Path) -> None:
    """Synthetic tree: only the real repo (not .venv/.git or node_modules/.git) returned."""
    from senex.cli_wizard import _discover_repos_on_disk

    real = tmp_path / "real-repo"
    real.mkdir()
    (real / ".git").mkdir()

    venv_repo = tmp_path / ".venv" / "fake-pkg"
    venv_repo.mkdir(parents=True)
    (venv_repo / ".git").mkdir()

    nm_repo = tmp_path / "node_modules" / "vendor"
    nm_repo.mkdir(parents=True)
    (nm_repo / ".git").mkdir()

    found = _discover_repos_on_disk(tmp_path)
    assert real.resolve() in [p.resolve() for p in found]
    assert venv_repo.resolve() not in [p.resolve() for p in found]
    assert nm_repo.resolve() not in [p.resolve() for p in found]


def test_discover_repos_handles_worktree_pointer_file(tmp_path: Path) -> None:
    """A `.git` *file* containing a `gitdir:` pointer still counts as a repo."""
    from senex.cli_wizard import _discover_repos_on_disk

    wt = tmp_path / "worktree-repo"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /tmp/some/place\n", encoding="utf-8")

    found = _discover_repos_on_disk(tmp_path)
    assert wt.resolve() in [p.resolve() for p in found]


def test_discover_repos_skips_bare_repos(tmp_path: Path) -> None:
    """A `.git/HEAD`-only directory with no working tree is NOT returned."""
    from senex.cli_wizard import _discover_repos_on_disk

    # A bare repo at the top level: only HEAD (no working tree). The parent
    # *is* the .git dir itself; discovery should not flag it as a repo.
    bare = tmp_path / "bare.git"
    bare.mkdir()
    (bare / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (bare / "objects").mkdir()
    (bare / "refs").mkdir()

    found = _discover_repos_on_disk(tmp_path)
    assert bare.resolve() not in [p.resolve() for p in found]


def test_discover_repos_respects_max_depth(tmp_path: Path) -> None:
    """`max_depth=2` doesn't recurse 3 levels deep."""
    from senex.cli_wizard import _discover_repos_on_disk

    # tmp_path / a / b / c / repo / .git  -> depth 4 from tmp_path; should be missed.
    deep = tmp_path / "a" / "b" / "c" / "repo"
    deep.mkdir(parents=True)
    (deep / ".git").mkdir()

    # tmp_path / shallow / .git  -> depth 1; should be found.
    shallow = tmp_path / "shallow"
    shallow.mkdir()
    (shallow / ".git").mkdir()

    found = _discover_repos_on_disk(tmp_path, max_depth=2)
    found_resolved = [p.resolve() for p in found]
    assert shallow.resolve() in found_resolved
    assert deep.resolve() not in found_resolved


def test_discover_repos_handles_permission_denied(tmp_path: Path) -> None:
    """`os.walk` raising PermissionError on a subdir doesn't crash discovery."""
    from senex.cli_wizard import _discover_repos_on_disk

    real = tmp_path / "ok-repo"
    real.mkdir()
    (real / ".git").mkdir()

    def _walk(root: str, *_: Any, **__: Any) -> Any:
        # Yield the top level normally, then raise on a subdir traversal.
        yield (str(tmp_path), ["ok-repo"], [])
        yield (str(real), [".git"], [])
        raise PermissionError("nope")

    with patch("senex.cli_wizard.os.walk", side_effect=_walk):
        found = _discover_repos_on_disk(tmp_path)
    assert real.resolve() in [p.resolve() for p in found]
