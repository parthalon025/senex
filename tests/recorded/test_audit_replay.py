"""End-to-end replay test — Task 3.9 §3.9.3.

Drives a 1-file audit through ``LMStudioClient`` with the ``recorded_lms``
fixture standing in for a live LMS server. The fixture file (named by
sha256 of the canonical request body) ships alongside this test module so
the suite is reproducible offline. The capture path is exercised by setting
``RECORD_LMS=1`` against a running LM Studio server (manual; not part of CI).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from senex.config import LmStudioCfg
from senex.events import EventBus
from senex.lmstudio_client import ChatMessage, LMStudioClient
from senex.secret_redactor import SecretRedactor


@pytest.fixture
def replay_cfg() -> LmStudioCfg:
    return LmStudioCfg(
        base_url="http://localhost:1234/v1",
        api_key="lm-studio-test",
        connect_timeout=1,
        read_timeout=5,
        http_retries=0,
        backoff_seconds=[],
        model="google/gemma-4-26b-a4b",
        context_window=32768,
        token_budget_pct=0.9,
        fingerprint_recheck_interval_s=60.0,
    )


@pytest.fixture
def replay_client(replay_cfg: LmStudioCfg) -> LMStudioClient:
    return LMStudioClient(
        config=replay_cfg, bus=EventBus(), redactor=SecretRedactor()
    )


@pytest.fixture
def audit_schema() -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "senex"
        / "schema"
        / "audit_response.schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _canonical_hash(body: dict[str, Any]) -> str:
    key = {
        "messages": body.get("messages"),
        "tools": body.get("tools"),
        "response_format": body.get("response_format"),
    }
    serialized = json.dumps(key, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


@pytest.mark.asyncio
async def test_replayed_audit_produces_valid_response(
    recorded_lms: Any,  # noqa: ARG001 — fixture autoroutes
    replay_client: LMStudioClient,
    audit_schema: dict[str, Any],
) -> None:
    """End-to-end audit replays from disk; ``RECORD_LMS=1`` would re-capture."""
    messages = [
        ChatMessage(
            role="system", content="You are a code-audit assistant. Respond JSON only."
        ),
        ChatMessage(role="user", content="<UNTRUSTED>x = 1\n</UNTRUSTED>"),
    ]
    response = await replay_client.chat(
        task="file_audit",
        messages=messages,
        schema=audit_schema,
        tools=None,
    )
    assert response.content_dict is not None
    assert response.content_dict["schema_version"] == 1
    assert isinstance(response.content_dict["findings"], list)
    await replay_client.aclose()


def test_record_lms_env_documented() -> None:
    """RECORD_LMS env var is the documented capture trigger (test docstring acts as doc).

    Sanity: verify the conftest source contains the trigger string so
    accidental renames are caught at test time.
    """
    src = (
        Path(__file__).resolve().parent / "conftest.py"
    ).read_text(encoding="utf-8")
    assert "RECORD_LMS" in src, (
        "recorded_lms fixture must honor RECORD_LMS env var (Task 3.9 spec)"
    )


def test_canonical_hash_excludes_transient_fields() -> None:
    """Same logical request with different timestamps hashes identically."""
    body_a: dict[str, Any] = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "temperature": 0.7,
    }
    body_b = dict(body_a)
    body_b["temperature"] = 0.5  # not in the canonical key
    assert _canonical_hash(body_a) == _canonical_hash(body_b)
    # but messages differing changes the hash
    body_c = {
        "model": "m",
        "messages": [{"role": "user", "content": "different"}],
    }
    assert _canonical_hash(body_a) != _canonical_hash(body_c)


def _ensure_recorded_env_unset_in_ci() -> None:
    """If CI accidentally set RECORD_LMS, this test reveals the misconfiguration."""
    assert os.environ.get("RECORD_LMS", "0") in ("", "0"), (
        "RECORD_LMS must not be set in normal/CI runs; replay-only mode."
    )


def test_record_lms_unset_by_default() -> None:
    _ensure_recorded_env_unset_in_ci()
