"""senex.secret_redactor — regex-based redaction for persisted strings.

Implements spec §5.10 and threat-model row §11.1 (secret leakage).
Patterns are ordered most-specific-first so PEM blocks win over generic
env-style matches (see test_redactor_priority_pem_before_env).
"""
from __future__ import annotations

from copy import deepcopy
from fnmatch import fnmatchcase
from typing import Any

import regex as re  # type: ignore[import-untyped]  # conventions §5: timeout-capable regex

_PATTERNS: tuple[tuple[str, str], ...] = (
    # PEM block (most specific — multi-line).
    ("pem", r"-----BEGIN [A-Z ]+-----[\s\S]*?-----END [A-Z ]+-----"),
    # JWT: three base64url segments.
    ("jwt", r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]{20,}\b"),
    # AWS access key id.
    ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
    # GitHub PAT (classic + OAuth).
    ("github_pat", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    # OpenAI / Anthropic style llm api key.
    ("llm_api_key", r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b"),
    # Generic env-style: KEY_WITH_SECRET=value. Value must not start with '['
    # so that an already-emitted [REDACTED:...] marker (from a prior pattern)
    # is not greedily consumed (see test_redactor_priority_pem_before_env).
    (
        "env_secret",
        r"\b(?P<key>[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|KEY|PASS))\s*=\s*[^\s\[\"'`]+",
    ),
)

_SENSITIVE_KEY_GLOBS: tuple[str, ...] = (
    "api_key", "*_token", "*_secret", "password*", "*_key",
)


class SecretRedactor:
    """Apply named regex patterns to redact secret-shaped strings.

    Pattern compile is done once at init (conventions §14: cache compiled regexes).
    """

    def __init__(self, sensitive_key_globs: tuple[str, ...] = _SENSITIVE_KEY_GLOBS) -> None:
        self._compiled: list[tuple[str, re.Pattern[str]]] = [
            (name, re.compile(pat, flags=re.MULTILINE))
            for name, pat in _PATTERNS
        ]
        self._sensitive_globs = sensitive_key_globs

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        for name, pat in self._compiled:
            out = pat.sub(f"[REDACTED:{name}]", out)
        return out

    def redact_dict(
        self, d: dict[str, Any], keys: tuple[str, ...] | None = None
    ) -> dict[str, Any]:
        """Return a deep-copied dict with sensitive-named keys replaced by ``[REDACTED]``.

        Args:
            d: input dict (untouched; deep-copied first).
            keys: glob list of sensitive key names (defaults to module-level globs).

        Returns:
            new dict with sensitive values replaced; non-sensitive entries pass through.
        """
        globs = keys if keys is not None else self._sensitive_globs
        out = deepcopy(d)
        self._scrub(out, globs)
        return out

    def _scrub(self, node: Any, globs: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if isinstance(k, str) and any(fnmatchcase(k, g) for g in globs):
                    node[k] = "[REDACTED]"
                else:
                    self._scrub(v, globs)
        elif isinstance(node, list):
            for item in node:
                self._scrub(item, globs)
