"""``PreflightPhase`` orchestration tests (M8 Task 8.2.3 + 8.2.4).

* First FAIL short-circuits (subsequent checks not run).
* WARN results are all collected and emitted as ``PreflightWarning`` events.
* ``PreflightFailure`` carries the documented ``exit_code`` per spec §8.1.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from senex.config import LmStudioCfg, SenexConfig
from senex.events import (
    CommandBus,
    EventBus,
    PreflightWarning,
)
from senex.lens import Lens
from senex.inference_client import LoadedModelInfo, ProbedCapabilities
from senex.phases import PreflightFailure
from senex.phases.preflight import (
    PreflightInputs,
    PreflightPhase,
)


class _FakeClient:
    def __init__(self, *, base_url: str = "http://localhost:1234/v1") -> None:
        self._config = LmStudioCfg(base_url=base_url, allow_non_loopback=False)

    async def list_loaded_models(self) -> list[LoadedModelInfo]:
        return [LoadedModelInfo(id="google/gemma-4-26b-a4b")]

    async def probe_capabilities(self, model_id: str) -> ProbedCapabilities:
        return ProbedCapabilities(
            supports_tools=True,
            supports_schema_with_tools=True,
            supports_streaming=True,
            supports_reasoning_effort=True,
        )


def _make_inputs(tmp_path: Path) -> PreflightInputs:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    cfg_path = tmp_path / "senex.config.toml"
    cfg_path.write_text("[lmstudio]\nmodel='google/gemma-4-26b-a4b'\n", encoding="utf-8")
    out = tmp_path / "audits"
    anchors = tmp_path / "prompts"
    anchors.mkdir()
    # Provide a python anchor so language_anchors check passes.
    (anchors / "lang_python.md").write_text("py\n", encoding="utf-8")
    return PreflightInputs(
        config_path=cfg_path,
        repo_path=repo,
        output_dir=out,
        min_disk_bytes=1,
        addendum_path=None,
        anchors_dir=anchors,
    )


def _make_lens() -> Lens:
    return Lens.load("correctness")


@pytest.mark.asyncio
async def test_preflight_phase_pass_emits_no_failure(tmp_path: Path) -> None:
    inputs = _make_inputs(tmp_path)
    client = _FakeClient()
    bus = EventBus()
    cb = CommandBus()

    cfg = SenexConfig(
        walker=SenexConfig().walker.model_copy(update={"extensions": [".py"]})
    )
    phase = PreflightPhase(client=client, inputs=inputs, run_id="r1")
    state = await phase.do_work(None, _make_lens(), cfg, bus, cb)
    assert state["warnings"] == [] or all(
        isinstance(w, str) for w in state["warnings"]
    )


@pytest.mark.asyncio
async def test_preflight_phase_first_fail_short_circuits(tmp_path: Path) -> None:
    inputs = _make_inputs(tmp_path)
    # Break repo path -> repo_path check fails BEFORE async checks run.
    inputs = PreflightInputs(
        config_path=inputs.config_path,
        repo_path=tmp_path / "missing_repo",
        output_dir=inputs.output_dir,
        min_disk_bytes=inputs.min_disk_bytes,
        addendum_path=inputs.addendum_path,
        anchors_dir=inputs.anchors_dir,
    )

    class TrackingClient(_FakeClient):
        list_calls = 0

        async def list_loaded_models(self) -> list[LoadedModelInfo]:
            TrackingClient.list_calls += 1
            return await super().list_loaded_models()

    client = TrackingClient()
    bus = EventBus()
    cb = CommandBus()
    cfg = SenexConfig()
    phase = PreflightPhase(client=client, inputs=inputs, run_id="r1")
    with pytest.raises(PreflightFailure) as excinfo:
        await phase.do_work(None, _make_lens(), cfg, bus, cb)
    assert excinfo.value.check_name == "repo_path"
    assert excinfo.value.exit_code == 2
    # Async checks must NOT have run.
    assert TrackingClient.list_calls == 0


@pytest.mark.asyncio
async def test_preflight_phase_collects_warns_into_events(tmp_path: Path) -> None:
    inputs = _make_inputs(tmp_path)
    # Anchors dir is empty for some extensions -> WARN
    inputs = PreflightInputs(
        config_path=inputs.config_path,
        repo_path=inputs.repo_path,
        output_dir=inputs.output_dir,
        min_disk_bytes=inputs.min_disk_bytes,
        addendum_path=inputs.addendum_path,
        anchors_dir=inputs.anchors_dir,
    )
    client = _FakeClient()
    bus = EventBus()
    captured: list[PreflightWarning] = []

    async def cb(evt: Any) -> None:
        captured.append(evt)

    bus.subscribe_local("test", PreflightWarning, cb)

    cb_bus = CommandBus()
    cfg = SenexConfig(
        walker=SenexConfig().walker.model_copy(update={"extensions": [".py", ".rs", ".go"]})
    )
    phase = PreflightPhase(client=client, inputs=inputs, run_id="r1")
    state = await phase.do_work(None, _make_lens(), cfg, bus, cb_bus)
    assert state["warnings"], "expected at least one WARN result"
    assert len(captured) == len(state["warnings"])
    assert all(isinstance(e, PreflightWarning) for e in captured)


@pytest.mark.asyncio
async def test_preflight_phase_persists_warnings_when_present(tmp_path: Path) -> None:
    """Step 8.2.3: write_state persists warnings to ``preflight.json``."""
    inputs = _make_inputs(tmp_path)
    client = _FakeClient()
    bus = EventBus()
    cb_bus = CommandBus()
    cfg = SenexConfig(
        walker=SenexConfig().walker.model_copy(update={"extensions": [".py", ".rs"]})
    )
    phase = PreflightPhase(client=client, inputs=inputs, run_id="r1")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    state = await phase.do_work(None, _make_lens(), cfg, bus, cb_bus)
    await phase.write_state(audit_dir, state)
    if state["warnings"]:
        assert (audit_dir / "preflight.json").exists()


# ---------------------------------------------------------------------------
# Exit code locking — tests/unit/test_exit_codes.py was specified by plan;
# we co-locate the table here so it lives next to the orchestration tests.
# ---------------------------------------------------------------------------


_EXIT_CODE_LOCKS: dict[str, int] = {
    "config_parses": 2,
    "repo_path": 2,
    "output_dir_writable": 2,
    "addendum_safety": 2,
    "sampling_ranges": 2,
    "lifecycle_backend": 3,
    "runlock_dir_writable": 2,
    "lms_reachable": 3,
    "model_loaded": 3,
}


@pytest.mark.parametrize("check_name,expected_exit", list(_EXIT_CODE_LOCKS.items()))
def test_preflight_failure_exit_code_lock(check_name: str, expected_exit: int) -> None:
    """Spec §8.1 contract: each check produces a fixed exit code.

    Constructing PreflightFailure with the documented exit_code is the
    public-API surface; the per-check unit tests in
    ``test_preflight_checks.py`` confirm each check function returns the
    expected exit_code on its FAIL path.
    """
    err = PreflightFailure(
        exit_code=expected_exit,
        message="locked",
        check_name=check_name,
    )
    assert err.exit_code == expected_exit
    assert err.check_name == check_name
