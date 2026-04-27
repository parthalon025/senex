"""senex.tools — Tool framework: registry, safety, dispatch loop, 6 tools.

Implements spec §5.11 (tool framework), §5.11.1 (v1 tool set), §5.11.2
(per-lens packs), §5.11.3 (loop semantics), §5.11.4 (tool safety), §SEC-2
(prompt-injection trust boundary), §SEC-7 (ANSI strip on outputs), §11.1
(threat model — ReDoS, traversal, exfiltration).

Re-exports the public API. Internal modules import via explicit relative
paths (conventions §1).

Architectural intent: the tool framework is the largest external attack
surface of senex (subprocesses, attacker-influenced filesystem reads). The
``safety`` primitives are the choke point used by every handler. ``loop``
is the bounded iteration controller that wraps ``LMStudioClient.chat()``
across multiple round-trips (M3 ↔ M5 reconciliation; see ``loop.py``).
"""
from __future__ import annotations

from .context import ToolContext
from .exceptions import (
    PathOutsideRepo,
    RegexTimeoutExceeded,
    RegexTooComplex,
    SymlinkRefused,
    ToolBudgetExhausted,
    ToolDispatchFailed,
    ToolingError,
    ToolInputInvalid,
    ToolUnavailable,
)
from .registry import ToolError, ToolRegistry, ToolResult
from .safety import (
    redact_tool_result,
    strip_ansi,
    validate_regex_pattern,
    validate_repo_path,
)

__all__ = [
    "PathOutsideRepo",
    "RegexTimeoutExceeded",
    "RegexTooComplex",
    "SymlinkRefused",
    "ToolBudgetExhausted",
    "ToolContext",
    "ToolDispatchFailed",
    "ToolError",
    "ToolInputInvalid",
    "ToolRegistry",
    "ToolResult",
    "ToolUnavailable",
    "ToolingError",
    "redact_tool_result",
    "strip_ansi",
    "validate_regex_pattern",
    "validate_repo_path",
]
