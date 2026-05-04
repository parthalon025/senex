"""Tests for Ollama-specific doctor checks added in Task 9."""
import httpx
import pytest
import respx

from senex.phases.preflight import CheckStatus


@respx.mock
@pytest.mark.asyncio
async def test_ollama_reachable_passes_on_200():
    from senex.phases.preflight import check_ollama_reachable

    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(200))
    result = await check_ollama_reachable("http://localhost:11434")
    assert result.status == CheckStatus.PASS


@respx.mock
@pytest.mark.asyncio
async def test_ollama_reachable_fails_on_non_200():
    from senex.phases.preflight import check_ollama_reachable

    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(503))
    result = await check_ollama_reachable("http://localhost:11434")
    assert result.status == CheckStatus.FAIL
    assert result.exit_code == 3


@respx.mock
@pytest.mark.asyncio
async def test_ollama_reachable_fails_on_connection_error():
    from senex.phases.preflight import check_ollama_reachable

    respx.get("http://localhost:11434/").mock(side_effect=httpx.ConnectError("refused"))
    result = await check_ollama_reachable("http://localhost:11434")
    assert result.status == CheckStatus.FAIL
    assert result.exit_code == 3


@respx.mock
@pytest.mark.asyncio
async def test_ollama_model_available_passes_on_200():
    from senex.phases.preflight import check_ollama_model_available

    respx.post("http://localhost:11434/api/show").mock(
        return_value=httpx.Response(200, json={"details": {"digest": "sha256:abc"}})
    )
    result = await check_ollama_model_available("http://localhost:11434", "gemma4:e4b")
    assert result.status == CheckStatus.PASS


@respx.mock
@pytest.mark.asyncio
async def test_ollama_model_available_fails_on_404():
    from senex.phases.preflight import check_ollama_model_available

    respx.post("http://localhost:11434/api/show").mock(return_value=httpx.Response(404))
    result = await check_ollama_model_available("http://localhost:11434", "gemma4:e4b")
    assert result.status == CheckStatus.FAIL
    assert result.exit_code == 3


@respx.mock
@pytest.mark.asyncio
async def test_ollama_model_available_fails_on_connection_error():
    from senex.phases.preflight import check_ollama_model_available

    respx.post("http://localhost:11434/api/show").mock(
        side_effect=httpx.ConnectError("refused")
    )
    result = await check_ollama_model_available("http://localhost:11434", "gemma4:e4b")
    assert result.status == CheckStatus.FAIL


def test_run_async_checks_includes_ollama_checks_when_backend_is_ollama():
    """_run_async_checks inserts ollama_reachable + ollama_model_available for ollama backend."""
    from unittest.mock import AsyncMock, patch

    from senex.config import InferenceCfg, SenexConfig
    from senex.phases.preflight import CheckResult, CheckStatus

    _ok = CheckResult(status=CheckStatus.PASS)
    _pass_pair = lambda name: (name, _ok)  # noqa: E731

    with (
        patch(
            "senex.cli_doctor.check_lms_reachable", new=AsyncMock(return_value=_ok)
        ),
        patch(
            "senex.cli_doctor.check_model_loaded_or_loadable",
            new=AsyncMock(return_value=_ok),
        ),
        patch(
            "senex.cli_doctor.check_schema_with_thinking",
            new=AsyncMock(return_value=_ok),
        ),
        patch("senex.cli_doctor.check_streaming", new=AsyncMock(return_value=_ok)),
        patch(
            "senex.cli_doctor.check_ollama_reachable",
            new=AsyncMock(return_value=_ok),
        ),
        patch(
            "senex.cli_doctor.check_ollama_model_available",
            new=AsyncMock(return_value=_ok),
        ),
    ):
        from senex.cli_doctor import _run_async_checks

        cfg = InferenceCfg(backend="ollama")
        rows = _run_async_checks(
            SenexConfig(inference=cfg),  # type: ignore[call-arg]
        )

    names = [name for name, _ in rows]
    assert "ollama_reachable" in names
    assert "ollama_model_available" in names
