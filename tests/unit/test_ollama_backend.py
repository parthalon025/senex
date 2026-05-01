"""Tests for OllamaBackend lifecycle operations."""
import json

import httpx
import pytest
import respx

from senex.config import OllamaCfg
from senex.inference_lifecycle import OllamaBackend


@pytest.fixture
def cfg() -> OllamaCfg:
    return OllamaCfg(
        base_url="http://localhost:11434",
        model="gemma4:e4b",
        manage_process=False,
        pull_on_start=False,
    )


@respx.mock
@pytest.mark.asyncio
async def test_probe_returns_true_when_reachable(cfg):
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(200))
    assert await OllamaBackend.probe("http://localhost:11434") is True


@respx.mock
@pytest.mark.asyncio
async def test_probe_returns_false_when_unreachable(cfg):
    respx.get("http://localhost:11434/").mock(side_effect=httpx.ConnectError("refused"))
    assert await OllamaBackend.probe("http://localhost:11434") is False


@respx.mock
@pytest.mark.asyncio
async def test_is_loaded_true_when_model_in_ps(cfg):
    respx.get("http://localhost:11434/api/ps").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "gemma4:e4b"}]}
    ))
    backend = OllamaBackend(cfg)
    assert await backend.is_loaded("gemma4:e4b") is True


@respx.mock
@pytest.mark.asyncio
async def test_is_loaded_false_when_model_absent(cfg):
    respx.get("http://localhost:11434/api/ps").mock(return_value=httpx.Response(
        200, json={"models": []}
    ))
    backend = OllamaBackend(cfg)
    assert await backend.is_loaded("gemma4:e4b") is False


@respx.mock
@pytest.mark.asyncio
async def test_load_raises_when_health_fails_and_no_manage_process(cfg):
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(503))
    from senex.inference_lifecycle import LifecycleBackendUnavailable
    backend = OllamaBackend(cfg)
    with pytest.raises(LifecycleBackendUnavailable):
        await backend.load("gemma4:e4b", timeout=10)


@respx.mock
@pytest.mark.asyncio
async def test_load_succeeds_with_warmup(cfg):
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(200))
    respx.get("http://localhost:11434/api/tags").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "gemma4:e4b"}]}
    ))
    respx.get("http://localhost:11434/api/ps").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "gemma4:e4b"}]}
    ))
    respx.post("http://localhost:11434/api/show").mock(return_value=httpx.Response(
        200, json={"details": {"digest": "sha256:abc123"}}
    ))
    backend = OllamaBackend(cfg)
    info = await backend.load("gemma4:e4b", timeout=10)
    assert info.model_id == "gemma4:e4b"
    assert info.checkpoint_digest == "sha256:abc123"


@respx.mock
@pytest.mark.asyncio
async def test_unload_sends_keep_alive_zero(cfg):
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json={})
    )
    backend = OllamaBackend(cfg)
    await backend.unload("gemma4:e4b")
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["keep_alive"] == 0


@respx.mock
@pytest.mark.asyncio
async def test_list_loaded_returns_running_models(cfg):
    respx.get("http://localhost:11434/api/ps").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "gemma4:e4b"}, {"name": "llama3:8b"}]}
    ))
    backend = OllamaBackend(cfg)
    models = await backend.list_loaded()
    assert len(models) == 2
    assert models[0].model_id == "gemma4:e4b"
