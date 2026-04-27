"""Tests for the Textual ``SenexApp`` skeleton (M9 Task 9.4)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from senex.events import CommandBus, EventBus
from senex.tui.app import SenexApp
from senex.tui.exceptions import WidgetRenderError
from senex.tui.launcher import LauncherScreen
from senex.tui.monitor import MonitorScreen


def _app(tmp_path: Path) -> SenexApp:
    cfg = tmp_path / "senex.config.toml"
    cfg.write_text("", encoding="utf-8")
    return SenexApp(
        config_path=cfg,
        repo_path_default=tmp_path,
        bus=EventBus(),
        command_bus=CommandBus(),
    )


@pytest.mark.asyncio
async def test_senex_app_constructs_with_required_args(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert isinstance(app, SenexApp)
    assert app._config_path.exists()


@pytest.mark.asyncio
async def test_senex_app_on_mount_pushes_launcher(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(pilot.app.screen, LauncherScreen)


@pytest.mark.asyncio
async def test_senex_app_start_audit_pushes_monitor_and_spawns_task(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)

    async def fake_audit(**kwargs: object) -> int:
        await asyncio.sleep(0.5)
        return 0

    app._audit_runner = fake_audit  # type: ignore[assignment]
    async with app.run_test() as pilot:
        await pilot.pause()
        app.start_audit_for_test()
        await pilot.pause()
        assert isinstance(pilot.app.screen, MonitorScreen)
        assert app._audit_task is not None
        assert not app._audit_task.done()


@pytest.mark.asyncio
async def test_senex_app_audit_task_exception_renders_error_screen(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)

    async def boom_audit(**kwargs: object) -> int:
        raise RuntimeError("boom")

    app._audit_runner = boom_audit  # type: ignore[assignment]
    async with app.run_test() as pilot:
        await pilot.pause()
        app.start_audit_for_test()
        # Allow exception + done callback to fire.
        for _ in range(10):
            await pilot.pause()
            if app._audit_task is not None and app._audit_task.done():
                break
        assert app._audit_task is not None
        assert app._audit_task.done()
        assert app._last_error_message is not None
        assert "boom" in app._last_error_message


@pytest.mark.asyncio
async def test_senex_app_ctrl_q_cancels_audit_task(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def long_audit(**kwargs: object) -> int:
        await asyncio.sleep(60)
        return 0

    app._audit_runner = long_audit  # type: ignore[assignment]
    async with app.run_test() as pilot:
        await pilot.pause()
        app.start_audit_for_test()
        await pilot.pause()
        await pilot.press("ctrl+q")
        for _ in range(10):
            await pilot.pause()
            if app._audit_task is not None and app._audit_task.done():
                break
        assert app._audit_task is not None
        assert app._audit_task.cancelled() or app._audit_task.done()


@pytest.mark.asyncio
async def test_widget_render_error_is_named() -> None:
    assert issubclass(WidgetRenderError, Exception)
    err = WidgetRenderError("render failed")
    assert "render failed" in str(err)


@pytest.mark.asyncio
async def test_senex_app_in_replay_mode_factory(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    app = SenexApp.in_replay_mode(audit_dir=audit, truncated=True)
    assert app.replay_mode is True
    assert app._truncated_replay is True
    assert app._config_path == audit / "config.snapshot.toml"


@pytest.mark.asyncio
async def test_senex_app_handle_widget_error_records_message(tmp_path: Path) -> None:
    app = _app(tmp_path)
    err = WidgetRenderError("widget x failed")
    app._handle_widget_error(err)
    assert app._last_error_message is not None
    assert "widget x failed" in app._last_error_message


@pytest.mark.asyncio
async def test_senex_app_start_audit_with_runtime_config(tmp_path: Path) -> None:
    """Cover the production start_audit path that consumes a RuntimeConfig."""
    from senex.config import SenexConfig
    from senex.lens import Lens
    from senex.tui.runtime import RuntimeConfig

    app = _app(tmp_path)
    captured: list[dict[str, object]] = []

    async def fake_audit(**kwargs: object) -> int:
        captured.append(kwargs)
        return 0

    app._audit_runner = fake_audit  # type: ignore[assignment]

    rt = RuntimeConfig(
        repo=tmp_path,
        config=SenexConfig(),
        lens=Lens.load("correctness"),
        config_path=tmp_path / "senex.config.toml",
        output_root=tmp_path / "audits",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.start_audit(rt)
        await pilot.pause()
        # Wait for task completion.
        for _ in range(20):
            await pilot.pause()
            if app._audit_task is not None and app._audit_task.done():
                break
        assert len(captured) == 1
        assert captured[0]["repo"] == tmp_path
