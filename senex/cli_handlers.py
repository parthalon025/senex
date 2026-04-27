"""senex.cli_handlers — per-subcommand dispatchers (M10 Tasks 10.2-10.5).

Each ``cmd_<name>(args)`` function is the composition root for its subcommand:
loads config, builds the dependency graph, and calls into lower modules.

These functions are kept in a separate module from ``senex.cli`` so that:
  1. ``senex.cli`` stays the pure argparse layer (testable in isolation).
  2. Importing ``senex.cli`` for ``--help`` does not transitively load the
     entire stack (faster ``--version`` + ``--help`` startup).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subcommand dispatchers — populated by Tasks 10.2-10.5.
# Each returns an OS exit code (0/1/2/3/130 per spec §8.1).
# ---------------------------------------------------------------------------


def cmd_audit(args: argparse.Namespace) -> int:
    """``senex audit`` — populated by Task 10.2."""
    from senex.cli_audit import cmd_audit as _impl

    return _impl(args)


def cmd_view(args: argparse.Namespace) -> int:
    """``senex view`` — wraps ``senex.tui.view.run_view`` (M9 Task 9.9)."""
    from senex.tui.view import run_view
    from senex.tui.exceptions import ReplayError

    raw = getattr(args, "audit_dir", None)
    if raw is None:
        sys.stderr.write(
            "senex view: must supply <audit-dir>; auto-detect not yet implemented\n"
        )
        return 2
    audit_dir = Path(raw)
    if not audit_dir.exists():
        sys.stderr.write(f"senex view: audit dir does not exist: {audit_dir}\n")
        return 2
    speed = float(getattr(args, "speed", 1.0))
    try:
        asyncio.run(run_view(audit_dir, speed=speed))
    except ReplayError as exc:
        sys.stderr.write(f"senex view: {exc}\n")
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """``senex doctor`` — populated by Task 10.3."""
    from senex.cli_doctor import cmd_doctor as _impl

    return _impl(args)


def cmd_aggregate(args: argparse.Namespace) -> int:
    """``senex aggregate`` — populated by Task 10.4."""
    from senex.cli_aggregate import cmd_aggregate as _impl

    return _impl(args)


def cmd_config(args: argparse.Namespace) -> int:
    """``senex config show`` — populated by Task 10.5."""
    from senex.cli_config_show import cmd_config_show as _impl

    return _impl(args)


def cmd_lifecycle(args: argparse.Namespace) -> int:
    """``senex lifecycle status | clear-locks`` — wraps M4 lifecycle_cli."""
    from senex.lifecycle_cli import (
        cli_lifecycle_clear_locks,
        cli_lifecycle_status,
    )

    if args.lifecycle_cmd == "status":
        return asyncio.run(
            cli_lifecycle_status(as_json=bool(getattr(args, "as_json", False)))
        )
    if args.lifecycle_cmd == "clear-locks":
        return asyncio.run(
            cli_lifecycle_clear_locks(force=bool(getattr(args, "force", False)))
        )
    sys.stderr.write(f"senex lifecycle: unknown subcommand {args.lifecycle_cmd!r}\n")
    return 2


__all__ = [
    "cmd_aggregate",
    "cmd_audit",
    "cmd_config",
    "cmd_doctor",
    "cmd_lifecycle",
    "cmd_view",
]
