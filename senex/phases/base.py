"""senex.phases.base — Phase protocol + named exceptions for the auditor.

Implements spec §ARCH-3 (phases as resumable units of work) and §ARCH-15
(checkpoint state machine). The protocol is ``runtime_checkable`` so concrete
phase classes can be tested via ``isinstance(obj, Phase)`` without ABC
inheritance — keeps the implementations free of class-hierarchy boilerplate.

Conventions §1: module docstrings; §4: named exceptions at module top level;
§2: full type annotations on the public surface.

Import-boundary rule (spec §3.2): this module MUST NOT import from
``senex.tui``. Enforced by ``tests/unit/test_import_boundaries.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from senex.config import SenexConfig
    from senex.events import CommandBus, EventBus
    from senex.lens import Lens


@runtime_checkable
class Phase(Protocol):
    """Resumable unit of work in the audit pipeline (spec §ARCH-3).

    Five concrete implementations: ``PreflightPhase``, ``DiscoveryPhase``,
    ``FileAuditPhase``, ``CrosscutPhase``, ``AggregatePhase``. Each takes
    state in (read from disk by ``read_state`` or supplied by the auditor),
    does work, and persists state out via ``write_state``.

    Phases are stateless across ``do_work`` calls; the auditor owns persistence
    between phases. ``read_state`` / ``write_state`` are async to keep the I/O
    boundary uniform; cheap synchronous reads (e.g. in-memory dicts) may simply
    ``return value`` without ``await``.
    """

    name: str  # one of: "preflight" | "discovery" | "file_audit" | "crosscut" | "aggregate"

    async def read_state(self, audit_dir: Path) -> Any:
        """Load the phase's input state from ``audit_dir`` (or return a default)."""
        ...

    async def do_work(
        self,
        state: Any,
        lens: Lens,
        config: SenexConfig,
        bus: EventBus,
        command_bus: CommandBus,
    ) -> Any:
        """Perform the phase's work; return the new state."""
        ...

    async def write_state(self, audit_dir: Path, state: Any) -> None:
        """Persist the phase's output state to ``audit_dir``."""
        ...


# ---------------------------------------------------------------------------
# Named exceptions (spec §8.1 exit codes; R11 collapses to {0,1,2,3,130}).
# ---------------------------------------------------------------------------


class PreflightFailure(Exception):
    """Raised by ``PreflightPhase`` on the first hard FAIL.

    Carries the documented spec §8.1 exit code so the auditor can return it
    directly to the OS. ``check_name`` identifies which preflight row failed
    (handy for telemetry + the doctor JSON output).
    """

    def __init__(self, exit_code: int, message: str, check_name: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message
        self.check_name = check_name


class PhaseAborted(Exception):
    """Raised when a phase aborts due to an unrecoverable condition specific
    to that phase (e.g. discovery returns no files; crosscut reads an empty
    partial). Distinct from ``PreflightFailure`` (which is preflight-only)
    and ``AggregateFailed`` (which is recoverable via ``senex aggregate``).
    """


class ResumeIncompatible(Exception):
    """Raised when checkpoint hashes don't match current config / prompts /
    model / tool pack / lens (spec §8.5). exit_code=2 (config/setup error
    category — checkpoint hash mismatch is a setup-state mismatch; collapses
    into the spec §8.1 0/1/2/3/130 set per R11). The user can override with
    ``--allow-mixed-resume`` (M10 CLI).
    """

    exit_code: int = 2

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RenderFatal(Exception):
    """Truly unrecoverable render error (disk full, audit dir gone). NOT for
    per-file render failures (those go to ``<file>.RENDER_ERROR.md`` per
    §ARCH-13 and let the run continue). Only ``DiskFatalError`` (ENOSPC /
    EROFS / EDQUOT) is run-killing.
    """


class AggregateFailed(Exception):
    """Aggregate phase failed but is resumable via ``senex aggregate``.

    exit_code=1 (partial success — per-file work survived; only aggregation
    failed; user can run ``senex aggregate`` to recover). Collapses into the
    spec §8.1 0/1/2/3/130 set per R11.
    """

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


__all__ = [
    "AggregateFailed",
    "Phase",
    "PhaseAborted",
    "PreflightFailure",
    "RenderFatal",
    "ResumeIncompatible",
]
