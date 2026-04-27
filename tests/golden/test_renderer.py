"""Golden-file regression test for senex.renderer.

Pins byte-exact rendered Markdown for a hand-crafted AuditResponse fixture.
Drift here is intentional only — regenerate the golden file in the same
commit and explain why in the body.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from senex.render_models import FileMetadata, ToolCallSummary
from senex.renderer import Renderer
from senex.secret_redactor import SecretRedactor

GOLDEN_DIR = Path(__file__).parent
INPUT_PATH = GOLDEN_DIR / "sample_file.input.json"
EXPECTED_PATH = GOLDEN_DIR / "sample_file.expected.md"
SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "senex"
    / "schema"
    / "audit_response.schema.json"
)


def _file_metadata() -> FileMetadata:
    return FileMetadata(
        relpath="store/sqlite.py",
        language="python",
        model_id="google/gemma-4-26b-a4b",
        lens_name="correctness",
        prompt_tokens=8423,
        completion_tokens=1276,
        thinking_seconds=32.0,
        output_seconds=15.0,
        tools_used=[
            ToolCallSummary(name="gitnexus_context", count=1),
            ToolCallSummary(name="read_file", count=2),
        ],
        compactions_used=0,
        compactions_max=3,
        graph_context_summary=(
            "cluster=retrieval/factory | callers d=1: 14 | "
            "processes: build_retrieval_pipeline, search_pipeline_init"
        ),
        run_id_short="01jz3k7b",
        date="2026-04-26",
    )


def _render() -> str:
    response = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    renderer = Renderer(output_root=Path("/tmp/test"), redactor=SecretRedactor())
    return renderer.render_file(response, _file_metadata())


def test_render_byte_match_against_golden() -> None:
    rendered = _render()
    expected = EXPECTED_PATH.read_text(encoding="utf-8")
    assert rendered == expected, (
        "renderer output drifted from golden file\n"
        f"--- expected ({len(expected)} bytes) ---\n{expected}\n"
        f"--- got ({len(rendered)} bytes) ---\n{rendered}\n"
    )


def test_render_includes_every_metadata_field() -> None:
    """Every FileMetadata field must appear at least once in the rendered output."""
    rendered = _render()
    meta = _file_metadata()

    must_appear = [
        meta.relpath,
        meta.model_id,
        meta.lens_name,
        str(meta.prompt_tokens),
        str(meta.completion_tokens),
        str(int(meta.thinking_seconds)),
        str(int(meta.output_seconds)),
        meta.graph_context_summary,
        meta.run_id_short,
        meta.date,
        str(meta.compactions_used),
        str(meta.compactions_max),
    ]
    for tool in meta.tools_used:
        must_appear.append(tool.name)

    for fragment in must_appear:
        assert fragment in rendered, f"missing fragment in rendered output: {fragment!r}"


def test_render_escapes_pipes_in_table_cells() -> None:
    rendered = _render()
    # The third best-practices row contains "|" in its `feature` cell.
    assert "Boolean flag \\| toggle" in rendered
    # And in the "original" cell.
    assert "x \\| y as bitmask" in rendered


def test_render_escapes_pipes_in_finding_title() -> None:
    rendered = _render()
    # The Low-priority finding title contains a literal "|".
    assert "Unbounded retry loop on stale \\| index" in rendered
    # Category names also escape pipes.
    assert "Style \\| Naming" in rendered


def test_render_recommendation_language_falls_back_to_file_language() -> None:
    """Recommendation without `language` uses FileMetadata.language as fence tag."""
    response = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    # Re-shape: clear language on rec 1, and add a code_snippet to rec 2.
    response["recommendations"] = [
        {
            "title": "no-language rec",
            "rationale": "covers fallback path",
            "code_snippet": "print('hi')",
        }
    ]
    renderer = Renderer(output_root=Path("/tmp/test"), redactor=SecretRedactor())
    rendered = renderer.render_file(response, _file_metadata())
    # FileMetadata.language is "python".
    assert "```python" in rendered


def test_audit_response_schema_rejects_extra_fields_on_inner_items() -> None:
    """convention SCHEMA-2 R5: extra fields on findings/recommendations/best_practices items fail."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    # 1) Extra field on a finding.
    base = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    base["findings"][0]["severity"] = "p0"
    with pytest.raises(ValidationError) as ei:
        validator.validate(base)
    assert "additionalProperties" in str(ei.value) or "severity" in str(ei.value)

    # 2) Extra field on a recommendation.
    base = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    base["recommendations"][0]["author"] = "me"
    with pytest.raises(ValidationError):
        validator.validate(base)

    # 3) Extra field on a best_practices_table row.
    base = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    base["best_practices_table"][0]["note"] = "x"
    with pytest.raises(ValidationError):
        validator.validate(base)
