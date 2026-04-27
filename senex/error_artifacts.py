"""senex.error_artifacts - per-file recovery writers (spec section 8.2 + ARCH-13).

Four writers, each producing one of:
    <relpath>.ERROR.md         - retried+failed audit (HTTP 4xx/5xx, schema mismatch)
    <relpath>.SKIPPED.md       - skipped file (read error, too large, token budget)
    <relpath>.RAW.json         - raw model response when validation failed
    <relpath>.RENDER_ERROR.md  - renderer crash on a validated response (ARCH-13)

All four:
    - apply SecretRedactor to the body before writing
    - write atomically via senex.atomic_io.write_text_atomic
    - mkdir(parents=True) for nested relpaths
    - return the final on-disk path
"""
from __future__ import annotations

from pathlib import Path

from senex.atomic_io import write_text_atomic
from senex.secret_redactor import SecretRedactor

# Module-level singleton: the redactor is stateless after init (compiled
# regex cache only); per spec section 5.10 we apply it at the rendered-string
# boundary. Phases / auditor inject their own redactor when needed; this
# default keeps the writers usable as standalone helpers.
_REDACTOR = SecretRedactor()


def write_error_artifact(
    *,
    audit_dir: Path,
    relpath: str,
    kind: str,
    error_message: str,
    traceback: str,
) -> Path:
    """Write `<audit_dir>/<relpath>.ERROR.md` per spec section 8.2."""
    body = (
        f"# senex Audit Error: {relpath}\n"
        f"\n"
        f"**Kind:** {kind}\n"
        f"\n"
        f"## Error Message\n"
        f"\n"
        f"{error_message}\n"
        f"\n"
        f"## Traceback\n"
        f"\n"
        f"```\n"
        f"{traceback}\n"
        f"```\n"
    )
    redacted = _REDACTOR.redact(body)
    target = audit_dir / f"{relpath}.ERROR.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    return write_text_atomic(target, redacted)


def write_skipped_artifact(
    *,
    audit_dir: Path,
    relpath: str,
    reason: str,
) -> Path:
    """Write `<audit_dir>/<relpath>.SKIPPED.md` per spec section 8.2."""
    body = (
        f"# senex Audit Skipped: {relpath}\n"
        f"\n"
        f"**Reason:** {reason}\n"
    )
    redacted = _REDACTOR.redact(body)
    target = audit_dir / f"{relpath}.SKIPPED.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    return write_text_atomic(target, redacted)


def write_raw_response(
    *,
    audit_dir: Path,
    relpath: str,
    raw_json: str,
) -> Path:
    """Write `<audit_dir>/<relpath>.RAW.json` preserving raw response bytes.

    Per plan task 7.8: no parse-rewrite. The whole point of RAW.json is to
    preserve the model's exact output for debugging schema-validation
    failures. Redaction is applied (the raw JSON can carry secrets the model
    parroted from a tool result) but the JSON structure is otherwise
    unchanged.
    """
    redacted = _REDACTOR.redact(raw_json)
    target = audit_dir / f"{relpath}.RAW.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    return write_text_atomic(target, redacted)


def write_render_error_artifact(
    *,
    audit_dir: Path,
    relpath: str,
    traceback: str,
    response_dump: str,
) -> Path:
    """Write `<audit_dir>/<relpath>.RENDER_ERROR.md` per spec ARCH-13.

    Renderer crashes on a validated response are NOT run-killing; this
    artifact captures the traceback + the response dump so the bug can be
    reproduced. The auditor continues with the next file.
    """
    body = (
        f"# senex Renderer Crash: {relpath}\n"
        f"\n"
        f"The renderer raised on a validated AuditResponse. The run "
        f"continued with the next file. See spec section ARCH-13.\n"
        f"\n"
        f"## Traceback\n"
        f"\n"
        f"```\n"
        f"{traceback}\n"
        f"```\n"
        f"\n"
        f"## Validated response that triggered the crash\n"
        f"\n"
        f"```json\n"
        f"{response_dump}\n"
        f"```\n"
    )
    redacted = _REDACTOR.redact(body)
    target = audit_dir / f"{relpath}.RENDER_ERROR.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    return write_text_atomic(target, redacted)


__all__ = [
    "write_error_artifact",
    "write_skipped_artifact",
    "write_raw_response",
    "write_render_error_artifact",
]
