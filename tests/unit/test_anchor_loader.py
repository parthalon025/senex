"""Tests for senex.prompts._anchor_loader and prompt-asset stability.

Implements M2 Tasks 2.3 + 2.4 + 2.5 verification (spec sections 5.3, 5.1
trust-boundary template, 7.4 handoff, plus prompt-hash regression net).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from senex.prompts._anchor_loader import (
    EXT_TO_ANCHOR,
    PromptTemplateUnsubstituted,
    build_user_prompt,
    select_anchor,
)

# Repo-relative paths used by hash-pin / shape tests.
_REPO = Path(__file__).resolve().parent.parent.parent
_PROMPTS = _REPO / "senex" / "prompts"


def test_select_anchor_python_returns_anchor() -> None:
    anchor = select_anchor("foo.py")
    assert anchor is not None
    assert "pathlib" in anchor and "async" in anchor and "bare except" in anchor


def test_select_anchor_typescript_returns_anchor() -> None:
    assert "strict" in (select_anchor("foo.ts") or "")
    assert "strict" in (select_anchor("Foo.tsx") or "")


@pytest.mark.parametrize(
    "ext,must_contain",
    [
        (".rs", "Result"),
        (".go", "errors.Is"),
        (".cs", "IDisposable"),
    ],
)
def test_select_anchor_other_languages(ext: str, must_contain: str) -> None:
    anchor = select_anchor(f"foo{ext}")
    assert anchor is not None and must_contain in anchor


def test_select_anchor_unknown_returns_none() -> None:
    assert select_anchor("foo.unknown") is None
    assert select_anchor("foo") is None
    assert select_anchor("foo.txt") is None


def test_anchors_parse_as_nonempty_markdown() -> None:
    for filename in set(EXT_TO_ANCHOR.values()):
        body = (_PROMPTS / filename).read_text(encoding="utf-8")
        assert body.strip(), f"{filename} is empty after stripping"
        # No yaml front matter.
        assert not body.lstrip().startswith(
            "---"
        ), f"{filename} unexpectedly has front matter"


def test_anchor_python_required_keywords() -> None:
    body = select_anchor("foo.py") or ""
    for kw in [
        "pathlib",
        "async",
        "await",
        "bare except",
        "mutable default",
        "is None",
        "f-string",
    ]:
        assert kw in body, f"lang_python.md missing required keyword: {kw}"


def test_anchor_typescript_required_keywords() -> None:
    body = select_anchor("foo.ts") or ""
    for kw in [
        "strict",
        "narrow",
        "never",
        "unknown",
        "any",
        "Promise",
        "await",
    ]:
        assert kw in body, f"lang_typescript.md missing required keyword: {kw}"


def test_anchor_rust_required_keywords() -> None:
    body = select_anchor("foo.rs") or ""
    for kw in ["Result", "Option", "unwrap", "?", "ownership", "lifetime", "unsafe", "clippy"]:
        assert kw in body, f"lang_rust.md missing required keyword: {kw}"


def test_anchor_go_required_keywords() -> None:
    body = select_anchor("foo.go") or ""
    for kw in [
        "error",
        "errors.Is",
        "defer",
        "goroutine",
        "channel",
        "context.Context",
        "nil",
    ]:
        assert kw in body, f"lang_go.md missing required keyword: {kw}"


def test_anchor_csharp_required_keywords() -> None:
    body = select_anchor("foo.cs") or ""
    for kw in [
        "IDisposable",
        "using",
        "async",
        "Task",
        "nullable",
        "ConfigureAwait",
        "IAsyncEnumerable",
    ]:
        assert kw in body, f"lang_csharp.md missing required keyword: {kw}"


def test_build_user_prompt_substitutes_variables() -> None:
    out = build_user_prompt(
        file_relpath="src/foo.py",
        language="python",
        graph_context="Cluster: foo",
        numbered_source="  1: x = 1\n  2: y = 2\n",
    )
    assert "src/foo.py" in out
    assert "python" in out
    assert "Cluster: foo" in out
    assert "x = 1" in out
    assert "{file_relpath}" not in out
    assert "{graph_context}" not in out


def test_build_user_prompt_raises_on_missing_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If template has an unknown placeholder, raise PromptTemplateUnsubstituted."""
    from senex.prompts import _anchor_loader as al

    al._read_prompt.cache_clear()

    def fake_read(name: str) -> str:
        if name == "per_file_user.md":
            return "Hello {who} from {file_relpath}\n"
        return ""

    monkeypatch.setattr(al, "_read_prompt", fake_read)
    with pytest.raises(PromptTemplateUnsubstituted):
        build_user_prompt(
            file_relpath="a", language="b", graph_context="c", numbered_source="d"
        )


def test_anchor_loader_module_has_nonempty_docstring() -> None:
    from senex.prompts import _anchor_loader as al

    assert al.__doc__ and al.__doc__.strip() != ""


# ----------------------------------------------------------------------
# Task 2.4 — system prompt + per-file user template (hash + shape)
# ----------------------------------------------------------------------

EXPECTED_SYSTEM_PROMPT_SHA256 = (
    "b901a49bcf3848f5c0afd934d6d9be12fc9ba25714a67a6e9c5f15d684c123b9"
)


def test_system_senior_dev_hash_matches_spec_5_1() -> None:
    """sha256 over `system_senior_dev.md` MUST equal the spec section 5.1 pin.

    Section POL-9 + spec section 5.1: the system prompt is the audit's
    behavioral contract. Drift is a spec change, not an implementation
    change.
    """
    body = (_PROMPTS / "system_senior_dev.md").read_bytes()
    # Reject CRLF and BOM; the test is intentionally strict on byte-shape.
    assert b"\r\n" not in body, (
        "system_senior_dev.md must use LF line endings, not CRLF"
    )
    assert not body.startswith(b"\xef\xbb\xbf"), (
        "system_senior_dev.md must not start with UTF-8 BOM"
    )
    digest = hashlib.sha256(body).hexdigest()
    assert digest == EXPECTED_SYSTEM_PROMPT_SHA256, (
        "system_senior_dev.md sha256 mismatch.\n"
        f"  expected: {EXPECTED_SYSTEM_PROMPT_SHA256}\n"
        f"  actual:   {digest}\n"
        "If the spec section 5.1 prompt has changed intentionally, update "
        "both the file and EXPECTED_SYSTEM_PROMPT_SHA256 + "
        "tests/fixtures/expected_prompt_hashes.json in the same commit, "
        "and cite the spec edit in the commit body."
    )


def test_system_senior_dev_contains_required_anchors() -> None:
    body = (_PROMPTS / "system_senior_dev.md").read_text(encoding="utf-8")
    for required in [
        "ROLE",
        "TRUST BOUNDARY",
        "<UNTRUSTED_FILE_CONTENT>",
        "TRIAGE GATE",
        "PRIORITY RUBRIC",
        "WHAT TO LOOK FOR",
        "WHAT TO NOT FLAG",
        "OUTPUT DISCIPLINE",
        "TOOL USE",
        "CONFIDENCE RUBRIC",
    ]:
        assert required in body, f"missing required anchor: {required}"


def test_per_file_user_template_has_four_placeholders_and_trust_boundary() -> None:
    body = (_PROMPTS / "per_file_user.md").read_text(encoding="utf-8")
    for placeholder in [
        "{file_relpath}",
        "{language}",
        "{graph_context}",
        "{numbered_source}",
    ]:
        assert placeholder in body, f"template missing {placeholder}"
    assert "<UNTRUSTED_FILE_CONTENT>" in body
    assert "</UNTRUSTED_FILE_CONTENT>" in body


# ----------------------------------------------------------------------
# Task 2.5 — crosscut + handoff + compaction + hash regression net
# ----------------------------------------------------------------------


def test_all_prompt_hashes_stable_byte_for_byte() -> None:
    """Spec section POL-9: every prompt is byte-pinned by sha256.

    Drift produces a clear diff at review; intentional spec edits update
    both file + fixture in the same commit.
    """
    fixture_path = (
        _REPO / "tests" / "fixtures" / "expected_prompt_hashes.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["algorithm"] == "sha256"
    assert fixture["newline"] == "lf"
    for filename, expected in fixture["prompts"].items():
        body = (_PROMPTS / filename).read_bytes()
        assert b"\r\n" not in body, (
            f"{filename} has CRLF; spec requires LF"
        )
        assert not body.startswith(b"\xef\xbb\xbf"), (
            f"{filename} has UTF-8 BOM; not allowed"
        )
        actual = hashlib.sha256(body).hexdigest()
        assert actual == expected, (
            f"prompt drift detected: {filename}\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}\n"
            "If the change is intentional, regenerate "
            "tests/fixtures/expected_prompt_hashes.json in this commit "
            "and cite the spec edit in the commit body."
        )


def test_all_prompts_use_lf_endings_and_no_bom() -> None:
    """Defense-in-depth: CRLF / BOM in any prompt would invalidate the hash."""
    for p in _PROMPTS.glob("*.md"):
        b = p.read_bytes()
        assert b"\r\n" not in b, f"{p} has CRLF"
        assert not b.startswith(b"\xef\xbb\xbf"), f"{p} has BOM"
        assert b.endswith(b"\n"), f"{p} missing trailing LF"


def test_cross_cutting_prompt_has_required_anchors() -> None:
    body = (_PROMPTS / "cross_cutting.md").read_text(encoding="utf-8")
    for required in ["ROLE", "TRUST BOUNDARY", "YOUR JOB", "RULES", "OUTPUT"]:
        assert required in body, f"cross_cutting.md missing: {required}"


def test_compaction_prompt_has_required_anchors() -> None:
    body = (_PROMPTS / "compaction.md").read_text(encoding="utf-8")
    for required in [
        "ROLE",
        "TRUST BOUNDARY",
        "evidence_summary",
        "key_findings_so_far",
        "unanswered_questions",
        "OUTPUT",
    ]:
        assert required in body, f"compaction.md missing: {required}"


def test_claude_handoff_prompt_has_required_anchors() -> None:
    body = (_PROMPTS / "claude_handoff.md").read_text(encoding="utf-8")
    # Spec section 7.4 verbatim body markers.
    for required in [
        "senex audit findings",
        "Audit dir:",
        "Findings index:",
        "APPLY",
        "DISMISS",
        "DEFER",
        "gitnexus_impact",
    ]:
        assert required in body, f"claude_handoff.md missing: {required}"
