"""Unit tests for Renderer.render_combined."""
from __future__ import annotations

from pathlib import Path

from senex.render_models import (
    ErroredRecord,
    FindingRecord,
    LocationRecord,
    RunMetadata,
    SkippedRecord,
    Theme,
)
from senex.renderer import Renderer
from senex.secret_redactor import SecretRedactor


def _renderer() -> Renderer:
    return Renderer(output_root=Path("/tmp/x"), redactor=SecretRedactor())


def _run(*, totals_files: int = 87) -> RunMetadata:
    return RunMetadata(
        repo="senex",
        run_id="01JZ3K7B9C8DQRS4M2EXAMPLE",
        run_id_short="01jz3k7b",
        audit_dir="E:/senex-audits/senex/2026-04-26-01jz3k7b",
        model="google/gemma-4-26b-a4b",
        model_fingerprint="sha256:fingerprint12345",
        lens="correctness",
        lens_version="1.0.0",
        started_at="2026-04-26T22:14:03Z",
        duration_seconds=8044.0,
        config_hash="sha256:cfgcfgcfg",
        prompt_hash="sha256:promptpromptprompt",
        tool_pack_hash="sha256:toolpack",
        gitnexus_index_hash="sha256:gitnexus",
        files_audited=totals_files,
        files_skipped=4,
        files_errored=1,
        files_with_no_findings=["clean/a.py", "clean/b.py"],
        date="2026-04-26",
    )


def _finding(
    *,
    fid: str = "f-aaaaaaaaaaaa",
    file: str = "x.py",
    priority: str = "medium",
    title: str = "t",
    line: int = 10,
) -> FindingRecord:
    return FindingRecord(
        id=fid,
        file=file,
        category="cat",
        priority=priority,  # type: ignore[arg-type]
        title=title,
        issue="i",
        why="w",
        fix="f",
        confidence="medium",
        location=LocationRecord(line_start=line),
        report_path=f"{file}.md",
        suppressed=False,
        prompt_hash="sha256:p",
        config_hash="sha256:c",
        model_fingerprint="sha256:m",
        lens_version="1.0.0",
    )


def test_render_combined_includes_every_run_metadata_field() -> None:
    rendered = _renderer().render_combined(
        run_metadata=_run(),
        findings=[],
        themes=[],
        skipped=[],
        errored=[],
    )
    run = _run()
    must_appear = [
        run.repo,
        run.run_id,
        run.audit_dir,
        run.model,
        run.model_fingerprint,
        run.lens,
        run.lens_version,
        run.started_at,
        str(run.duration_seconds),
        run.config_hash,
        run.prompt_hash,
        run.tool_pack_hash,
        run.gitnexus_index_hash,
        str(run.files_audited),
        str(run.files_skipped),
        str(run.files_errored),
        run.date,
    ]
    for fragment in must_appear:
        assert fragment in rendered, f"missing fragment in rendered combined: {fragment!r}"


def test_render_combined_priority_rollup_correct() -> None:
    findings = (
        [_finding(fid=f"f-1{i:011x}", priority="high", title=f"h{i}") for i in range(8)]
        + [_finding(fid=f"f-2{i:011x}", priority="medium", title=f"m{i}") for i in range(34)]
        + [_finding(fid=f"f-3{i:011x}", priority="low", title=f"l{i}") for i in range(56)]
        + [_finding(fid=f"f-4{i:011x}", priority="healthy", title=f"o{i}") for i in range(17)]
    )
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=findings, themes=[],
        skipped=[], errored=[],
    )
    assert "HIGH: 8" in rendered
    assert "MEDIUM: 34" in rendered
    assert "LOW: 56" in rendered
    assert "HEALTHY: 17" in rendered


def test_render_combined_themes_section_when_themes_present() -> None:
    themes = [
        Theme(
            id="t-aaaaaaaaaaaa",
            title="Repeated swallowed exceptions",
            description="The store layer swallows exceptions silently.",
            affected_files=["a.py", "b.py", "c.py"],
            priority="high",
            confidence="medium",
            recommended_action="Add explicit logging.",
        )
    ]
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=[], themes=themes,
        skipped=[], errored=[],
    )
    assert "Repeated swallowed exceptions" in rendered
    assert "The store layer swallows exceptions silently." in rendered


def test_render_combined_themes_unavailable_gap_when_themes_none() -> None:
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=[], themes=None,
        skipped=[], errored=[],
    )
    assert "[cross-cutting themes unavailable]" in rendered


def test_render_combined_top_findings_table_sorted() -> None:
    findings = [
        _finding(fid="f-100000000001", file="b.py", priority="medium", line=5, title="m1"),
        _finding(fid="f-100000000002", file="a.py", priority="high", line=20, title="h1"),
        _finding(fid="f-100000000003", file="a.py", priority="high", line=10, title="h2"),
    ]
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=findings, themes=[],
        skipped=[], errored=[],
    )
    # h2 (a.py:10) before h1 (a.py:20) before m1 (b.py:5).
    h2_pos = rendered.index("h2")
    h1_pos = rendered.index("h1")
    m1_pos = rendered.index("m1")
    assert h2_pos < h1_pos < m1_pos


def test_render_combined_top_findings_hyperlink_to_per_file_report() -> None:
    findings = [_finding(fid="f-100000000001", file="store/sqlite.py")]
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=findings, themes=[],
        skipped=[], errored=[],
    )
    assert "[report](store/sqlite.py.md)" in rendered


def test_render_combined_files_with_no_findings_section() -> None:
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=[], themes=[],
        skipped=[], errored=[],
    )
    assert "## Files With No Findings" in rendered
    assert "clean/a.py" in rendered
    assert "clean/b.py" in rendered


def test_render_combined_skipped_section_lists_reason() -> None:
    skipped = [
        SkippedRecord(relpath="big/dump.txt", reason="too large"),
        SkippedRecord(relpath="enc/binfile", reason="encoding error"),
    ]
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=[], themes=[], skipped=skipped, errored=[],
    )
    assert "big/dump.txt: too large" in rendered
    assert "enc/binfile: encoding error" in rendered


def test_render_combined_errored_section_lists_kind() -> None:
    errored = [
        ErroredRecord(relpath="bad.py", kind="schema_mismatch", error_message="failed"),
    ]
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=[], themes=[], skipped=[], errored=errored,
    )
    assert "bad.py" in rendered
    assert "schema_mismatch" in rendered


def test_render_combined_pipe_escapes_finding_titles_in_table() -> None:
    findings = [
        _finding(fid="f-100000000001", title="Has | a pipe", file="x.py"),
    ]
    rendered = _renderer().render_combined(
        run_metadata=_run(), findings=findings, themes=[], skipped=[], errored=[],
    )
    assert "Has \\| a pipe" in rendered
