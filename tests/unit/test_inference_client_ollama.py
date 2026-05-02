"""Tests for backend-aware schema translation and thinking injection."""
from unittest.mock import MagicMock

from pydantic import BaseModel


class _Schema(BaseModel):
    answer: str


def _make_client(backend_type: str):
    from senex.config import InferenceCfg
    from senex.inference_client import InferenceClient

    cfg = InferenceCfg()
    bus = MagicMock()
    redactor = MagicMock()
    redactor.redact.side_effect = lambda x: x
    client = InferenceClient(cfg, bus, redactor)
    client._backend_type = backend_type
    return client


def test_build_request_ollama_uses_format_not_response_format():
    client = _make_client("ollama")
    messages = [{"role": "user", "content": "hi"}]
    body = client._apply_schema_to_body({"model": "gemma4:e4b", "messages": messages}, _Schema)
    assert "format" in body
    assert "response_format" not in body
    assert body["format"] == _Schema.model_json_schema()


def test_build_request_sglang_uses_response_format():
    client = _make_client("sglang")
    messages = [{"role": "user", "content": "hi"}]
    body = client._apply_schema_to_body({"model": "x", "messages": messages}, _Schema)
    assert "response_format" in body
    assert "format" not in body


def test_inject_thinking_ollama_prepends_think_token():
    client = _make_client("ollama")
    messages = [
        {"role": "system", "content": "You are an auditor."},
        {"role": "user", "content": "Audit this."},
    ]
    result = client._inject_thinking(messages, backend_type="ollama", thinking_enabled=True)
    assert result[0]["content"].startswith("<|think|>")
    assert "You are an auditor." in result[0]["content"]


def test_inject_thinking_sglang_no_message_mutation():
    client = _make_client("sglang")
    messages = [
        {"role": "system", "content": "You are an auditor."},
    ]
    result = client._inject_thinking(messages, backend_type="sglang", thinking_enabled=True)
    assert result[0]["content"] == "You are an auditor."


def test_inject_thinking_disabled_no_mutation():
    client = _make_client("ollama")
    messages = [{"role": "system", "content": "Sys prompt."}]
    result = client._inject_thinking(messages, backend_type="ollama", thinking_enabled=False)
    assert result[0]["content"] == "Sys prompt."
