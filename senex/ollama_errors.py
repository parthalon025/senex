"""senex.ollama_errors — named exception classes for the Ollama client.

Mirrors the LMS taxonomy (see ``lmstudio_errors.py``) so the per-file
recovery matrix in ``phases/file_audit.py`` stays structurally identical.
Each subclass carries an ``error_kind: str`` class attribute that
``auditor.py`` maps to a recovery path (``FileError.error_kind`` field).

Adds two ReAct-specific exceptions (``ReActParseFailed``,
``ReActLoopExceeded``) that surface from ``senex.react_loop`` and feed
into the same recovery matrix.

Per conventions §4: exceptions live at module top-level; named after the
failure mode; never bare except.
"""
from __future__ import annotations


class OllamaError(Exception):
    """Base for all Ollama client failures. Subclasses override ``error_kind``."""

    error_kind: str = "ollama_error"


class OllamaConnectionLost(OllamaError):
    """Underlying TCP connection dropped or refused; re-probe required.

    Only this exception (and ``KeyboardInterrupt``) propagate above the per-file
    boundary in ``auditor.py``; all other ``OllamaError`` subclasses are
    recoverable per-file.
    """

    error_kind = "connection_lost"


class OllamaResponseSchemaInvalid(OllamaError):
    """Final response was valid JSON but did not match the audit schema."""

    error_kind = "schema_invalid"


class OllamaResponseInvalidJSON(OllamaError):
    """Final response could not be parsed as JSON (after ``<think>`` strip)."""

    error_kind = "invalid_json"


class TokenBudgetExceeded(OllamaError):
    """Pre-call ``count_tokens()`` exceeded ``token_budget_pct * context_window``."""

    error_kind = "token_budget_exceeded"


class FormatNegotiationFailed(OllamaError):
    """Both ``format=<jsonschema>`` and ``format="json"`` paths failed.

    Equivalent to LMS's ``SchemaNegotiationFailed`` but named for Ollama's
    ``format`` parameter rather than ``response_format``.
    """

    error_kind = "format_negotiation_failed"


class FingerprintChanged(OllamaError):
    """Per-call fingerprint differs from the runlock-pinned fingerprint."""

    error_kind = "fingerprint_changed"


class ThinkingTokensExceeded(OllamaError):
    """Reasoning stream exceeded ``[ollama.thinking].max_thinking_tokens``."""

    error_kind = "thinking_tokens_exceeded"


class ReActParseFailed(OllamaError):
    """ReAct parser could not extract Thought/Action/Final Answer from output.

    Raised by ``senex.react_loop`` after one auto-correction injection has
    already failed. Recovery: per-file ``<file>.ERROR.md``.
    """

    error_kind = "react_parse_failed"


class ReActLoopExceeded(OllamaError):
    """ReAct loop hit ``max_calls_per_file`` without emitting a Final Answer."""

    error_kind = "react_loop_exceeded"


__all__ = [
    "FingerprintChanged",
    "FormatNegotiationFailed",
    "OllamaConnectionLost",
    "OllamaError",
    "OllamaResponseInvalidJSON",
    "OllamaResponseSchemaInvalid",
    "ReActLoopExceeded",
    "ReActParseFailed",
    "ThinkingTokensExceeded",
    "TokenBudgetExceeded",
]
