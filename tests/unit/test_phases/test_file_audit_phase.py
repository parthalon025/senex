"""``FileAuditPhase`` per-file recovery branch tests (M8 Task 8.4).

Each spec §8.2 row gets its own test — happy path, plus every recovery
branch. Uses an in-memory fake LMS client so tests run hermetically.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from senex.checkpoint import Checkpoint
from senex.config import SenexConfig
from senex.events import (
    BaseEvent,
    Command,
    CommandBus,
    EventBus,
    FileComplete,
    FileError,
    FileStart,
)
from senex.findings_partial import FindingsPartialWriter
from senex.lens import Lens
from senex.lmstudio_client import (
    ChatMessage,
    ChatResponse,
    LoadedModelInfo,
    ProbedCapabilities,
)
from senex.lmstudio_errors import (
    FingerprintChanged,
    LMSConnectionLost,
    LMSResponseSchemaInvalid,
)
from senex.phases.file_audit import FileAuditPhase
from senex.renderer import Renderer
from senex.secret_redactor import SecretRedactor
from senex.tools.registry import ToolRegistry


_VALID_RESPONSE_DICT: dict[str, Any] = {
    "schema_version": 1,
    "overall_assessment": "Looks fine.",
    "findings": [
        {
            "category": "Correctness",
            "priority": "low",
            "title": "Off-by-one suspected",
            "issue": "first_n returns n+1 items",
            "why": "slice end is n+1",
            "fix": "use xs[:n]",
            "confidence": "medium",
            "location": {"line_start": 4, "symbol": "first_n"},
        }
    ],
    "recommendations": [
        {"title": "Tighten slice", "rationale": "...", "code_snippet": "xs[:n]"}
    ],
    "best_practices_table": [],
}


class FakeClient:
    """Minimal LMStudioClient stand-in for FileAuditPhase tests."""

    def __init__(
        self,
        *,
        responses: list[Any] | None = None,
        context_window: int = 32768,
        token_budget_pct: float = 0.9,
    ) -> None:
        from senex.config import LmStudioCfg

        self._responses = list(responses or [])
        self._config = LmStudioCfg(
            context_window=context_window,
            token_budget_pct=token_budget_pct,
        )
        self._fingerprint_pinned = "fp123"
        self.calls = 0

    @property
    def context_window(self) -> int:
        return self._config.context_window

    async def chat(self, *, task: str, messages: list[Any], schema: Any, tools: Any = None) -> Any:
        # Yield control so the test's command-bus poster has a chance to run.
        import asyncio

        await asyncio.sleep(0)
        self.calls += 1
        if not self._responses:
            return _make_response(_VALID_RESPONSE_DICT)
        head = self._responses.pop(0)
        if isinstance(head, BaseException):
            raise head
        return head

    async def list_loaded_models(self) -> list[LoadedModelInfo]:
        return [LoadedModelInfo(id="google/gemma-4-26b-a4b")]

    async def probe_capabilities(self, model_id: str) -> ProbedCapabilities:
        return ProbedCapabilities(
            supports_tools=True,
            supports_schema_with_tools=True,
            supports_streaming=True,
            supports_reasoning_effort=True,
        )

    def count_tokens(self, messages: list[ChatMessage], model_id: str) -> int:
        # Sum content length / 4 — approximate.
        total = 0
        for m in messages:
            if m.content:
                total += len(m.content) // 4
        return total

    def compute_fingerprint(self, model_info: LoadedModelInfo) -> str:
        return "fp123"

    async def aclose(self) -> None:
        return None


def _make_response(payload: dict[str, Any]) -> ChatResponse:
    body = json.dumps(payload)
    return ChatResponse(
        content=body,
        content_dict=payload,
        reasoning_content="thought",
        tool_calls=None,
        finish_reason="stop",
        latency_ms=10,
        prompt_tokens=10,
        completion_tokens=20,
        fingerprint="fp123",
    )


class FakeGraphProvider:
    async def fetch(self, relpath: str) -> Any:
        from senex.graph_awareness import GraphContext

        return GraphContext(
            cluster=None,
            cluster_summary=None,
            public_symbols=[],
            callers_d1_count={},
            top_processes=[],
            available=True,
            raw_text_block="(graph block)",
        )


def _setup_phase(
    tmp_path: Path,
    client: FakeClient,
    files: list[Path],
    *,
    save_traces: bool = False,
) -> tuple[FileAuditPhase, EventBus, CommandBus, list[BaseEvent], dict[str, Any]]:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    Checkpoint.create(
        audit_dir,
        run_id="01JZRUNDTEST1234567890ABCD",
        config_hash="c",
        prompt_hash="p",
        model_fingerprint="fp123",
        tool_pack_hash="t",
        lens_version="1.0.0",
    )
    redactor = SecretRedactor()
    renderer = Renderer(audit_dir, redactor)
    partial = FindingsPartialWriter(audit_dir)
    registry = ToolRegistry()
    Lens.load("correctness")  # ensure lens loads cleanly (no ref kept)

    repo_root = files[0].parent if files else tmp_path

    def compactor_factory(file: Path) -> Any:
        class _NoCompact:
            async def maybe_compact(
                self, msgs: list[Any], context_window: int
            ) -> list[Any]:
                return msgs

        return _NoCompact()

    cfg = SenexConfig()
    cfg.lmstudio.thinking = cfg.lmstudio.thinking.model_copy(
        update={"save_traces": save_traces}
    )

    phase = FileAuditPhase(
        audit_dir=audit_dir,
        repo_root=repo_root,
        repo_name="tiny_python",
        run_id="01JZRUNDTEST1234567890ABCD",
        run_id_short="01JZRUND",
        client=client,
        compactor_factory=compactor_factory,
        graph_provider=FakeGraphProvider(),
        renderer=renderer,
        partial_writer=partial,
        tool_registry=registry,
        lens_tools_resolved=[],
        config_hash="c",
        prompt_hash="p",
    )

    bus = EventBus()
    captured: list[BaseEvent] = []

    async def cb(evt: BaseEvent) -> None:
        captured.append(evt)

    bus.subscribe_local("test", BaseEvent, cb)
    cb_bus = CommandBus()

    state: dict[str, Any] = {
        "files": files,
        "skipped": [],
        "relpath_to_report_path": {},
    }
    return phase, bus, cb_bus, captured, state


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_one_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("def first_n(xs, n): return xs[:n+1]\n", encoding="utf-8")

    client = FakeClient(responses=[_make_response(_VALID_RESPONSE_DICT)])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])

    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert len(out["completed"]) == 1
    starts = [e for e in captured if isinstance(e, FileStart)]
    completes = [e for e in captured if isinstance(e, FileComplete)]
    assert len(starts) == 1
    assert len(completes) == 1
    # Per-file md present.
    assert (phase._audit_dir / "main.py.md").exists()


# ---------------------------------------------------------------------------
# Recovery branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_error_writes_skipped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "binary.py"
    f1.write_bytes(b"\xff\xfe\x00\x01\xc3\x28")  # invalid UTF-8

    client = FakeClient(responses=[])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert out["skipped"] == ["binary.py"]
    err_evts = [
        e
        for e in captured
        if isinstance(e, FileError) and e.error_kind == "read_error"
    ]
    assert len(err_evts) == 1
    assert (phase._audit_dir / "binary.py.SKIPPED.md").exists()


@pytest.mark.asyncio
async def test_token_budget_exceeded_writes_skipped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "huge.py"
    # Stuff with content; we'll force tiny ctx window in client.
    f1.write_text("x = 1\n" * 1000, encoding="utf-8")

    client = FakeClient(responses=[], context_window=64, token_budget_pct=0.9)
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    cfg.lmstudio.context_window = 64
    cfg.lmstudio.token_budget_pct = 0.9
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert out["skipped"] == ["huge.py"]
    err = [e for e in captured if isinstance(e, FileError) and e.error_kind == "token_budget"]
    assert len(err) == 1
    assert (phase._audit_dir / "huge.py.SKIPPED.md").exists()


@pytest.mark.asyncio
async def test_schema_mismatch_then_success(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    client = FakeClient(
        responses=[
            LMSResponseSchemaInvalid("first try bad"),
            _make_response(_VALID_RESPONSE_DICT),
        ]
    )
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert out["completed"] == ["main.py"]
    # Only the success FileComplete should have fired.
    assert len([e for e in captured if isinstance(e, FileComplete)]) == 1


@pytest.mark.asyncio
async def test_schema_mismatch_both_tries_writes_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    client = FakeClient(
        responses=[
            LMSResponseSchemaInvalid("first bad"),
            LMSResponseSchemaInvalid("second bad"),
        ]
    )
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert out["errored"] == ["main.py"]
    assert (phase._audit_dir / "main.py.ERROR.md").exists()
    assert (phase._audit_dir / "main.py.RAW.json").exists()
    err = [e for e in captured if isinstance(e, FileError) and e.error_kind == "schema_mismatch"]
    assert len(err) == 1


@pytest.mark.asyncio
async def test_lms_connection_lost_propagates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    client = FakeClient(responses=[LMSConnectionLost("dropped")])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    with pytest.raises(LMSConnectionLost):
        await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)


@pytest.mark.asyncio
async def test_lms_error_writes_error_artifact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    from senex.lmstudio_errors import LMStudioError

    client = FakeClient(responses=[LMStudioError("server returned 500")])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert out["errored"] == ["main.py"]
    assert (phase._audit_dir / "main.py.ERROR.md").exists()
    err = [e for e in captured if isinstance(e, FileError) and e.error_kind == "lms_error"]
    assert len(err) == 1


@pytest.mark.asyncio
async def test_render_crash_writes_render_error_continues(tmp_path: Path) -> None:
    """ARCH-13: render crash on a per-file basis is NOT run-killing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")
    f2 = repo / "other.py"
    f2.write_text("y=2\n", encoding="utf-8")

    client = FakeClient(responses=[_make_response(_VALID_RESPONSE_DICT)] * 5)
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1, f2])

    # Patch renderer.render_file to raise on the first call only.
    original_render = phase._renderer.render_file
    calls = {"n": 0}

    def boom(response: Any, meta: Any) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("render exploded")
        return original_render(response, meta)

    phase._renderer.render_file = boom  # type: ignore[method-assign]
    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert "main.py" in out["errored"]
    assert "other.py" in out["completed"]
    assert (phase._audit_dir / "main.py.RENDER_ERROR.md").exists()


@pytest.mark.asyncio
async def test_compaction_loop_exceeded_writes_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    from senex.compaction import CompactionLoopExceeded

    client = FakeClient(
        responses=[CompactionLoopExceeded(path=f1, compactions_so_far=4, limit=3)]
    )
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert out["errored"] == ["main.py"]
    assert (phase._audit_dir / "main.py.ERROR.md").exists()
    err = [
        e
        for e in captured
        if isinstance(e, FileError) and e.error_kind == "compaction_loop"
    ]
    assert len(err) == 1


@pytest.mark.asyncio
async def test_fingerprint_changed_propagates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    client = FakeClient(responses=[FingerprintChanged("model swapped")])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    with pytest.raises(FingerprintChanged):
        await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)


# ---------------------------------------------------------------------------
# Command bus integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_bus_skip_writes_skipped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")
    f2 = repo / "other.py"
    f2.write_text("y=2\n", encoding="utf-8")

    client = FakeClient(responses=[_make_response(_VALID_RESPONSE_DICT)] * 5)
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1, f2])
    # Pre-subscribe a queue so we know the bus is wired; the phase will create
    # its own subscriber inside do_work (poll before FileStart). Pre-publish
    # the command into a third queue we attach right before calling do_work
    # would race with the phase's subscribe(). Instead, monkey-patch the
    # subscribe to also pre-fill the queue with our Skip command.
    import asyncio
    from datetime import datetime, timezone

    skip_cmd = Command(type="Skip", target="main.py", ts=datetime.now(tz=timezone.utc))
    original_subscribe = cb.subscribe

    def subscribe_and_seed() -> asyncio.Queue[Command]:
        q = original_subscribe()
        q.put_nowait(skip_cmd)
        return q

    cb.subscribe = subscribe_and_seed  # type: ignore[method-assign]

    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert "main.py" in out["skipped"]
    assert "other.py" in out["completed"]


@pytest.mark.asyncio
async def test_command_bus_quit_propagates_keyboard_interrupt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    client = FakeClient(responses=[_make_response(_VALID_RESPONSE_DICT)])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    import asyncio
    from datetime import datetime, timezone

    cfg = SenexConfig()
    quit_cmd = Command(type="Quit", target=None, ts=datetime.now(tz=timezone.utc))
    original_subscribe = cb.subscribe

    def subscribe_and_seed() -> asyncio.Queue[Command]:
        q = original_subscribe()
        q.put_nowait(quit_cmd)
        return q

    cb.subscribe = subscribe_and_seed  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt):
        await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)


# ---------------------------------------------------------------------------
# Resume — already-completed files are skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_skips_already_completed_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")
    f2 = repo / "other.py"
    f2.write_text("y=2\n", encoding="utf-8")

    client = FakeClient(responses=[_make_response(_VALID_RESPONSE_DICT)])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1, f2])
    # Mark f1 as already done in checkpoint.
    Checkpoint.mark_done(phase._audit_dir, "main.py")

    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    # f1 went through resume-skip (no FileStart fired); f2 ran normally.
    assert "main.py" in out["completed"]
    assert "other.py" in out["completed"]
    starts = [e.path for e in captured if isinstance(e, FileStart)]
    assert "main.py" not in starts
    assert "other.py" in starts


# ---------------------------------------------------------------------------
# Thinking trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_trace_written_when_enabled(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    client = FakeClient(responses=[_make_response(_VALID_RESPONSE_DICT)])
    phase, bus, cb, captured, state = _setup_phase(
        tmp_path, client, [f1], save_traces=True
    )
    cfg = SenexConfig()
    cfg.lmstudio.thinking = cfg.lmstudio.thinking.model_copy(update={"save_traces": True})
    await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert (phase._audit_dir / "main.py.thinking.md").exists()


# ---------------------------------------------------------------------------
# FileMetadata population (M11 bug 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_metadata_populates_tokens_latency_compactions(
    tmp_path: Path,
) -> None:
    """``_build_file_metadata`` MUST surface ChatResponse token/timing fields
    and the compactor's running count instead of hardcoded zeros (M11 bug 2).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    # Build a response with all the metric fields populated.
    body = json.dumps(_VALID_RESPONSE_DICT)
    rich_response = ChatResponse(
        content=body,
        content_dict=_VALID_RESPONSE_DICT,
        reasoning_content="thought",
        tool_calls=None,
        finish_reason="stop",
        latency_ms=1500,
        thinking_ms=1000,
        output_ms=500,
        prompt_tokens=1234,
        completion_tokens=567,
        fingerprint="fp123",
    )
    client = FakeClient(responses=[rich_response])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])

    cfg = SenexConfig()
    out = await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert out["completed"] == ["main.py"]
    rendered = (phase._audit_dir / "main.py.md").read_text(encoding="utf-8")
    # Tokens row (was "0 / 0" pre-fix).
    assert "**Tokens in/out:** 1234 / 567" in rendered
    # Latency row uses int seconds.
    assert "**Latency:** 1s (thinking 1s, output 0s)" in rendered


@pytest.mark.asyncio
async def test_file_metadata_falls_back_to_latency_when_phase_split_unknown(
    tmp_path: Path,
) -> None:
    """When the model doesn't expose phase boundaries (thinking_ms ==
    output_ms == 0), the renderer must still surface the total latency."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    body = json.dumps(_VALID_RESPONSE_DICT)
    response = ChatResponse(
        content=body,
        content_dict=_VALID_RESPONSE_DICT,
        reasoning_content="",
        tool_calls=None,
        finish_reason="stop",
        latency_ms=2000,
        thinking_ms=0,
        output_ms=0,
        prompt_tokens=10,
        completion_tokens=20,
        fingerprint="fp123",
    )
    client = FakeClient(responses=[response])
    phase, bus, cb, captured, state = _setup_phase(tmp_path, client, [f1])
    cfg = SenexConfig()
    await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    rendered = (phase._audit_dir / "main.py.md").read_text(encoding="utf-8")
    # Output collapses to total latency when no split available.
    assert "**Latency:** 2s (thinking 0s, output 2s)" in rendered


@pytest.mark.asyncio
async def test_thinking_trace_not_written_when_disabled(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    f1 = repo / "main.py"
    f1.write_text("x=1\n", encoding="utf-8")

    client = FakeClient(responses=[_make_response(_VALID_RESPONSE_DICT)])
    phase, bus, cb, captured, state = _setup_phase(
        tmp_path, client, [f1], save_traces=False
    )
    cfg = SenexConfig()
    cfg.lmstudio.thinking = cfg.lmstudio.thinking.model_copy(
        update={"save_traces": False}
    )
    await phase.do_work(state, Lens.load("correctness"), cfg, bus, cb)
    assert not (phase._audit_dir / "main.py.thinking.md").exists()
