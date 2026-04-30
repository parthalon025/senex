"""senex.renderer - structured AuditResponse -> human-facing markdown.

Implements spec sections 7.1 (per-file report), 7.2 (combined report),
ARCH-13 (renderer crashes are non-fatal except disk-fatal).

Pure rendering (no I/O) lives in `Renderer.render_file()` and
`Renderer.render_combined()`; the side-effecting `Renderer.write_file_atomic`
delegates to `senex.atomic_io.write_text_atomic` (Task 7.7) so the byte-match
golden test can run with no filesystem access.

Convention notes:
- section 5.10: SecretRedactor applied as the LAST step before returning
  the rendered string.
- POL: every cell in tables AND every finding title is pipe-escaped.
- section 8 of conventions: UTF-8, LF line endings; the renderer joins on "\\n"
  and the writer encodes with utf-8 (no BOM).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from senex.render_models import (
    ErroredRecord,
    FileMetadata,
    FindingRecord,
    RunMetadata,
    SkippedRecord,
    Theme,
)
from senex.secret_redactor import SecretRedactor

PRIORITY_LETTER = {"high": "A", "medium": "B", "low": "C"}
PRIORITY_LABEL = {"high": "High Priority", "medium": "Medium Priority", "low": "Low Priority"}
PRIORITY_ORDER = ("high", "medium", "low", "healthy")


class RenderError(Exception):
    """Renderer-internal failure. Caught at the auditor boundary; produces
    `<file>.RENDER_ERROR.md`. NOT run-killing per spec ARCH-13.
    """


def _normalize_newlines(text: str) -> str:
    """Convert CRLF / CR to LF so Windows-origin fixtures don't thrash byte-match."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _escape_pipe(cell: str) -> str:
    """Escape literal `|` as `\\|` for safe insertion into markdown table cells."""
    return cell.replace("|", "\\|")


def _format_location(location: dict[str, Any]) -> str:
    """Return the spec section 7.1 Location bullet body.

    Examples:
        {"line_start": 142, "line_end": 156, "symbol": "rollback_transaction"} ->
            "L142-L156, `rollback_transaction`"
        {"line_start": 312} -> "L312"
        {"symbol": "_apply_batch"} -> "`_apply_batch`"
    """
    line_start = location.get("line_start")
    line_end = location.get("line_end")
    symbol = location.get("symbol")
    parts: list[str] = []
    if line_start is not None:
        if line_end is not None and line_end != line_start:
            parts.append(f"L{line_start}-L{line_end}")
        else:
            parts.append(f"L{line_start}")
    if symbol:
        parts.append(f"`{symbol}`")
    return ", ".join(parts)


def _sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort: priority bucket -> (file, line_start, title)."""

    def key(f: dict[str, Any]) -> tuple[int, int, str]:
        prio = f.get("priority", "healthy")
        prio_rank = PRIORITY_ORDER.index(prio) if prio in PRIORITY_ORDER else 99
        line = f.get("location", {}).get("line_start") or 0
        title = f.get("title", "")
        return (prio_rank, line, title)

    return sorted(findings, key=key)


class Renderer:
    """Pure-rendering + atomic-writer for per-file and combined reports."""

    def __init__(self, output_root: Path, redactor: SecretRedactor) -> None:
        self._output_root = output_root
        self._redactor = redactor

    # --- per-file ------------------------------------------------------------

    def render_file(self, response: dict[str, Any], file_metadata: FileMetadata) -> str:
        """Render an AuditResponse dict into a single markdown string.

        Pure: no disk I/O, no event emission. Caller is responsible for
        validating the response against `audit_response.schema.json` BEFORE
        calling this (spec SEC-10).

        Args:
            response: validated AuditResponse dict.
            file_metadata: per-file render-time stamps.

        Returns:
            Rendered markdown, secret-redacted, LF line endings.

        Raises:
            RenderError: when the validated response shape unexpectedly fails
                renderer assumptions (e.g. missing required key after schema
                validation should be impossible).
        """
        try:
            body = self._render_file_body(response, file_metadata)
        except (KeyError, TypeError, ValueError) as exc:
            raise RenderError(f"renderer failed on {file_metadata.relpath}: {exc}") from exc
        return self._redactor.redact(body)

    def _render_file_body(
        self, response: dict[str, Any], meta: FileMetadata
    ) -> str:
        lines: list[str] = []

        # Header.
        lines.append(f"# Audit: {meta.relpath}")
        lines.append("")
        lines.append(
            f"**Date:** {meta.date}  **Run ID:** {meta.run_id_short}  "
            f"**Model:** {meta.model_id}  **Lens:** {meta.lens_name}"
        )
        thinking_int = int(meta.thinking_seconds)
        output_int = int(meta.output_seconds)
        latency_int = thinking_int + output_int
        lines.append(
            f"**Tokens in/out:** {meta.prompt_tokens} / {meta.completion_tokens}  "
            f"**Latency:** {latency_int}s "
            f"(thinking {thinking_int}s, output {output_int}s)"
        )
        # Tools line.
        tools_total = sum(t.count for t in meta.tools_used)
        # spec example uses `x` not Unicode multiplier so the byte-match holds on
        # plain ASCII terminals; this is intentional.
        tool_parts = ", ".join(f"`{t.name}`x{t.count}" for t in meta.tools_used)
        if tool_parts:
            lines.append(
                f"**Tools used:** {tools_total} / {meta.tools_max} ({tool_parts})  "
                f"**Compactions:** {meta.compactions_used} / {meta.compactions_max}"
            )
        else:
            lines.append(
                f"**Tools used:** {tools_total} / {meta.tools_max}  "
                f"**Compactions:** {meta.compactions_used} / {meta.compactions_max}"
            )
        lines.append(f"**GitNexus context:** {meta.graph_context_summary}")
        lines.append("")

        # Overall assessment paragraph.
        assessment = _normalize_newlines(response.get("overall_assessment", ""))
        lines.append(assessment)
        lines.append("")

        lines.append("---")
        lines.append("")

        lines.append("## Detailed Audit Findings")
        lines.append("")

        findings = _sort_findings(list(response.get("findings", [])))
        # Within each priority bucket, the FIRST finding's category drives the
        # section heading (per spec section 7.1: A=high, B=medium, C=low). The bullet
        # list under it can contain multiple findings of that priority.
        by_prio: dict[str, list[dict[str, Any]]] = {p: [] for p in PRIORITY_ORDER}
        for f in findings:
            prio = f.get("priority", "healthy")
            by_prio.setdefault(prio, []).append(f)

        for prio in ("high", "medium", "low"):
            bucket = by_prio.get(prio, [])
            if not bucket:
                continue
            letter = PRIORITY_LETTER[prio]
            label = PRIORITY_LABEL[prio]
            category = _escape_pipe(_normalize_newlines(bucket[0].get("category", "")))
            lines.append(f"### {letter}. {category} ({label})")
            for f in bucket:
                self._append_finding_bullet(lines, f)
            lines.append("")

        healthy = by_prio.get("healthy", [])
        if healthy:
            lines.append("### Healthy")
            for f in healthy:
                title = _escape_pipe(_normalize_newlines(f.get("title", "")))
                issue = _normalize_newlines(f.get("issue", ""))
                lines.append(f"- **{title}** - {issue}")
            lines.append("")

        lines.append("---")
        lines.append("")

        lines.append("## Recommendations")
        lines.append("")

        recommendations = response.get("recommendations", [])
        for i, rec in enumerate(recommendations, start=1):
            title = _normalize_newlines(rec.get("title", ""))
            rationale = _normalize_newlines(rec.get("rationale", ""))
            lines.append(f"#### Recommendation {i}: {title}")
            lines.append(rationale)
            snippet = rec.get("code_snippet")
            if snippet:
                language = rec.get("language") or meta.language
                lines.append("")
                lines.append(f"```{language}")
                lines.append(_normalize_newlines(snippet))
                lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append("")

        lines.append("### Summary of Best Practices Applied")
        lines.append("| Feature | Original Code | Recommended |")
        lines.append("|---|---|---|")
        for row in response.get("best_practices_table", []):
            feature = _escape_pipe(_normalize_newlines(row.get("feature", "")))
            original = _escape_pipe(_normalize_newlines(row.get("original", "")))
            recommended = _escape_pipe(_normalize_newlines(row.get("recommended", "")))
            lines.append(f"| {feature} | {original} | {recommended} |")

        return "\n".join(lines) + "\n"

    def _append_finding_bullet(
        self, lines: list[str], finding: dict[str, Any]
    ) -> None:
        title = _escape_pipe(_normalize_newlines(finding.get("title", "")))
        issue = _normalize_newlines(finding.get("issue", ""))
        why = _normalize_newlines(finding.get("why", ""))
        fix = _normalize_newlines(finding.get("fix", ""))
        confidence = finding.get("confidence", "")
        location = _format_location(finding.get("location", {}))
        lines.append(f"- **{title}**")
        lines.append(f"  - **Issue:** {issue}")
        lines.append(f"  - **Why:** {why}")
        lines.append(f"  - **Fix:** {fix}")
        lines.append(f"  - **Confidence:** {confidence}")
        lines.append(f"  - **Location:** {location}")

    # --- combined ------------------------------------------------------------

    def render_combined(
        self,
        run_metadata: RunMetadata,
        findings: list[FindingRecord],
        themes: list[Theme] | None,
        skipped: list[SkippedRecord],
        errored: list[ErroredRecord],
    ) -> str:
        """Render combined.md per spec section 7.2. Pure; no I/O."""
        try:
            body = self._render_combined_body(
                run_metadata, findings, themes, skipped, errored
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RenderError(f"combined renderer failed: {exc}") from exc
        return self._redactor.redact(body)

    def _render_combined_body(
        self,
        run: RunMetadata,
        findings: list[FindingRecord],
        themes: list[Theme] | None,
        skipped: list[SkippedRecord],
        errored: list[ErroredRecord],
    ) -> str:
        lines: list[str] = []
        lines.append(f"# senex Audit: {run.repo} {run.date}")
        lines.append("")
        lines.append(
            f"**Files audited:** {run.files_audited}  "
            f"**Skipped:** {run.files_skipped}  **Errored:** {run.files_errored}"
        )
        lines.append(
            f"**Duration:** {run.duration_seconds}s  **Model:** {run.model}"
        )
        lines.append(
            f"**Prompt hash:** {run.prompt_hash}  **Config hash:** {run.config_hash}"
        )
        lines.append(f"**Run ID:** {run.run_id}  **Started:** {run.started_at}")
        lines.append(f"**Lens:** {run.lens} (v{run.lens_version})")
        lines.append("")

        # Priority rollup.
        totals = {p: 0 for p in PRIORITY_ORDER}
        for f in findings:
            totals[f.priority] = totals.get(f.priority, 0) + 1
        lines.append("## Priority Rollup")
        lines.append(f"- HIGH: {totals['high']}")
        lines.append(f"- MEDIUM: {totals['medium']}")
        lines.append(f"- LOW: {totals['low']}")
        lines.append(f"- HEALTHY: {totals['healthy']}")
        lines.append("")

        # Themes.
        lines.append("## Cross-Cutting Themes")
        if themes is None:
            lines.append("[cross-cutting themes unavailable]")
        elif not themes:
            lines.append("(no cross-cutting themes detected)")
        else:
            for i, theme in enumerate(themes, start=1):
                title = _normalize_newlines(theme.title)
                desc = _normalize_newlines(theme.description)
                affected = ", ".join(theme.affected_files)
                lines.append(
                    f"{i}. **{title}** ({theme.priority}, "
                    f"confidence={theme.confidence}) - {len(theme.affected_files)} "
                    f"file(s) affected"
                )
                lines.append(f"   {desc}")
                if affected:
                    lines.append(f"   Affected: {affected}")
        lines.append("")

        # Top findings table.
        lines.append("## Top Findings (sorted by priority)")
        lines.append("| File | Priority | Title | Link |")
        lines.append("|---|---|---|---|")
        sorted_findings = sorted(
            findings,
            key=lambda f: (
                PRIORITY_ORDER.index(f.priority) if f.priority in PRIORITY_ORDER else 99,
                f.file,
                f.location.line_start or 0,
            ),
        )
        for f in sorted_findings:
            file_loc = f.file
            if f.location.line_start is not None:
                file_loc = f"{f.file}:{f.location.line_start}"
            title = _escape_pipe(_normalize_newlines(f.title))
            file_loc = _escape_pipe(file_loc)
            lines.append(
                f"| {file_loc} | {f.priority.upper()} | {title} | "
                f"[report]({f.report_path}) |"
            )
        lines.append("")

        # Files with no findings.
        lines.append("## Files With No Findings")
        if run.files_with_no_findings:
            for path in run.files_with_no_findings:
                lines.append(f"- {path}")
        else:
            lines.append("(none)")
        lines.append("")

        # Skipped.
        lines.append("## Skipped Files")
        if skipped:
            for s in skipped:
                lines.append(f"- {s.relpath}: {s.reason}")
        else:
            lines.append("(none)")
        lines.append("")

        # Errored.
        lines.append("## Errored Files")
        if errored:
            for e in errored:
                lines.append(f"- {e.relpath} ({e.kind}): {e.error_message}")
        else:
            lines.append("(none)")
        lines.append("")

        # Run metadata.
        lines.append("## Run Metadata")
        lines.append(f"- Model fingerprint: {run.model_fingerprint}")
        lines.append(f"- GitNexus index hash: {run.gitnexus_index_hash}")
        lines.append(f"- Tool pack hash: {run.tool_pack_hash}")
        lines.append(f"- Audit dir: {run.audit_dir}")
        lines.append(
            f"- Totals: high={totals['high']}, medium={totals['medium']}, "
            f"low={totals['low']}, healthy={totals['healthy']}, "
            f"files={run.files_audited}"
        )

        return "\n".join(lines) + "\n"

    # --- writer --------------------------------------------------------------

    def write_file_atomic(
        self, audit_dir: Path, relpath: str, markdown: str
    ) -> Path:
        """Write `<audit_dir>/<relpath>.md` via atomic tmp+fsync+rename.

        Delegates to senex.atomic_io.write_text_atomic (Task 7.7).
        """
        from senex.atomic_io import write_text_atomic

        # spec section 7 layout: per-file md is `<audit_dir>/<relpath>.md`.
        # mkdir parents to permit nested relpaths (e.g. store/sqlite.py.md).
        target = audit_dir / f"{relpath}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        return write_text_atomic(target, markdown)


__all__ = ["Renderer", "RenderError"]
