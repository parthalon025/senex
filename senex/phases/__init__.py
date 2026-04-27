"""senex.phases — Five resumable phases + named exceptions (spec §ARCH-3).

Re-exports the phase protocol, the five concrete phase classes, and all
named exceptions used by ``senex.auditor.run_audit``.

Import-boundary rule (spec §3.2): NO module under ``senex.phases.*`` may
import from ``senex.tui``. Enforced by ``tests/unit/test_import_boundaries.py``.
"""
from __future__ import annotations

from .base import (
    AggregateFailed,
    Phase,
    PhaseAborted,
    PreflightFailure,
    RenderFatal,
    ResumeIncompatible,
)

__all__ = [
    "AggregateFailed",
    "Phase",
    "PhaseAborted",
    "PreflightFailure",
    "RenderFatal",
    "ResumeIncompatible",
]
