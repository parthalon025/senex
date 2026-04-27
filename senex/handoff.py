"""senex.handoff - claude-handoff.md writer (references-only per spec POL-2).

Per POL-2 the handoff REFERENCES audit-dir paths; it does NOT embed
finding `issue` / `why` / `fix` content. The top-3 list summarizes findings
as `[<PRIORITY>]   <file>:<line> -- <title>` only.

Defense-in-depth self-check: after rendering, the writer scans the output
for any of the top findings' body text and raises HandoffPolicyViolation if
it leaks. This catches programmer mistakes before they hit disk.
"""
from __future__ import annotations

from pathlib import Path

from senex.atomic_io import write_text_atomic
from senex.render_models import FindingRecord, RunMetadata
from senex.secret_redactor import SecretRedactor

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2, "healthy": 3}


class HandoffPolicyViolation(RuntimeError):
    """Raised when a rendered handoff contains finding body text (issue/why/fix).

    Per spec POL-2 the handoff is REFERENCES-only; the per-file reports
    carry the full finding text. The writer self-checks the rendered string
    before writing to disk; this exception fires before any artifact lands.
    """


def write_handoff(
    audit_dir: Path,
    run_metadata: RunMetadata,
    top_findings: list[FindingRecord],
) -> Path:
    """Render + atomic-write `<audit_dir>/claude-handoff.md`.

    Args:
        audit_dir: target directory; `<audit_dir>/claude-handoff.md` is the
            output path.
        run_metadata: run-level stamps (repo, run_id_short, audit_dir, ...).
        top_findings: ALL findings sorted by priority/file/line; only the top
            3 (high+medium ranked) are summarized in the body.

    Returns:
        Path to the written `claude-handoff.md`.

    Raises:
        HandoffPolicyViolation: the rendered body contains finding
            issue/why/fix text (defense-in-depth against POL-2 regressions).
    """
    body = _build_handoff_body(audit_dir, run_metadata, top_findings)

    # Defense-in-depth self-check (POL-2). We compare against the actual
    # finding bodies, not just substrings, because an issue text could
    # legitimately overlap with a title.
    for f in top_findings[:3]:
        # Skip ultra-short or trivially-overlapping body fragments — the
        # check is meant to catch real leakage, not single-character coincidences.
        for body_field in (f.issue, f.why, f.fix):
            if len(body_field) >= 8 and body_field in body:
                raise HandoffPolicyViolation(
                    f"finding body text leaked into claude-handoff.md "
                    f"(finding {f.id}). Per spec POL-2 the handoff must be "
                    f"references-only."
                )

    redacted = SecretRedactor().redact(body)
    target = audit_dir / "claude-handoff.md"
    return write_text_atomic(target, redacted)


def _build_handoff_body(
    audit_dir: Path,
    run: RunMetadata,
    findings: list[FindingRecord],
) -> str:
    """Render the handoff body string. Pure; isolated for testability."""
    audit_dir_str = str(audit_dir).replace("\\", "/")
    findings_index = f"{audit_dir_str}/findings.json"
    per_file_glob = f"{audit_dir_str}/<relpath>.md"

    # Top 3 findings sorted by priority then line then file (per plan
    # task 7.6.1 test_handoff_top_3_uses_priority_then_line_order).
    top_3 = sorted(
        findings,
        key=lambda f: (
            PRIORITY_RANK.get(f.priority, 99),
            f.location.line_start or 0,
            f.file,
        ),
    )[:3]

    lines: list[str] = []
    lines.append(
        f"You are reviewing senex audit findings for {run.repo} from "
        f"{run.date} (run {run.run_id_short})."
    )
    lines.append("")
    lines.append(f"Audit dir:        {audit_dir_str}")
    lines.append(f"Findings index:   {findings_index}")
    lines.append(f"Per-file reports: {per_file_glob}")
    lines.append("")
    lines.append("For each finding in findings.json, decide:")
    lines.append("  - APPLY    -- implement the fix as specified")
    lines.append("  - MODIFY   -- implement an adjusted version (specify what changes)")
    lines.append("  - DISMISS  -- explain why this is not actionable")
    lines.append("  - DEFER    -- record as known issue but don't fix in this pass")
    lines.append("")
    lines.append("Process by priority: HIGH -> MEDIUM -> LOW.")
    lines.append("Run gitnexus_impact before any code changes.")
    lines.append("Open senex's per-file <file>.md for full context on each finding.")
    lines.append("")
    lines.append("Top findings (titles only -- full text in per-file reports):")
    for i, f in enumerate(top_3, start=1):
        prio_label = f.priority.upper()
        # Pad the bracketed priority + trailing spaces to 9 columns so
        # `[HIGH]`, `[MEDIUM]`, `[LOW]` all align: "[HIGH]   ", "[MEDIUM] ", "[LOW]    ".
        bracketed = f"[{prio_label}]"
        padded = bracketed + " " * max(1, 9 - len(bracketed))
        line_no = f.location.line_start
        loc = f"{f.file}:{line_no}" if line_no is not None else f.file
        lines.append(f"{i}. {padded}{loc} -- {f.title}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["HandoffPolicyViolation", "write_handoff"]
