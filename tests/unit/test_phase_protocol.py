"""Test the Phase protocol contract + named exceptions (M8 Task 8.1).

Verifies:
* ``Phase`` is ``runtime_checkable`` so concrete phase classes can be tested
  via ``isinstance(obj, Phase)`` without ABC inheritance.
* Each named exception is importable, carries the documented attributes,
  and (for the ones with ``exit_code``) has the documented integer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from senex.config import SenexConfig  # noqa: F401 — used by Protocol type hints
from senex.events import CommandBus, EventBus  # noqa: F401 — used by Protocol
from senex.lens import Lens  # noqa: F401 — used by Protocol
from senex.phases import (
    AggregateFailed,
    Phase,
    PhaseAborted,
    PreflightFailure,
    RenderFatal,
    ResumeIncompatible,
)


class _StubPhase:
    name = "stub"

    async def read_state(self, audit_dir: Path) -> Any:
        return None

    async def do_work(
        self,
        state: Any,
        lens: Any,
        config: Any,
        bus: Any,
        command_bus: Any,
    ) -> Any:
        return None

    async def write_state(self, audit_dir: Path, state: Any) -> None:
        return None


def test_phase_protocol_is_runtime_checkable() -> None:
    stub = _StubPhase()
    assert isinstance(stub, Phase)


def test_phase_protocol_rejects_non_phase() -> None:
    class NotAPhase:
        pass

    assert not isinstance(NotAPhase(), Phase)


def test_preflight_failure_carries_exit_code_and_check_name() -> None:
    exc = PreflightFailure(exit_code=2, message="bad config", check_name="config_parses")
    assert exc.exit_code == 2
    assert exc.message == "bad config"
    assert exc.check_name == "config_parses"
    assert "bad config" in str(exc)


def test_phase_aborted_is_an_exception() -> None:
    with pytest.raises(PhaseAborted):
        raise PhaseAborted("aborted")


def test_resume_incompatible_default_exit_code_is_2() -> None:
    exc = ResumeIncompatible("hash mismatch")
    assert exc.exit_code == 2


def test_render_fatal_is_an_exception() -> None:
    with pytest.raises(RenderFatal):
        raise RenderFatal("disk gone")


def test_aggregate_failed_default_exit_code_is_1() -> None:
    exc = AggregateFailed("aggregator crashed")
    assert exc.exit_code == 1


def test_aggregate_failed_overridable_exit_code() -> None:
    exc = AggregateFailed("oops", exit_code=1)
    assert exc.exit_code == 1
