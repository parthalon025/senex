"""Tests for Monitor command-bus keybindings (M9 Task 9.8)."""
from __future__ import annotations

from pathlib import Path

import pytest

from senex.events import Command, CommandBus, EventBus
from senex.tui.app import SenexApp
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


async def _push_monitor(app: SenexApp) -> MonitorScreen:
    screen = MonitorScreen(app.bus, app.command_bus)
    await app.push_screen(screen)
    return screen


async def _drain_command_queue(q) -> list[Command]:
    out: list[Command] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.mark.asyncio
async def test_p_posts_pause_command(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_monitor(app)
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        cmds = await _drain_command_queue(q)
        assert any(c.type == "Pause" for c in cmds)


@pytest.mark.asyncio
async def test_p_second_press_posts_resume(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_monitor(app)
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        cmds = await _drain_command_queue(q)
        types = [c.type for c in cmds]
        assert "Pause" in types
        assert "Resume" in types
        # Pause precedes Resume.
        assert types.index("Pause") < types.index("Resume")


@pytest.mark.asyncio
async def test_q_dialog_dismiss_does_not_post(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_monitor(app)
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        cmds = await _drain_command_queue(q)
        assert not any(c.type == "Quit" for c in cmds)


@pytest.mark.asyncio
async def test_q_confirm_posts_quit(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_monitor(app)
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        cmds = await _drain_command_queue(q)
        assert any(c.type == "Quit" for c in cmds)


@pytest.mark.asyncio
async def test_s_with_confirm_posts_skip_with_target(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_monitor(app)
        screen._current_path = "src/foo.py"
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        cmds = await _drain_command_queue(q)
        skips = [c for c in cmds if c.type == "Skip"]
        assert len(skips) == 1
        assert skips[0].target == "src/foo.py"


@pytest.mark.asyncio
async def test_r_with_confirm_posts_rerun_with_target(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_monitor(app)
        screen._current_path = "src/bar.py"
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        cmds = await _drain_command_queue(q)
        reruns = [c for c in cmds if c.type == "Rerun"]
        assert len(reruns) == 1
        assert reruns[0].target == "src/bar.py"


@pytest.mark.asyncio
async def test_t_toggles_local_no_command(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_monitor(app)
        await pilot.pause()
        before = screen._min_priority_display
        await pilot.press("t")
        await pilot.pause()
        after = screen._min_priority_display
        assert before != after
        cmds = await _drain_command_queue(q)
        # No command on the bus.
        assert not cmds


@pytest.mark.asyncio
async def test_keybindings_inactive_on_launcher(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Still on Launcher; keybindings not active.
        await pilot.press("p")
        await pilot.pause()
        cmds = await _drain_command_queue(q)
        assert not cmds


@pytest.mark.asyncio
async def test_replay_mode_disables_skip_and_rerun(tmp_path: Path) -> None:
    app = _app(tmp_path)
    q = app.command_bus.subscribe()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_monitor(app)
        screen.set_replay_mode(True)
        screen._current_path = "src/foo.py"
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        cmds = await _drain_command_queue(q)
        # Replay mode mutes Skip and Rerun.
        assert not any(c.type in ("Skip", "Rerun") for c in cmds)
