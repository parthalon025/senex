"""senex.tools — Tool framework: registry, safety, dispatch loop, 9 tools.

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
from .pack_hash import compute_tool_pack_hash
from .registry import ToolError, ToolRegistry, ToolResult
from .safety import (
    redact_tool_result,
    strip_ansi,
    validate_regex_pattern,
    validate_repo_path,
)


def register_default_tools(registry: ToolRegistry) -> None:
    """Register the v1 9-tool default pack with ``registry``.

    Calls each tool module's per-tool register_<name>() function. After
    this, the registry can serve any of the 9 tools the correctness lens
    (and any future lens) declares in its ``tools.toml``.

    Spec §5.11.1 lists the v1 tool set; conventions §13 documents the
    side-effect-free import convention so this helper is the explicit
    wiring point. Imports are local to defer subprocess-related imports
    until the function is actually called (e.g. preflight skips this on
    --no-tui paths that never invoke tools).
    """
    from . import (
        gitnexus_context,
        gitnexus_impact,
        gitnexus_query,
        grep,
        list_dir,
        list_symbols,
        read_file,
        run_semgrep,
        search_code,
    )

    gitnexus_query.register_gitnexus_query(registry)
    gitnexus_context.register_gitnexus_context(registry)
    gitnexus_impact.register_gitnexus_impact(registry)
    read_file.register_read_file(registry)
    grep.register_grep(registry)
    search_code.register_search_code(registry)
    list_dir.register_list_dir(registry)
    list_symbols.register_list_symbols(registry)
    run_semgrep.register_run_semgrep(registry)


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
    "compute_tool_pack_hash",
    "redact_tool_result",
    "register_default_tools",
    "strip_ansi",
    "validate_regex_pattern",
    "validate_repo_path",
]
