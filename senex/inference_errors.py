"""senex.inference_errors — named exception classes for the LM Studio client.

Implements spec §8.2 (per-file recovery taxonomy) and §M3 plan exception list.
Each subclass carries an ``error_kind: str`` class attribute that ``auditor.py``
maps to a recovery path (`FileError.error_kind` field).

Per conventions §4: exceptions live at module top-level; named after the
failure mode; never bare except.
"""
from __future__ import annotations


class LMStudioError(Exception):
    """Base for all LM Studio client failures. Subclasses override ``error_kind``."""

    error_kind: str = "lmstudio_error"


class LMSConnectionLost(LMStudioError):
    """Underlying TCP connection dropped or refused; re-probe required.

    Only this exception (and ``KeyboardInterrupt``) propagate above the per-file
    boundary in M8 ``auditor.py``; all other ``LMStudioError`` subclasses are
    recoverable per-file.
    """

    error_kind = "connection_lost"


class LMSResponseSchemaInvalid(LMStudioError):
    """Final response was valid JSON but did not match the audit schema."""

    error_kind = "schema_invalid"


class LMSResponseInvalidJSON(LMStudioError):
    """Final response could not be parsed as JSON (after ``<think>`` strip)."""

    error_kind = "invalid_json"


class TokenBudgetExceeded(LMStudioError):
    """Pre-call ``count_tokens()`` exceeded ``token_budget_pct * context_window``."""

    error_kind = "token_budget_exceeded"


class SchemaNegotiationFailed(LMStudioError):
    """Both ``json_schema`` and ``json_object`` paths failed."""

    error_kind = "schema_negotiation_failed"


class FingerprintChanged(LMStudioError):
    """Per-call fingerprint differs from the runlock-pinned fingerprint."""

    error_kind = "fingerprint_changed"


class ThinkingTokensExceeded(LMStudioError):
    """Reasoning stream exceeded ``[lmstudio.thinking].max_thinking_tokens``."""

    error_kind = "thinking_tokens_exceeded"


__all__ = [
    "FingerprintChanged",
    "LMSConnectionLost",
    "LMSResponseInvalidJSON",
    "LMSResponseSchemaInvalid",
    "LMStudioError",
    "SchemaNegotiationFailed",
    "ThinkingTokensExceeded",
    "TokenBudgetExceeded",
]
