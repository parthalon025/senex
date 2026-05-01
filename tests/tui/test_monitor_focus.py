"""Tests for Monitor focus restoration (F-10).

WCAG 2.4.3 / 2.4.7 - the Monitor screen must always have a focused
widget; after any modal (ConfirmDialog, HelpScreen) dismiss focus must
return to the focusable host so keybindings continue to function.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from senex.events import CommandBus, EventBus
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


@pytest.mark.asyncio
async def test_monitor_has_focusable_host(tmp_path: Path) -> None:
    """The Monitor compose() must yield a focusable host (#monitor_focus_host)."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_monitor(app)
        await pilot.pause()
        host = screen.query_one("#monitor_focus_host")
        assert host.can_focus is True


@pytest.mark.asyncio
async def test_focus_present_after_monitor_mount(tmp_path: Path) -> None:
    """After the Monitor is pushed, ``app.focused`` is non-None."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_monitor(app)
        # Allow on_screen_resume + focus restore to settle.
        await pilot.pause()
        await pilot.pause()
        assert app.focused is not None


@pytest.mark.asyncio
async def test_focus_restores_after_confirm_dialog_dismiss(
    tmp_path: Path,
) -> None:
    """After ConfirmDialog dismiss (Escape), Monitor focus is restored.

    F-10 acceptance: trigger the skip-confirmation dialog, dismiss it
    with Escape, and verify a focused widget exists on the Monitor.
    """
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_monitor(app)
        # set a current path so action_request_skip pushes the dialog
        screen._current_path = "src/foo.py"
        await pilot.pause()
        await pilot.press("s")  # opens ConfirmDialog
        await pilot.pause()
        await pilot.press("escape")  # dismisses with False
        # Allow callback + focus restore to settle.
        await pilot.pause()
        await pilot.pause()
        assert app.focused is not None
        assert app.focused.id == "monitor_focus_host"


@pytest.mark.asyncio
async def test_focus_restores_after_help_screen_dismiss(tmp_path: Path) -> None:
    """After HelpScreen dismiss (Escape), Monitor focus is restored (F-9)."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_monitor(app)
        await pilot.pause()
        await pilot.press("question_mark")  # opens HelpScreen
        await pilot.pause()
        await pilot.press("escape")  # dismisses help
        await pilot.pause()
        await pilot.pause()
        assert app.focused is not None
        assert app.focused.id == "monitor_focus_host"


@pytest.mark.asyncio
async def test_dismiss_banner_action_is_noop_when_banner_hidden(
    tmp_path: Path,
) -> None:
    """Pressing ``e`` when the banner is hidden does not raise (F-1)."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_monitor(app)
        await pilot.pause()
        # Banner starts hidden.
        from senex.tui.widgets.error_banner import ErrorBannerWidget
        banner = screen.query_one("#error_banner", ErrorBannerWidget)
        assert banner.display is False
        await pilot.press("e")
        await pilot.pause()
        # No crash; banner still hidden.
        assert banner.display is False


@pytest.mark.asyncio
async def test_dismiss_banner_action_hides_visible_banner(tmp_path: Path) -> None:
    """Pressing ``e`` while the banner is shown hides it (F-1)."""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await _push_monitor(app)
        await pilot.pause()
        from senex.tui.widgets.error_banner import ErrorBannerWidget
        banner = screen.query_one("#error_banner", ErrorBannerWidget)
        # Force the banner visible by pretending an error was surfaced.
        banner.show_external_error("synthetic test error")
        await pilot.pause()
        assert banner.display is True
        await pilot.press("e")
        await pilot.pause()
        assert banner.display is False


@pytest.mark.asyncio
async def test_help_screen_opens_on_question_mark(tmp_path: Path) -> None:
    """Pressing ``?`` opens the HelpScreen modal (F-9)."""
    from senex.tui.monitor import HelpScreen

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_monitor(app)
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        # Back on Monitor.
        assert isinstance(app.screen, MonitorScreen)


@pytest.mark.asyncio
async def test_help_screen_dismisses_on_enter(tmp_path: Path) -> None:
    """HelpScreen exits on Enter as well as Escape (F-9)."""
    from senex.tui.monitor import HelpScreen

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _push_monitor(app)
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, MonitorScreen)
