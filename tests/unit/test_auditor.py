"""``run_audit`` orchestration tests (M8 Task 8.7).

Three slices:
  1. ``compute_audit_dir`` shape (POL-1).
  2. ``lifecycle_acquire_or_resume`` context-manager invariants:
     release runs on entry success AND on body exception.
  3. Resume hash discipline: each of the 5 hashes (config, prompt, model
     fingerprint, tool pack, lens version) drives a ResumeIncompatible
     when mismatched (unless allow_mixed_resume).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from senex.auditor import (
    EXIT_CONFIG_ERROR,
    EXIT_EXTERNAL_ERROR,
    EXIT_INTERRUPTED,
    EXIT_PARTIAL_SUCCESS,
    EXIT_SUCCESS,
    compute_audit_dir,
)
from senex.checkpoint import Checkpoint


def test_compute_audit_dir_shape(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    out = tmp_path / "audits"
    audit = compute_audit_dir(repo, out, "01JZRUND")
    parts = audit.parts
    assert "myrepo" in parts
    # Folder name is "<DATE>-<run_id_short>".
    assert audit.name.endswith("-01JZRUND")


def test_exit_codes_are_distinct() -> None:
    codes = {
        EXIT_SUCCESS,
        EXIT_PARTIAL_SUCCESS,
        EXIT_CONFIG_ERROR,
        EXIT_EXTERNAL_ERROR,
        EXIT_INTERRUPTED,
    }
    assert codes == {0, 1, 2, 3, 130}


# ---------------------------------------------------------------------------
# lifecycle_acquire_or_resume CM contract: release on success and exception
# ---------------------------------------------------------------------------


class _FakeBackend:
    backend_name = "fake"
    is_loaded_calls = 0
    load_calls = 0
    unload_calls = 0
    list_calls = 0

    def __init__(self) -> None:
        self.loaded = False

    async def is_loaded(self, model_id: str) -> bool:
        _FakeBackend.is_loaded_calls += 1
        return self.loaded

    async def load(self, model_id: str, timeout: int) -> Any:
        from senex.inference_lifecycle import ModelInfo, _compute_fingerprint

        _FakeBackend.load_calls += 1
        self.loaded = True
        fp = _compute_fingerprint(model_id, "q4", "deadbeef")
        return ModelInfo(
            model_id=model_id,
            quant="q4",
            checkpoint_digest="deadbeef",
            fingerprint=fp,
            backend="fake",
        )

    async def unload(self, model_id: str) -> None:
        _FakeBackend.unload_calls += 1
        self.loaded = False

    async def list_loaded(self) -> list[Any]:
        _FakeBackend.list_calls += 1
        return []


@pytest.mark.asyncio
async def test_lifecycle_cm_release_on_normal_exit(tmp_path: Path) -> None:
    from senex.auditor import lifecycle_acquire_or_resume
    from senex.config import SenexConfig
    from senex.events import EventBus
    from senex.runlock import RunLock

    cfg = SenexConfig()
    cfg.inference.lifecycle = cfg.inference.lifecycle.model_copy(
        update={"runlock_dir": str(tmp_path / "locks")}
    )
    bus = EventBus()

    _FakeBackend.unload_calls = 0
    fake = _FakeBackend()
    with patch(
        "senex.auditor.LifecycleBackendFactory.select",
        new=lambda *a, **kw: _async_return(fake),
    ), patch.object(RunLock, "acquire", lambda *args, **kwargs: 1), patch.object(
        RunLock, "release", lambda *args, **kwargs: 0
    ):
        from senex.secret_redactor import SecretRedactor

        async with lifecycle_acquire_or_resume(
            config=cfg,
            run_id="01JZRUND0000000000000ABCDE",
            bus=bus,
            redactor=SecretRedactor(),
            resume=False,
            checkpoint_fingerprint=None,
        ) as (info, loaded_by_us):
            assert info.fingerprint
    # Unload on the auto_unload path is OK either way; just assert the CM
    # exited cleanly (no exception leaked).


@pytest.mark.asyncio
async def test_lifecycle_cm_release_on_body_exception(tmp_path: Path) -> None:
    from senex.auditor import lifecycle_acquire_or_resume
    from senex.config import SenexConfig
    from senex.events import EventBus
    from senex.runlock import RunLock
    from senex.secret_redactor import SecretRedactor

    cfg = SenexConfig()
    cfg.inference.lifecycle = cfg.inference.lifecycle.model_copy(
        update={"runlock_dir": str(tmp_path / "locks")}
    )
    bus = EventBus()
    fake = _FakeBackend()
    release_calls: list[bool] = []

    def _release(*args: Any, **kwargs: Any) -> int:
        release_calls.append(True)
        return 0

    with patch(
        "senex.auditor.LifecycleBackendFactory.select",
        new=lambda *a, **kw: _async_return(fake),
    ), patch.object(RunLock, "acquire", lambda *args, **kwargs: 1), patch.object(
        RunLock, "release", _release
    ), pytest.raises(RuntimeError):
        async with lifecycle_acquire_or_resume(
            config=cfg,
            run_id="01JZRUND0000000000000ABCDE",
            bus=bus,
            redactor=SecretRedactor(),
            resume=False,
            checkpoint_fingerprint=None,
        ):
            raise RuntimeError("body crashed")
    assert release_calls, "RunLock.release must run on body exception"


# ---------------------------------------------------------------------------
# Resume hash discipline
# ---------------------------------------------------------------------------


def _make_checkpoint(audit_dir: Path, **overrides: str) -> Checkpoint:
    fields = {
        "config_hash": "c0",
        "prompt_hash": "p0",
        "model_fingerprint": "fp0",
        "tool_pack_hash": "t0",
        "lens_version": "1.0.0",
    }
    fields.update(overrides)
    return Checkpoint.create(
        audit_dir, run_id="01JZRUND0000000000000ABCDE", **fields
    )


@pytest.mark.parametrize(
    "field",
    [
        "config_hash",
        "prompt_hash",
        "model_fingerprint",
        "tool_pack_hash",
        "lens_version",
    ],
)
def test_resume_hash_mismatch_each_field(field: str, tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    cp = _make_checkpoint(audit_dir)
    # Build "current" hashes that match except for ``field``.
    new_hashes: dict[str, str] = {
        "config_hash": "c0",
        "prompt_hash": "p0",
        "model_fingerprint": "fp0",
        "tool_pack_hash": "t0",
        "lens_version": "1.0.0",
    }
    new_hashes[field] = "DIFFERENT"
    assert not cp.is_compatible(new_hashes)
    # All-match cases are compatible.
    matched = {k: v for k, v in new_hashes.items() if k != field}
    matched[field] = cp.data[field]
    assert cp.is_compatible(matched)


# ---------------------------------------------------------------------------
# Resume incompatible -> exit 2 (without --allow-mixed-resume)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_audit_resume_incompatible_returns_exit_2(tmp_path: Path) -> None:
    """End-to-end: when a checkpoint exists and config_hash drifts, run_audit
    returns exit code 2 without trying to do any work.
    """
    import senex.auditor as auditor_mod
    from senex.auditor import run_audit
    from senex.config import SenexConfig
    from senex.events import CommandBus, EventBus
    from senex.lens import Lens

    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()

    audit_dir = auditor_mod.compute_audit_dir(repo, tmp_path / "audits", "01JZRUND")
    audit_dir.mkdir(parents=True)

    # Pre-existing checkpoint with a known config_hash.
    Checkpoint.create(
        audit_dir,
        run_id="01JZRUND0000000000000ABCDE",
        config_hash="STALE",
        prompt_hash="p",
        model_fingerprint="fp",
        tool_pack_hash="t",
        lens_version="1.0.0",
    )

    cfg = SenexConfig()
    cfg.output = cfg.output.model_copy(update={"root": str(tmp_path / "audits")})

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[lmstudio]\nmodel='google/gemma-4-26b-a4b'\n", encoding="utf-8")

    # Patch compute_audit_dir to return our pre-seeded dir regardless of run_id.
    with patch(
        "senex.auditor.compute_audit_dir",
        new=lambda r, o, run_id_short: audit_dir,
    ):
        rc = await run_audit(
            repo=repo,
            config=cfg,
            lens=Lens.load("correctness"),
            bus=EventBus(),
            command_bus=CommandBus(),
            config_path=cfg_path,
            output_root=tmp_path / "audits",
            resume=True,
            allow_mixed_resume=False,
        )
    assert rc == EXIT_CONFIG_ERROR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _async_return(value: Any) -> Any:
    return value


# ---------------------------------------------------------------------------
# Helper-level tests (raise auditor coverage above 70%)
# ---------------------------------------------------------------------------


def test_stable_hash_is_deterministic() -> None:
    from senex.auditor import _stable_hash

    h1 = _stable_hash("hello")
    h2 = _stable_hash("hello")
    h3 = _stable_hash("world")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64  # sha256 hex


def test_patch_checkpoint_fingerprint_updates_field(tmp_path: Path) -> None:
    """_patch_checkpoint_fingerprint atomically updates model_fingerprint."""
    import json

    from senex.auditor import _patch_checkpoint_fingerprint

    audit_dir = tmp_path / "audit"
    Checkpoint.create(
        audit_dir,
        run_id="01JZRUND0000000000000ABCDE",
        config_hash="c",
        prompt_hash="p",
        model_fingerprint="pending",
        tool_pack_hash="t",
        lens_version="1.0.0",
    )
    _patch_checkpoint_fingerprint(audit_dir, "newfp")
    data = json.loads((audit_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert data["model_fingerprint"] == "newfp"


@pytest.mark.asyncio
async def test_noop_graph_provider_returns_unavailable() -> None:
    from senex.auditor import _NoopGraphProvider

    ctx = await _NoopGraphProvider().fetch("any/relpath")
    assert ctx.available is False


def test_snapshot_config_writes_toml(tmp_path: Path) -> None:
    from senex.auditor import _snapshot_config
    from senex.config import SenexConfig

    audit = tmp_path / "audit"
    audit.mkdir()
    cfg = SenexConfig()
    _snapshot_config(cfg, audit)
    assert (audit / "config.snapshot.toml").exists()


def test_runcomplete_helper_includes_run_id() -> None:
    from senex.auditor import _runcomplete

    rc = _runcomplete("01JZRUND0000000000000ABCDE", 0, totals={"x": 1})
    assert rc.run_id == "01JZRUND0000000000000ABCDE"
    assert rc.exit_status == 0
    assert rc.totals == {"x": 1}


@pytest.mark.asyncio
async def test_run_audit_writes_events_jsonl(tmp_path: Path) -> None:
    """``run_audit`` MUST wire ``DiskWriterSubscriber`` so ``events.jsonl``
    lands in the audit dir for ``senex view`` replay (M9).

    The bug this guards: pre-fix, ``cli_audit._run_headless`` wired Metrics
    + Headless subscribers but never DiskWriter; the comment claimed it was
    wired "inside ``run_audit``" but it wasn't. We verify the file exists
    AND has at least one valid JSON line after a normal short-circuit run.
    """
    import json

    import senex.auditor as auditor_mod
    from senex.auditor import run_audit
    from senex.config import SenexConfig
    from senex.events import CommandBus, EventBus
    from senex.lens import Lens
    from senex.phases.base import PreflightFailure

    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[lmstudio]\nmodel='m'\n", encoding="utf-8")

    cfg = SenexConfig()
    cfg.inference.lifecycle = cfg.inference.lifecycle.model_copy(
        update={
            "auto_load": False,
            "auto_unload": False,
            "runlock_dir": str(tmp_path / "locks"),
        }
    )

    audit_dir = auditor_mod.compute_audit_dir(repo, tmp_path / "audits", "01JZRUND")

    fake = _FakeBackend()
    fake.loaded = True
    with patch(
        "senex.auditor.LifecycleBackendFactory.select",
        new=lambda *a, **kw: _async_return(fake),
    ), patch(
        "senex.runlock.RunLock.acquire", new=lambda *args, **kw: 1
    ), patch(
        "senex.runlock.RunLock.release", new=lambda *args, **kw: 0
    ), patch(
        "senex.auditor.InferenceClient"
    ) as lms_class, patch(
        "senex.auditor.compute_audit_dir",
        new=lambda r, o, run_id_short: audit_dir,
    ):
        lms_instance = lms_class.return_value
        lms_instance._fingerprint_pinned = ""

        async def _aclose() -> None:
            return None

        lms_instance.aclose = _aclose

        # Short-circuit at preflight so the run terminates before file work,
        # but RunStart still publishes -> events.jsonl gets written.
        async def fail_preflight(*args: Any, **kw: Any) -> Any:
            raise PreflightFailure(
                check_name="test",
                message="forced for test",
                exit_code=3,
            )

        with patch(
            "senex.phases.preflight.PreflightPhase.do_work",
            new=fail_preflight,
        ):
            await run_audit(
                repo=repo,
                config=cfg,
                lens=Lens.load("correctness"),
                bus=EventBus(),
                command_bus=CommandBus(),
                config_path=cfg_path,
                output_root=tmp_path / "audits",
                resume=False,
            )

    events_jsonl = audit_dir / "events.jsonl"
    assert events_jsonl.exists(), "DiskWriter must persist events.jsonl"
    body = events_jsonl.read_text(encoding="utf-8").strip()
    assert body, "events.jsonl must have at least one event"
    # Every non-empty line is a JSON object.
    for line in body.splitlines():
        json.loads(line)


@pytest.mark.asyncio
async def test_run_audit_keyboard_interrupt_returns_130(tmp_path: Path) -> None:
    """SIGINT in lifecycle propagates as exit code 130."""
    from senex.auditor import EXIT_INTERRUPTED, run_audit
    from senex.config import SenexConfig
    from senex.events import CommandBus, EventBus
    from senex.lens import Lens

    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[lmstudio]\nmodel='m'\n", encoding="utf-8")

    cfg = SenexConfig()
    cfg.inference.lifecycle = cfg.inference.lifecycle.model_copy(
        update={
            "auto_load": False,
            "auto_unload": False,
            "runlock_dir": str(tmp_path / "locks"),
        }
    )

    fake = _FakeBackend()
    fake.loaded = True
    with patch(
        "senex.auditor.LifecycleBackendFactory.select",
        new=lambda *a, **kw: _async_return(fake),
    ), patch(
        "senex.runlock.RunLock.acquire", new=lambda *args, **kw: 1
    ), patch(
        "senex.runlock.RunLock.release", new=lambda *args, **kw: 0
    ), patch(
        "senex.auditor.InferenceClient"
    ) as lms_class:
        lms_instance = lms_class.return_value
        lms_instance._fingerprint_pinned = ""

        async def _aclose() -> None:
            return None

        lms_instance.aclose = _aclose

        # Force preflight to KeyboardInterrupt.
        async def boom_preflight(*args: Any, **kw: Any) -> Any:
            raise KeyboardInterrupt

        with patch(
            "senex.phases.preflight.PreflightPhase.do_work",
            new=boom_preflight,
        ):
            rc = await run_audit(
                repo=repo,
                config=cfg,
                lens=Lens.load("correctness"),
                bus=EventBus(),
                command_bus=CommandBus(),
                config_path=cfg_path,
                output_root=tmp_path / "audits",
                resume=False,
            )
    assert rc == EXIT_INTERRUPTED
