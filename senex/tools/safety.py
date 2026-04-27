"""senex.tools.safety — path / regex / ANSI / redaction primitives.

Implements spec §5.11.4 (tool safety), §SEC-1 (path-safety rule), §SEC-7
(ANSI strip on textual outputs), §11.1 (threat model: traversal, ReDoS,
exfiltration).

Pipeline ordering (CRITICAL — used by ``redact_tool_result`` and every
tool handler):

    strip_ansi(s)  ->  redactor.redact(s)  ->  truncate(s, max_tokens)

The order matters. Redaction patterns assume ANSI-free input; running
redact before strip would let attacker-injected ANSI hide a key from the
redactor. Truncation runs last so the truncation marker is itself
ANSI-free and post-redaction.
"""
from __future__ import annotations

import re as _stdlib_re
from pathlib import Path

import regex  # type: ignore[import-untyped, unused-ignore]

from senex.secret_redactor import SecretRedactor

from .exceptions import (
    PathOutsideRepo,
    RegexTimeoutExceeded,
    RegexTooComplex,
    SymlinkRefused,
)

# §SEC-7: C0/C1 control chars (\x00-\x08, \x0b, \x0c, \x0e-\x1f, \x7f) plus
# ANSI CSI/OSC sequences. Whitespace (\t=\x09, \n=\x0a, \r=\x0d) intentionally
# preserved. The \x0c (form feed) and \x0b (vertical tab) are stripped — they
# are not used as whitespace in any senex-emitted artifact.
_ANSI_RE: regex.Pattern[str] = regex.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]"  # CSI (bracket-style)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC terminated by BEL or ST
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"  # C0/C1 controls (preserves \t\n\r)
)

# Pattern length cap from spec §5.11.4 / conventions §5.
_MAX_REGEX_LEN: int = 256
# Test-match timeout for the ReDoS heuristic (the ``regex`` library accepts
# a per-call ``timeout`` keyword that ``re`` lacks — second line of defense
# behind the syntactic checker below).
_REGEX_TEST_TIMEOUT_S: float = 0.1
# Synthetic input designed to ensure a sane regex still matches in
# microseconds. Used as the test-match probe.
_REDOS_PROBE: str = "a" * 30 + "X"
# Syntactic guard: nested-repetition shapes like ``(...+)+``, ``(...*)*``,
# ``(...{m,n})+`` are the canonical ReDoS hazards. The ``regex`` library
# uses an NFA that resists most catastrophic backtracking, but downstream
# Python users on stdlib ``re`` (or other backends) are still vulnerable;
# refuse the shape regardless. Pattern matches a closing ``)`` immediately
# preceded by a quantifier inside the group AND followed by another
# quantifier outside.
_REDOS_SHAPE_RE: regex.Pattern[str] = regex.compile(
    r"\([^)]*[*+?][^)]*\)\s*[*+?]"
)


def validate_repo_path(p: str | Path, repo_root: Path) -> Path:
    """Resolve ``p`` and verify it lives under ``repo_root`` with no symlinks.

    Args:
        p: relative or absolute path; if relative, joined onto ``repo_root``.
        repo_root: absolute, pre-resolved root the path must stay under.

    Returns:
        The resolved ``Path`` (guaranteed to be under ``repo_root``).

    Raises:
        PathOutsideRepo: resolved path escapes ``repo_root`` (``..``, UNC,
            drive-absolute outside repo).
        SymlinkRefused: any component of the resolved path (or any ancestor
            walked while resolving) is a symlink.
    """
    repo_root_resolved = repo_root.resolve()
    raw = Path(p)
    candidate = raw if raw.is_absolute() else (repo_root_resolved / raw)

    # Check for symlinks BEFORE resolving — ``Path.resolve()`` follows them
    # silently. Walk ancestors that exist; an ancestor symlink anywhere along
    # the chain is a refusal.
    for ancestor in [candidate, *candidate.parents]:
        try:
            if ancestor.is_symlink():
                raise SymlinkRefused(f"path component is a symlink: {ancestor}")
        except OSError:
            # Permission / nonexistent ancestor — keep going. The is_relative_to
            # check below is still authoritative for traversal rejection.
            continue

    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathOutsideRepo(f"cannot resolve path: {exc}") from exc

    try:
        # ``is_relative_to`` was added in 3.9; we require 3.11+.
        if not resolved.is_relative_to(repo_root_resolved):
            raise PathOutsideRepo(
                f"resolved path {resolved} escapes repo root {repo_root_resolved}"
            )
    except ValueError as exc:
        # On Windows, mismatched drive letters raise ValueError in 3.11.
        raise PathOutsideRepo(str(exc)) from exc

    return resolved


def validate_regex_pattern(
    pat: str,
    max_len: int = _MAX_REGEX_LEN,
    timeout_seconds: float = _REGEX_TEST_TIMEOUT_S,
) -> None:
    """Reject overlong / uncompilable / catastrophic-backtracking patterns.

    Args:
        pat: user-supplied regex source.
        max_len: maximum allowed pattern length (default 256).
        timeout_seconds: test-match budget for ReDoS detection (default 0.1).

    Raises:
        RegexTooComplex: pattern length exceeds ``max_len`` OR fails to
            compile under the ``regex`` library.
        RegexTimeoutExceeded: test-match against ``_REDOS_PROBE`` exceeded
            ``timeout_seconds`` (catastrophic backtracking heuristic).

    Returns:
        ``None`` on success.
    """
    if len(pat) > max_len:
        raise RegexTooComplex(
            f"pattern length {len(pat)} exceeds cap {max_len}"
        )
    # Syntactic ReDoS-shape rejection (first line of defense; the ``regex``
    # library's own engine is resistant but downstream consumers may not be).
    if _REDOS_SHAPE_RE.search(pat):
        raise RegexTooComplex(
            f"pattern shape resembles catastrophic-backtracking hazard: {pat!r}"
        )
    try:
        compiled = regex.compile(pat)
    except (regex.error, _stdlib_re.error) as exc:
        raise RegexTooComplex(f"pattern does not compile: {exc}") from exc

    # Second line of defense: timed test-match against a tiny probe. The
    # ``regex`` library accepts a per-call ``timeout`` keyword; stdlib ``re``
    # does not. A pattern that exceeds the budget on this probe is rejected.
    try:
        compiled.search(_REDOS_PROBE, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise RegexTimeoutExceeded(
            f"pattern test-match exceeded {timeout_seconds}s budget: {exc}"
        ) from exc
    return None


def strip_ansi(s: str) -> str:
    """Remove C0/C1 controls + ANSI CSI/OSC sequences; preserve ``\\t\\n\\r``.

    §SEC-7 mandates this on every string flowing from LLM/tool output to
    persisted markdown or TUI widgets. The compiled pattern is module-level
    cached (conventions §14: cache compiled regex once).
    """
    if not s:
        return s
    out: str = _ANSI_RE.sub("", s)
    return out


def redact_tool_result(s: str, redactor: SecretRedactor) -> str:
    """Apply ANSI strip THEN secret redaction (mandatory ordering).

    See module docstring for why ordering matters.
    """
    return redactor.redact(strip_ansi(s))
