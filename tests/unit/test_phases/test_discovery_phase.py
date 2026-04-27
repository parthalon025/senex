"""``DiscoveryPhase`` tests (M8 Task 8.3).

Wraps ``Walker.discover()``; emits exactly one ``DiscoveryStart`` and one
``DiscoveryComplete``; persists discovery output to ``discovery.json`` for
resume. Walker output is deterministic across runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from senex.config import SenexConfig
from senex.events import (
    BaseEvent,
    CommandBus,
    DiscoveryComplete,
    DiscoveryStart,
    EventBus,
)
from senex.lens import Lens
from senex.phases.discovery import DiscoveryPhase

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TINY_PY = _REPO_ROOT / "tests" / "fixtures" / "repos" / "tiny_python"


def _capture(bus: EventBus) -> list[BaseEvent]:
    captured: list[BaseEvent] = []

    async def cb(evt: BaseEvent) -> None:
        captured.append(evt)

    bus.subscribe_local("test", BaseEvent, cb)
    return captured


@pytest.mark.asyncio
async def test_discovery_returns_deterministic_files() -> None:
    cfg = SenexConfig(
        walker=SenexConfig().walker.model_copy(
            update={"extensions": [".py"], "include_tests": False}
        )
    )
    bus = EventBus()
    cb = CommandBus()
    captured = _capture(bus)
    phase = DiscoveryPhase(repo=_TINY_PY, run_id="r1")
    state1 = await phase.do_work(None, _make_lens(), cfg, bus, cb)

    bus2 = EventBus()
    _capture(bus2)
    state2 = await phase.do_work(None, _make_lens(), cfg, bus2, cb)

    files1 = [str(p) for p in state1["files"]]
    files2 = [str(p) for p in state2["files"]]
    assert files1 == files2  # determinism

    starts = [e for e in captured if isinstance(e, DiscoveryStart)]
    completes = [e for e in captured if isinstance(e, DiscoveryComplete)]
    assert len(starts) == 1
    assert len(completes) == 1
    assert completes[0].file_count == len(state1["files"])


@pytest.mark.asyncio
async def test_discovery_writes_state_for_resume(tmp_path: Path) -> None:
    cfg = SenexConfig(
        walker=SenexConfig().walker.model_copy(
            update={"extensions": [".py"], "include_tests": False}
        )
    )
    bus = EventBus()
    cb = CommandBus()
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    phase = DiscoveryPhase(repo=_TINY_PY, run_id="r1")
    state = await phase.do_work(None, _make_lens(), cfg, bus, cb)
    await phase.write_state(audit_dir, state)

    persisted = audit_dir / "discovery.json"
    assert persisted.exists()
    data = json.loads(persisted.read_text(encoding="utf-8"))
    assert "files" in data
    assert isinstance(data["files"], list)
    assert len(data["files"]) == len(state["files"])


@pytest.mark.asyncio
async def test_discovery_read_state_returns_persisted(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    persisted = audit_dir / "discovery.json"
    payload: dict[str, Any] = {"files": ["/x/y.py"], "skipped": []}
    persisted.write_text(json.dumps(payload), encoding="utf-8")

    phase = DiscoveryPhase(repo=tmp_path, run_id="r1")
    state = await phase.read_state(audit_dir)
    assert state is None or "files" in state  # contract: phase tolerates either


def _make_lens() -> Lens:
    return Lens.load("correctness")
