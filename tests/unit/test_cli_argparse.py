"""Tests for ``senex.cli`` argparse skeleton (M10 Task 10.1).

Verifies subcommand structure, common parent-parser flags, mutually-exclusive
combinations, and ``--version`` / ``--help`` behavior. Each test parses a
specific argv vector and asserts the resulting Namespace shape; tests do NOT
exercise the dispatcher (cmd_* handlers are covered by per-subcommand tests).
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

from senex.cli import build_parser, main


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Subcommand registration
# ---------------------------------------------------------------------------


def test_audit_subcommand_parses_with_repo() -> None:
    ns = _parse(["audit", "C:/some/repo"])
    assert ns.command == "audit"
    assert ns.repo_path == "C:/some/repo"
    assert ns.resume is False
    assert ns.nightly is False
    assert ns.no_tui is False


def test_audit_subcommand_resume_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--resume"])
    assert ns.resume is True


def test_audit_subcommand_nightly_flag() -> None:
    ns = _parse(["audit", "--nightly"])
    assert ns.nightly is True
    assert ns.repo_path is None


def test_audit_subcommand_model_sentinel_at_auto() -> None:
    """Per spec API-5: sentinels are passed through as literal strings."""
    ns = _parse(["audit", "C:/some/repo", "--model", "@auto"])
    assert ns.model == "@auto"


def test_audit_subcommand_model_sentinel_at_first() -> None:
    ns = _parse(["audit", "C:/some/repo", "--model", "@first"])
    assert ns.model == "@first"


def test_audit_subcommand_resume_and_nightly_raises() -> None:
    """Mutually-exclusive: --resume + --nightly must error (exit 2).

    Validation happens at ``main()`` cross-flag stage (test_main_resume_and_nightly_returns_two
    asserts the exit code; here we assert the parser still accepts the args
    so that the cross-flag check has a chance to inspect them).
    """
    # Both flags accepted at parse time; cross-flag check happens in main().
    ns = _parse(["audit", "C:/some/repo", "--resume", "--nightly"])
    assert ns.resume is True
    assert ns.nightly is True


def test_audit_subcommand_min_priority_choices() -> None:
    ns = _parse(["audit", "C:/some/repo", "--min-priority", "high"])
    assert ns.min_priority == "high"


def test_audit_subcommand_lens_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--lens", "correctness"])
    assert ns.lens == "correctness"


def test_audit_subcommand_include_tests_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--include-tests"])
    assert ns.include_tests is True


def test_audit_subcommand_unsafe_resume_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--resume", "--unsafe-resume"])
    assert ns.unsafe_resume is True


def test_audit_subcommand_allow_mixed_resume_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--resume", "--allow-mixed-resume"])
    assert ns.allow_mixed_resume is True


def test_audit_subcommand_no_wizard_flag() -> None:
    """M11: ``--no-wizard`` skips the interactive launcher when no path."""
    ns = _parse(["audit", "--no-wizard"])
    assert ns.no_wizard is True
    assert ns.repo_path is None


def test_audit_subcommand_no_wizard_default_false() -> None:
    ns = _parse(["audit", "C:/some/repo"])
    assert ns.no_wizard is False


def test_view_subcommand_parses_with_dir() -> None:
    ns = _parse(["view", "C:/audit/dir"])
    assert ns.command == "view"
    assert ns.audit_dir == "C:/audit/dir"
    assert ns.speed == 1.0


def test_view_subcommand_speed_flag() -> None:
    ns = _parse(["view", "C:/audit/dir", "--speed", "5.0"])
    assert ns.speed == 5.0


def test_view_subcommand_no_audit_dir() -> None:
    ns = _parse(["view"])
    assert ns.command == "view"
    assert ns.audit_dir is None


def test_doctor_subcommand_parses_with_repo() -> None:
    ns = _parse(["doctor", "C:/some/repo"])
    assert ns.command == "doctor"
    assert ns.repo_path == "C:/some/repo"


def test_doctor_subcommand_no_repo() -> None:
    ns = _parse(["doctor"])
    assert ns.command == "doctor"
    assert ns.repo_path is None


def test_doctor_subcommand_json_flag() -> None:
    ns = _parse(["doctor", "--json"])
    assert ns.as_json is True


def test_aggregate_subcommand_parses() -> None:
    ns = _parse(["aggregate", "C:/audit/dir"])
    assert ns.command == "aggregate"
    assert ns.audit_dir == "C:/audit/dir"


def test_aggregate_subcommand_requires_audit_dir() -> None:
    with pytest.raises(SystemExit):
        _parse(["aggregate"])


def test_config_show_with_repo() -> None:
    ns = _parse(["config", "show", "C:/some/repo"])
    assert ns.command == "config"
    assert ns.config_subcmd == "show"
    assert ns.repo_path == "C:/some/repo"


def test_config_show_with_all() -> None:
    ns = _parse(["config", "show", "--all"])
    assert ns.config_subcmd == "show"
    assert ns.all is True


def test_config_show_with_json() -> None:
    ns = _parse(["config", "show", "C:/some/repo", "--json"])
    assert ns.as_json is True


def test_lifecycle_status_subcommand() -> None:
    ns = _parse(["lifecycle", "status"])
    assert ns.command == "lifecycle"
    assert ns.lifecycle_cmd == "status"


def test_lifecycle_status_json() -> None:
    ns = _parse(["lifecycle", "status", "--json"])
    assert ns.as_json is True


def test_lifecycle_clear_locks_subcommand() -> None:
    ns = _parse(["lifecycle", "clear-locks"])
    assert ns.command == "lifecycle"
    assert ns.lifecycle_cmd == "clear-locks"
    assert ns.force is False


def test_lifecycle_clear_locks_force_flag() -> None:
    ns = _parse(["lifecycle", "clear-locks", "--force"])
    assert ns.force is True


# ---------------------------------------------------------------------------
# Common (parent) flags inherit on every subcommand
# ---------------------------------------------------------------------------


def test_audit_inherits_config_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--config", "C:/path/to/config.toml"])
    assert ns.config == "C:/path/to/config.toml"


def test_audit_inherits_no_tui_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--no-tui"])
    assert ns.no_tui is True


def test_audit_inherits_verbose_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--verbose"])
    assert ns.verbose is True


def test_audit_inherits_quiet_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--quiet"])
    assert ns.quiet is True


def test_audit_inherits_no_load_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--no-load"])
    assert ns.no_load is True


def test_audit_inherits_no_unload_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--no-unload"])
    assert ns.no_unload is True


def test_audit_inherits_unload_after_flag() -> None:
    ns = _parse(["audit", "C:/some/repo", "--unload-after"])
    assert ns.unload_after is True


# ---------------------------------------------------------------------------
# --version, --help, missing subcommand
# ---------------------------------------------------------------------------


def test_version_flag_returns_zero_and_prints_version() -> None:
    """--version exits 0 and prints senex.__version__."""
    import senex

    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert senex.__version__ in buf.getvalue()


def test_help_flag_returns_zero() -> None:
    """--help exits 0 and lists every subcommand name."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    text = buf.getvalue()
    for sub in ("audit", "view", "doctor", "aggregate", "config", "lifecycle"):
        assert sub in text


def test_no_subcommand_exits_nonzero() -> None:
    """argparse with required=True subparser should error when no command given."""
    buf = io.StringIO()
    with redirect_stderr(buf):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args([])
    assert exc.value.code != 0


# ---------------------------------------------------------------------------
# main() entry point — exit code for invalid combinations
# ---------------------------------------------------------------------------


def test_main_resume_and_nightly_returns_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """``senex audit --resume --nightly`` exits 2 with a clear stderr message."""
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = main(["audit", "--resume", "--nightly"])
    assert rc == 2
    assert "resume" in buf.getvalue().lower() or "nightly" in buf.getvalue().lower()
