"""Tests for senex.lmstudio_client — M3 LM Studio integration.

Covers (per ``docs/superpowers/plans/2026-04-26-senex-v1/m3-lmstudio.md``):
3.1 basic chat + schema validation, 3.2 streaming + Tick coalescing +
``<think>`` strip, 3.3 schema fallback decision tree, 3.4 token counting
+ budget enforcement, 3.5 retry + backoff, 3.6 ``tools=`` parameter
(single round-trip), 3.7 fingerprint, 3.8 capability probe,
3.10 ANSI strip + secret redaction.

External boundary mocked via ``respx`` (httpx mock library); no live LMS.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from senex.config import LmStudioCfg
from senex.events import EventBus
from senex.lmstudio_client import ChatMessage, LMStudioClient
from senex.secret_redactor import SecretRedactor


# --- Boundary checks (M3 ↔ M5 contract — see plan §"M3 ↔ M5 boundary check") ---

def test_lmstudio_client_does_not_import_senex_tools() -> None:
    """senex/lmstudio_client.py MUST NOT import senex.tools.* (M5 territory)."""
    src = (Path(__file__).resolve().parents[2] / "senex" / "lmstudio_client.py").read_text(
        encoding="utf-8"
    )
    assert "from senex.tools" not in src, (
        "lmstudio_client.py imported senex.tools.*; iteration belongs in M5 ToolLoop."
    )
    assert "import senex.tools" not in src, (
        "lmstudio_client.py imported senex.tools.*; iteration belongs in M5 ToolLoop."
    )


def test_lmstudio_client_has_no_iteration_methods() -> None:
    """LMStudioClient MUST NOT define a tool-loop / iteration / compaction surface."""
    src = (Path(__file__).resolve().parents[2] / "senex" / "lmstudio_client.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("_tool_loop", "iterate_tools", "compaction_callback")
    for token in forbidden:
        assert token not in src, (
            f"lmstudio_client.py contains forbidden symbol {token!r} — that lives in M5/M6."
        )


def test_chat_docstring_documents_round_trip_boundary() -> None:
    """chat() docstring MUST cite the single-round-trip boundary and ToolLoop."""
    doc = LMStudioClient.chat.__doc__ or ""
    assert "ONE chat-completion round-trip" in doc
    assert "senex.tools.loop.ToolLoop" in doc


# --- Shared fixtures ----------------------------------------------------------

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "lms_responses"


@pytest.fixture
def fixture_dir() -> Path:
    return _FIXTURE_DIR


@pytest.fixture
def test_cfg() -> LmStudioCfg:
    return LmStudioCfg(
        base_url="http://localhost:1234/v1",
        api_key="lm-studio-test",
        connect_timeout=1,
        read_timeout=5,
        http_retries=3,
        backoff_seconds=[5, 15, 45],
        model="google/gemma-4-26b-a4b",
        context_window=32768,
        token_budget_pct=0.9,
        fingerprint_recheck_interval_s=60.0,
    )


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def redactor() -> SecretRedactor:
    return SecretRedactor()


@pytest.fixture
def audit_schema() -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "senex"
        / "schema"
        / "audit_response.schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


@pytest.fixture
def client(test_cfg: LmStudioCfg, bus: EventBus, redactor: SecretRedactor) -> LMStudioClient:
    return LMStudioClient(config=test_cfg, bus=bus, redactor=redactor)


# --- Task 3.1: basic chat + schema validation --------------------------------

@pytest.mark.asyncio
async def test_chat_returns_validated_response(
    respx_mock: Any,
    fixture_dir: Path,
    client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    payload = json.loads((fixture_dir / "simple_audit.json").read_text(encoding="utf-8"))
    respx_mock.post("http://localhost:1234/v1/chat/completions").respond(json=payload)

    resp = await client.chat(
        task="file_audit",
        messages=[ChatMessage(role="user", content="audit me")],
        schema=audit_schema,
        tools=None,
    )

    assert resp.content_dict is not None
    assert resp.content_dict["schema_version"] == 1
    assert resp.finish_reason == "stop"
    assert resp.tool_calls is None
    await client.aclose()
