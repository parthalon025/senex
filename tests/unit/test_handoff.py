"""Unit tests for senex.handoff."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from senex.handoff import HandoffPolicyViolation, write_handoff
from senex.render_models import (
    FindingRecord,
    LocationRecord,
    RunMetadata,
)


def _run() -> RunMetadata:
    return RunMetadata(
        repo="senex",
        run_id="01JZ3K7B9C8DQRS4M2EXAMPLE",
        run_id_short="01jz3k7b",
        audit_dir="E:/senex-audits/senex/2026-04-26-01jz3k7b",
        model="m",
        model_fingerprint="sha256:m",
        lens="correctness",
        lens_version="1.0.0",
        started_at="2026-04-26T22:14:03Z",
        duration_seconds=10.0,
        config_hash="sha256:c",
        prompt_hash="sha256:p",
        date="2026-04-26",
    )


def _finding(
    *,
    fid: str = "f-aaaaaaaaaaaa",
    file: str = "x.py",
    priority: str = "high",
    title: str = "t",
    line: int = 10,
    issue: str = "the_unique_issue_text",
    why: str = "the_unique_why_text",
    fix: str = "the_unique_fix_text",
) -> FindingRecord:
    return FindingRecord(
        id=fid,
        file=file,
        category="cat",
        priority=priority,  # type: ignore[arg-type]
        title=title,
        issue=issue,
        why=why,
        fix=fix,
        confidence="high",
        location=LocationRecord(line_start=line),
        report_path=f"{file}.md",
        suppressed=False,
        prompt_hash="sha256:p",
        config_hash="sha256:c",
        model_fingerprint="sha256:m",
        lens_version="1.0.0",
    )


def test_handoff_lists_audit_dir_paths(tmp_path: Path) -> None:
    findings = [_finding(file="store/sqlite.py", title="Connection leak")]
    target = write_handoff(tmp_path, _run(), findings)
    text = target.read_text(encoding="utf-8")
    assert "Audit dir:" in text
    assert "Findings index:" in text
    assert "Per-file reports:" in text
    assert str(tmp_path).replace("\\", "/") in text or str(tmp_path) in text


def test_handoff_top_3_findings_summary_format(tmp_path: Path) -> None:
    findings = [
        _finding(fid="f-100000000001", file="store/sqlite.py", line=142,
                 title="Connection leak on rollback"),
        _finding(fid="f-100000000002", file="auth/session.py", line=88,
                 title="Timing-unsafe token comparison"),
        _finding(fid="f-100000000003", file="retrieval/factory.py", line=312,
                 priority="medium", title="Unbounded retry loop"),
    ]
    target = write_handoff(tmp_path, _run(), findings)
    text = target.read_text(encoding="utf-8")
    # Sort: priority (HIGH x2 first), then line. auth:88 < store:142.
    # Format: `[<PRIORITY>] ` padded so file column always starts at col 12.
    assert "1. [HIGH]   auth/session.py:88" in text
    assert "Timing-unsafe token comparison" in text
    assert "2. [HIGH]   store/sqlite.py:142" in text
    assert "Connection leak on rollback" in text
    assert "3. [MEDIUM] retrieval/factory.py:312" in text
    assert "Unbounded retry loop" in text


def test_handoff_does_not_embed_finding_issue_text(tmp_path: Path) -> None:
    findings = [_finding(issue="ISSUE_BODY_SENTINEL_xyz123")]
    target = write_handoff(tmp_path, _run(), findings)
    text = target.read_text(encoding="utf-8")
    assert "ISSUE_BODY_SENTINEL_xyz123" not in text


def test_handoff_does_not_embed_finding_why_text(tmp_path: Path) -> None:
    findings = [_finding(why="WHY_BODY_SENTINEL_xyz456")]
    target = write_handoff(tmp_path, _run(), findings)
    text = target.read_text(encoding="utf-8")
    assert "WHY_BODY_SENTINEL_xyz456" not in text


def test_handoff_does_not_embed_finding_fix_text(tmp_path: Path) -> None:
    findings = [_finding(fix="FIX_BODY_SENTINEL_xyz789")]
    target = write_handoff(tmp_path, _run(), findings)
    text = target.read_text(encoding="utf-8")
    assert "FIX_BODY_SENTINEL_xyz789" not in text


def test_handoff_self_check_raises_on_policy_violation(tmp_path: Path) -> None:
    """Inject a finding whose issue text would render verbatim — defensively raise.

    We patch the internal renderer to deliberately leak the issue body.
    """
    findings = [_finding(issue="DETECTABLE_BODY_SENTINEL_xyz")]

    # Patch the internal builder to produce a body that contains the issue.
    def leaky(*args: object, **kwargs: object) -> str:
        return "Audit dir: x\nFindings index: x\nPer-file reports: x\n" + findings[0].issue

    with patch("senex.handoff._build_handoff_body", side_effect=leaky):
        with pytest.raises(HandoffPolicyViolation):
            write_handoff(tmp_path, _run(), findings)


def test_handoff_atomic_write(tmp_path: Path) -> None:
    """If os.replace raises, original handoff (if present) is unchanged."""
    findings = [_finding()]
    # Pre-create a sentinel handoff.
    sentinel = "PREEXISTING_HANDOFF_CONTENT"
    (tmp_path / "claude-handoff.md").write_text(sentinel, encoding="utf-8")

    with patch("senex.atomic_io.os.replace", side_effect=OSError("simulated")):
        with pytest.raises(OSError):
            write_handoff(tmp_path, _run(), findings)

    assert (tmp_path / "claude-handoff.md").read_text(encoding="utf-8") == sentinel


def test_handoff_top_3_uses_priority_then_line_order(tmp_path: Path) -> None:
    findings = [
        _finding(fid="f-100000000001", file="z.py", line=1, priority="low", title="zlow"),
        _finding(fid="f-100000000002", file="a.py", line=10, priority="high", title="ahigh"),
        _finding(fid="f-100000000003", file="b.py", line=5, priority="medium", title="bmed"),
        _finding(fid="f-100000000004", file="a.py", line=5, priority="high", title="ahi5"),
    ]
    target = write_handoff(tmp_path, _run(), findings)
    text = target.read_text(encoding="utf-8")
    # Top 3: ahi5 (a.py:5), ahigh (a.py:10), bmed (b.py:5). zlow excluded.
    pos_ahi5 = text.index("ahi5")
    pos_ahigh = text.index("ahigh")
    pos_bmed = text.index("bmed")
    assert pos_ahi5 < pos_ahigh < pos_bmed
    assert "zlow" not in text
