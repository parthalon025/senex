"""senex.cli — entry point + argparse for all 6 subcommands (M10).

Implements spec §10 (CLI surface), §API-4 (argparse subcommand structure),
§API-5 (sentinel handling for ``--model``).

Composition root: this module does NO business logic. Each subcommand is a
small ``cmd_<name>(args, ...)`` function that wires lower-layer objects
together and calls into them. Tests assert that args parse correctly and
that deps are wired correctly; behavioral assertions belong in lower modules.

Exit-code contract (per spec §8.1):
    0   success
    1   partial success (some files errored)
    2   config / setup error
    3   external dependency error
    130 interrupted (SIGINT)
"""
from __future__ import annotations

import argparse
import logging
import sys

import senex


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Named exceptions
# ---------------------------------------------------------------------------


class CLIArgError(Exception):
    """Invalid CLI argument combination (e.g. ``--resume`` + ``--nightly``).

    Caught in ``main()``; prints to stderr and returns exit code 2.
    """


class ValidationGateFailed(Exception):
    """Raised by ``senex doctor`` when any check returns ``fail``.

    Caught in ``main()``; returns the failing check's exit code.
    """

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# Argparse builders
# ---------------------------------------------------------------------------


def _build_common_parent() -> argparse.ArgumentParser:
    """Parent parser with flags inherited by every subcommand.

    Per spec §10 common flags: ``--config``, ``--no-tui``, ``--verbose``,
    ``--quiet``, ``--json``, ``--no-load``, ``--no-unload``, ``--unload-after``.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to senex.config.toml (default: ./senex.config.toml).",
    )
    parent.add_argument(
        "--no-tui",
        action="store_true",
        help="Disable TUI; use HeadlessSubscriber stdout output.",
    )
    parent.add_argument(
        "--verbose", action="store_true", help="Verbose logging (DEBUG)."
    )
    parent.add_argument(
        "--quiet", action="store_true", help="Quiet logging (WARN+)."
    )
    parent.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON output (where supported).",
    )
    parent.add_argument(
        "--no-load",
        action="store_true",
        help="Do not auto-load the model at start (overrides config).",
    )
    parent.add_argument(
        "--no-unload",
        action="store_true",
        help="Do not auto-unload the model on exit (overrides config).",
    )
    parent.add_argument(
        "--unload-after",
        action="store_true",
        help="Unload the model when the runlock count drops to zero.",
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser plus all 6 subcommand parsers.

    Returns:
        A configured ``argparse.ArgumentParser`` whose ``parse_args`` returns
        a Namespace with at least ``command`` set to one of:
        ``audit | view | doctor | aggregate | config | lifecycle``.
    """
    parent = _build_common_parent()

    root = argparse.ArgumentParser(
        prog="senex",
        description="senex — local-LLM repository audit tool.",
    )
    root.add_argument(
        "--version",
        action="version",
        version=f"senex {senex.__version__}",
    )

    subparsers = root.add_subparsers(dest="command", required=True)

    # ---- audit ---------------------------------------------------------
    audit_p = subparsers.add_parser(
        "audit",
        parents=[parent],
        help="Run an audit against a repository.",
    )
    audit_p.add_argument(
        "repo_path",
        nargs="?",
        default=None,
        help="Path to the repo to audit (omit when --nightly).",
    )
    audit_p.add_argument(
        "--resume",
        action="store_true",
        help="Resume the latest unfinished audit for this repo.",
    )
    audit_p.add_argument(
        "--nightly",
        action="store_true",
        help="Iterate every [[repos]] in the config.",
    )
    audit_p.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test files in the audit walker.",
    )
    audit_p.add_argument(
        "--lens",
        type=str,
        default=None,
        help="Override the configured lens name.",
    )
    audit_p.add_argument(
        "--min-priority",
        type=str,
        choices=["all", "low", "medium", "high"],
        default=None,
        help="Filter findings below this priority level.",
    )
    audit_p.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the configured model id (sentinels: @auto, @first).",
    )
    audit_p.add_argument(
        "--allow-mixed-resume",
        action="store_true",
        help="Tolerate fingerprint mismatch on resume (advanced).",
    )
    audit_p.add_argument(
        "--unsafe-resume",
        action="store_true",
        help="Bypass full hash discipline on resume (advanced).",
    )

    # ---- view ----------------------------------------------------------
    view_p = subparsers.add_parser(
        "view",
        parents=[parent],
        help="Replay an audit's events.jsonl through a Monitor screen.",
    )
    view_p.add_argument(
        "audit_dir",
        nargs="?",
        default=None,
        help="Path to the audit directory to replay (default: latest).",
    )
    view_p.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Replay speed multiplier (1.0 = wall-clock; 10.0 = 10x).",
    )

    # ---- doctor --------------------------------------------------------
    doctor_p = subparsers.add_parser(
        "doctor",
        parents=[parent],
        help="Run preflight diagnostics; --json for CI-friendly output.",
    )
    doctor_p.add_argument(
        "repo_path",
        nargs="?",
        default=None,
        help="Repo path to check (omit to check every [[repos]]).",
    )

    # ---- aggregate -----------------------------------------------------
    agg_p = subparsers.add_parser(
        "aggregate",
        parents=[parent],
        help="Re-run Phase 5 (aggregation) against an existing audit dir.",
    )
    agg_p.add_argument(
        "audit_dir",
        help="Path to the audit directory.",
    )

    # ---- config show ---------------------------------------------------
    cfg_p = subparsers.add_parser(
        "config",
        parents=[parent],
        help="Inspect resolved configuration.",
    )
    cfg_sub = cfg_p.add_subparsers(dest="config_subcmd", required=True)
    cfg_show = cfg_sub.add_parser(
        "show",
        parents=[parent],
        help="Show resolved configuration for a repo (TOML or --json).",
    )
    cfg_show.add_argument(
        "repo_path",
        nargs="?",
        default=None,
        help="Repo path to resolve config for (omit to dump all).",
    )
    cfg_show.add_argument(
        "--all",
        action="store_true",
        help="Dump resolved config for every [[repos]] entry.",
    )

    # ---- lifecycle -----------------------------------------------------
    life_p = subparsers.add_parser(
        "lifecycle",
        parents=[parent],
        help="Inspect / manage LM Studio model lifecycle state.",
    )
    life_sub = life_p.add_subparsers(dest="lifecycle_cmd", required=True)
    life_status = life_sub.add_parser(
        "status",
        parents=[parent],
        help="List loaded models + runlock holders.",
    )
    life_status.set_defaults(force=False)
    life_clear = life_sub.add_parser(
        "clear-locks",
        parents=[parent],
        help="Prune stale-PID runlock holder entries.",
    )
    life_clear.add_argument(
        "--force",
        action="store_true",
        help="Also remove live-PID holders (DESTRUCTIVE).",
    )

    return root


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def _validate_args(args: argparse.Namespace) -> None:
    """Cross-flag validation (raises ``CLIArgError`` on conflicts)."""
    if args.command == "audit":
        if getattr(args, "resume", False) and getattr(args, "nightly", False):
            raise CLIArgError(
                "--resume and --nightly are mutually exclusive; "
                "resume targets a single audit dir, nightly iterates [[repos]]"
            )


def main(argv: list[str] | None = None) -> int:
    """Parse argv, dispatch to ``cmd_<command>``, return process exit code.

    Returns:
        Process exit code per spec §8.1 (0/1/2/3/130).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # Cross-flag validation (post-parse).
    try:
        _validate_args(args)
    except CLIArgError as exc:
        sys.stderr.write(f"senex: error: {exc}\n")
        return 2

    # Logging level from --verbose / --quiet.
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.DEBUG)
    elif getattr(args, "quiet", False):
        logging.basicConfig(level=logging.WARNING)
    else:
        logging.basicConfig(level=logging.INFO)

    # Dispatch.
    if args.command == "audit":
        from senex.cli_handlers import cmd_audit

        return cmd_audit(args)
    if args.command == "view":
        from senex.cli_handlers import cmd_view

        return cmd_view(args)
    if args.command == "doctor":
        from senex.cli_handlers import cmd_doctor

        return cmd_doctor(args)
    if args.command == "aggregate":
        from senex.cli_handlers import cmd_aggregate

        return cmd_aggregate(args)
    if args.command == "config":
        from senex.cli_handlers import cmd_config

        return cmd_config(args)
    if args.command == "lifecycle":
        from senex.cli_handlers import cmd_lifecycle

        return cmd_lifecycle(args)

    # argparse should have rejected unknown commands; guard just in case.
    sys.stderr.write(f"senex: unknown command: {args.command!r}\n")
    return 2


__all__ = [
    "CLIArgError",
    "ValidationGateFailed",
    "build_parser",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
