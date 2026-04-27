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
from .aggregate import AggregatePhase
from .crosscut import CrosscutPhase
from .discovery import DiscoveryPhase
from .file_audit import FileAuditPhase
from .preflight import PreflightInputs, PreflightPhase

__all__ = [
    "AggregateFailed",
    "AggregatePhase",
    "CrosscutPhase",
    "DiscoveryPhase",
    "FileAuditPhase",
    "Phase",
    "PhaseAborted",
    "PreflightFailure",
    "PreflightInputs",
    "PreflightPhase",
    "RenderFatal",
    "ResumeIncompatible",
]
