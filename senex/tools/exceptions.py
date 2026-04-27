"""senex.tools.exceptions — named tooling exception hierarchy.

Implements M5 plan key contracts (§Exceptions). Each subclass exposes a
class-level ``kind`` attribute that the registry maps onto the
``ToolError`` event ``kind`` field (constrained by ``senex.events``):

| Exception                | kind                |
|--------------------------|---------------------|
| ``ToolInputInvalid``     | ``schema_invalid``  |
| ``PathOutsideRepo``      | ``path_rejected``   |
| ``SymlinkRefused``       | ``path_rejected``   |
| ``RegexTooComplex``      | ``regex_invalid``   |
| ``RegexTimeoutExceeded`` | ``regex_timeout``   |
| ``ToolDispatchFailed``   | ``dispatch_failed`` |
| ``ToolUnavailable``      | ``unavailable``     |
| ``ToolBudgetExhausted``  | ``budget_exhausted``|

Per conventions §4 (named exceptions, never bare except). All inherit
from ``ToolingError`` (a senex-tools-package-local base — there is no
project-wide ``SenexError`` in v1; mirrors the ``LMStudioError`` pattern
used in M3).
"""
from __future__ import annotations


class ToolingError(Exception):
    """Base for all senex.tools failures.

    Subclasses override ``kind`` to a stable string consumed by event mapping.
    Conventions §4: exception classes live at module top-level, named after
    the failure mode.
    """

    kind: str = "tooling_error"


class ToolInputInvalid(ToolingError):
    """Pydantic input validation failed inside ``ToolRegistry.dispatch``."""

    kind = "schema_invalid"


class PathOutsideRepo(ToolingError):
    """``validate_repo_path`` resolved to a path outside ``repo_root``."""

    kind = "path_rejected"


class SymlinkRefused(ToolingError):
    """``validate_repo_path`` encountered a symlinked path component."""

    kind = "path_rejected"


class RegexTooComplex(ToolingError):
    """``validate_regex_pattern`` rejected a pattern (length cap or compile fail)."""

    kind = "regex_invalid"


class RegexTimeoutExceeded(ToolingError):
    """``validate_regex_pattern`` test-match exceeded the safety budget."""

    kind = "regex_timeout"


class ToolDispatchFailed(ToolingError):
    """Uncaught handler failure (subprocess crash, IOError, malformed output)."""

    kind = "dispatch_failed"


class ToolUnavailable(ToolingError):
    """An external tool dependency is unreachable (e.g. claude-context MCP)."""

    kind = "unavailable"


class ToolBudgetExhausted(ToolingError):
    """``ToolLoop`` reached its ``max_calls`` budget (raised for control flow only).

    The loop emits the ``ToolBudgetExhausted`` *event* directly; this exception
    type exists so callers can distinguish budget exhaustion from other
    tooling failures if the loop is wrapped at a higher boundary.
    """

    kind = "budget_exhausted"


__all__ = [
    "PathOutsideRepo",
    "RegexTimeoutExceeded",
    "RegexTooComplex",
    "SymlinkRefused",
    "ToolBudgetExhausted",
    "ToolDispatchFailed",
    "ToolInputInvalid",
    "ToolUnavailable",
    "ToolingError",
]
