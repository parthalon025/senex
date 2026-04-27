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
