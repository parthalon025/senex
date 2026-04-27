"""Unit tests for senex.findings_aggregator."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from senex.findings_aggregator import (
    Aggregator,
    AggregatorInputMissing,
)
from senex.findings_partial import FindingsPartialWriter
from senex.render_models import (
    FindingRecord,
    LocationRecord,
    RunMetadata,
    Theme,
)

FINDINGS_INDEX_SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent.parent / "senex" / "schema" / "findings_index.schema.json").read_text(encoding="utf-8")
)


def _run_metadata() -> RunMetadata:
    return RunMetadata(
        repo="senex",
        run_id="01JZ3K7B9C8DQRS4M2EXAMPLE",
        run_id_short="01jz3k7b",
        audit_dir="E:/senex-audits/senex/2026-04-26-01jz3k7b",
        model="google/gemma-4-26b-a4b",
        model_fingerprint="sha256:m",
        lens="correctness",
        lens_version="1.0.0",
        started_at="2026-04-26T22:14:03Z",
        duration_seconds=8044.0,
        config_hash="sha256:c",
        prompt_hash="sha256:p",
        tool_pack_hash="sha256:t",
        gitnexus_index_hash="sha256:g",
        files_audited=3,
        files_skipped=0,
        files_errored=0,
        files_with_no_findings=[],
        date="2026-04-26",
    )


def _finding(
    *,
    fid: str,
    file: str = "x.py",
    priority: str = "medium",
    title: str = "t",
    line: int = 10,
    issue: str = "i",
) -> FindingRecord:
    return FindingRecord(
        id=fid,
        file=file,
        category="cat",
        priority=priority,  # type: ignore[arg-type]
        title=title,
        issue=issue,
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


async def test_aggregate_dedup_by_id_last_wins(tmp_path: Path) -> None:
    """Three writes with the same id collapse to one finding with the LAST title."""
    fid = "f-deadbeef0001"
    with FindingsPartialWriter(tmp_path) as w:
        w.append(_finding(fid=fid, title="first"))
        w.append(_finding(fid=fid, title="second"))
        w.append(_finding(fid=fid, title="third"))

    agg = Aggregator()
    target = await agg.run(tmp_path, themes=None, run_metadata=_run_metadata())
    data = json.loads(target.read_text(encoding="utf-8"))

    assert len(data["findings"]) == 1
    assert data["findings"][0]["title"] == "third"


async def test_aggregate_sort_priority_then_file_then_line(tmp_path: Path) -> None:
    """high before medium before low before healthy; alphabetical by file; ascending by line."""
    findings = [
        _finding(fid="f-000000000001", file="b.py", priority="medium", line=5, title="m1"),
        _finding(fid="f-000000000002", file="a.py", priority="high", line=20, title="h1"),
        _finding(fid="f-000000000003", file="a.py", priority="high", line=10, title="h2"),
        _finding(fid="f-000000000004", file="a.py", priority="low", line=1, title="l1"),
        _finding(fid="f-000000000005", file="z.py", priority="healthy", line=1, title="ok"),
    ]
    with FindingsPartialWriter(tmp_path) as w:
        for f in findings:
            w.append(f)

    target = await Aggregator().run(tmp_path, themes=None, run_metadata=_run_metadata())
    data = json.loads(target.read_text(encoding="utf-8"))
    titles = [f["title"] for f in data["findings"]]
    # high -> a.py:10, a.py:20; medium -> b.py:5; low -> a.py:1; healthy -> z.py:1
    assert titles == ["h2", "h1", "m1", "l1", "ok"]


async def test_aggregate_validates_against_findings_index_schema(tmp_path: Path) -> None:
    with FindingsPartialWriter(tmp_path) as w:
        w.append(_finding(fid="f-aaaaaaaaaaaa"))
    target = await Aggregator().run(tmp_path, themes=[], run_metadata=_run_metadata())
    data = json.loads(target.read_text(encoding="utf-8"))
    Draft202012Validator(FINDINGS_INDEX_SCHEMA).validate(data)


async def test_aggregate_idempotent(tmp_path: Path) -> None:
    with FindingsPartialWriter(tmp_path) as w:
        w.append(_finding(fid="f-aaaaaaaaaaaa"))
        w.append(_finding(fid="f-bbbbbbbbbbbb", title="b"))
    agg = Aggregator()
    p1 = await agg.run(tmp_path, themes=None, run_metadata=_run_metadata())
    out1 = p1.read_text(encoding="utf-8")
    p2 = await agg.run(tmp_path, themes=None, run_metadata=_run_metadata())
    out2 = p2.read_text(encoding="utf-8")
    assert out1 == out2


async def test_aggregate_skips_corrupt_last_line(tmp_path: Path) -> None:
    """4 valid + 1 truncated last line -> aggregator emits 4, no raise."""
    findings = [_finding(fid=f"f-{i:012x}", title=f"t{i}") for i in range(4)]
    with FindingsPartialWriter(tmp_path) as w:
        for f in findings:
            w.append(f)
    # Append a truncated last line.
    with open(tmp_path / "findings.partial.jsonl", "ab") as fp:
        fp.write(b'{"id": "f-truncated')

    target = await Aggregator().run(tmp_path, themes=None, run_metadata=_run_metadata())
    data = json.loads(target.read_text(encoding="utf-8"))
    assert len(data["findings"]) == 4


async def test_aggregate_missing_partial_returns_empty_findings_json(tmp_path: Path) -> None:
    """No findings.partial.jsonl on disk -> empty findings; ``totals.files``
    reflects the ``run_metadata.files_audited`` scope (M11 bug 3) so the
    operator sees the audit ran even when no findings were emitted."""
    target = await Aggregator().run(tmp_path, themes=None, run_metadata=_run_metadata())
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["findings"] == []
    # files_audited=3 in _run_metadata; surfaces as totals.files.
    assert data["totals"] == {"high": 0, "medium": 0, "low": 0, "healthy": 0, "files": 3}


async def test_aggregate_totals_files_uses_run_metadata_when_findings_present(
    tmp_path: Path,
) -> None:
    """``totals.files`` MUST reflect run_metadata.files_audited even when
    findings exist — i.e., it's "files audited", not "files with findings".

    Pre-M11-bug-3-fix the aggregator returned ``len(set(f.file ...))`` which
    only counted files that produced findings; a clean run with 1/4 files
    finding-free reported 3 instead of 4.
    """
    findings = [
        _finding(fid="f-aaaaaaaaaaaa", file="a.py"),
        _finding(fid="f-bbbbbbbbbbbb", file="a.py"),  # same file, dup
        _finding(fid="f-cccccccccccc", file="b.py"),
    ]
    with FindingsPartialWriter(tmp_path) as w:
        for f in findings:
            w.append(f)

    # _run_metadata() defaults files_audited=3 — pretend 3 files audited
    # but only 2 produced findings (a.py, b.py).
    target = await Aggregator().run(
        tmp_path, themes=None, run_metadata=_run_metadata()
    )
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["totals"]["files"] == 3, (
        "totals.files must reflect run_metadata.files_audited, not "
        "len(set(finding.file))"
    )


async def test_aggregate_atomic_write(tmp_path: Path) -> None:
    """If os.replace raises, no partial findings.json appears."""
    with FindingsPartialWriter(tmp_path) as w:
        w.append(_finding(fid="f-aaaaaaaaaaaa"))

    with patch("senex.atomic_io.os.replace", side_effect=OSError("simulated rename failure")):
        with pytest.raises(OSError):
            await Aggregator().run(tmp_path, themes=None, run_metadata=_run_metadata())
    assert not (tmp_path / "findings.json").exists()


async def test_aggregate_themes_are_serialized(tmp_path: Path) -> None:
    with FindingsPartialWriter(tmp_path) as w:
        w.append(_finding(fid="f-aaaaaaaaaaaa"))
    themes = [
        Theme(
            id="t-aaaaaaaaaaaa",
            title="a theme",
            description="d",
            affected_files=["a.py", "b.py"],
            priority="medium",
            confidence="medium",
            recommended_action="do x",
        )
    ]
    target = await Aggregator().run(tmp_path, themes=themes, run_metadata=_run_metadata())
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["themes"][0]["title"] == "a theme"


async def test_aggregator_input_missing_audit_dir(tmp_path: Path) -> None:
    """Catastrophic case: audit_dir itself is missing -> AggregatorInputMissing."""
    missing = tmp_path / "does" / "not" / "exist"
    with pytest.raises(AggregatorInputMissing):
        await Aggregator().run(missing, themes=None, run_metadata=_run_metadata())
