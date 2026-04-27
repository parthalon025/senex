"""Tests for ``LauncherScreen`` (M9 Task 9.5)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import Button, Input, Select, Static, Switch

from senex.events import CommandBus, EventBus
from senex.lmstudio_client import LoadedModelInfo
from senex.tui.app import SenexApp
from senex.tui.launcher import LauncherScreen


def _app(tmp_path: Path) -> SenexApp:
    cfg = tmp_path / "senex.config.toml"
    cfg.write_text(
        f'[output]\nroot = "{tmp_path / "audits"}"\n'.replace("\\", "\\\\"),
        encoding="utf-8",
    )
    return SenexApp(
        config_path=cfg,
        repo_path_default=tmp_path,
        bus=EventBus(),
        command_bus=CommandBus(),
    )


@pytest.mark.asyncio
async def test_launcher_renders_all_form_fields(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        for wid in (
            "#repo_path",
            "#lens_select",
            "#model_select",
            "#temperature",
            "#max_tokens",
            "#seed",
            "#include_tests",
            "#save_traces",
            "#start_btn",
            "#reset_btn",
        ):
            assert pilot.app.screen.query_one(wid) is not None


@pytest.mark.asyncio
async def test_launcher_repo_path_input_typing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        repo_input = screen.query_one("#repo_path", Input)
        repo_input.value = "/tmp/my-repo"
        assert repo_input.value == "/tmp/my-repo"


@pytest.mark.asyncio
async def test_launcher_model_dropdown_populated_from_lms(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def fake_models(self: LauncherScreen) -> list[str]:
        return ["model-a", "model-b"]

    with patch.object(LauncherScreen, "_fetch_loaded_models", fake_models):
        async with app.run_test() as pilot:
            await pilot.pause()
            select = pilot.app.screen.query_one("#model_select", Select)
            # Allow the on_mount populate task a moment.
            await pilot.pause()
            opts = [v for _, v in select._options]
            assert "model-a" in opts


@pytest.mark.asyncio
async def test_launcher_unreachable_lms_shows_placeholder(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def fake_models(self: LauncherScreen) -> list[str]:
        from senex.lmstudio_errors import LMSConnectionLost
        raise LMSConnectionLost("connection refused")

    with patch.object(LauncherScreen, "_fetch_loaded_models", fake_models):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, LauncherScreen)
            select = screen.query_one("#model_select", Select)
            assert select.value == "[no LM Studio]"
            start_btn = screen.query_one("#start_btn", Button)
            assert start_btn.disabled is True


@pytest.mark.asyncio
async def test_launcher_resume_detection_finds_existing_dirs(tmp_path: Path) -> None:
    audits_root = tmp_path / "audits" / tmp_path.name
    prior = audits_root / "2026-04-26-abc123"
    prior.mkdir(parents=True)
    (prior / "checkpoint.json").write_text("{}", encoding="utf-8")

    app = _app(tmp_path)

    async def empty_models(self: LauncherScreen) -> list[str]:
        return ["m"]

    with patch.object(LauncherScreen, "_fetch_loaded_models", empty_models):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, LauncherScreen)
            assert len(screen._resumable_dirs) == 1
            resume_btn = screen.query_one("#resume_btn", Button)
            assert resume_btn.display is True


@pytest.mark.asyncio
async def test_launcher_no_prior_runs_hides_resume_button(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def models(self: LauncherScreen) -> list[str]:
        return ["m"]

    with patch.object(LauncherScreen, "_fetch_loaded_models", models):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            resume_btn = screen.query_one("#resume_btn", Button)
            assert resume_btn.display is False


@pytest.mark.asyncio
async def test_launcher_invalid_repo_path_shows_error(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def models(self: LauncherScreen) -> list[str]:
        return ["m"]

    with patch.object(LauncherScreen, "_fetch_loaded_models", models):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            screen.query_one("#repo_path", Input).value = str(
                tmp_path / "does_not_exist"
            )
            await screen._submit()
            err = screen.query_one("#error_label", Static)
            assert "repo path" in str(err.render())


@pytest.mark.asyncio
async def test_launcher_reset_restores_defaults(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def models(self: LauncherScreen) -> list[str]:
        return ["m"]

    with patch.object(LauncherScreen, "_fetch_loaded_models", models):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            screen.query_one("#repo_path", Input).value = "/tmp/foo"
            screen.query_one("#temperature", Input).value = "0.99"
            screen.query_one("#include_tests", Switch).value = True
            await screen._reset()
            assert screen.query_one("#repo_path", Input).value == str(tmp_path)
            assert screen.query_one("#temperature", Input).value == "0.6"
            assert screen.query_one("#include_tests", Switch).value is False


@pytest.mark.asyncio
async def test_launcher_start_button_calls_app_start_audit(tmp_path: Path) -> None:
    app = _app(tmp_path)
    captured: list[object] = []

    def fake_start_audit(self: SenexApp, rt: object) -> None:
        captured.append(rt)

    async def models(self: LauncherScreen) -> list[str]:
        return ["m"]

    with patch.object(LauncherScreen, "_fetch_loaded_models", models):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, LauncherScreen)
            screen.query_one("#repo_path", Input).value = str(tmp_path)
            with patch.object(SenexApp, "start_audit", fake_start_audit):
                await screen._submit()
            assert len(captured) == 1
            from senex.tui.runtime import RuntimeConfig

            assert isinstance(captured[0], RuntimeConfig)
            assert captured[0].repo == tmp_path.resolve()


@pytest.mark.asyncio
async def test_launcher_loaded_model_info_compatibility() -> None:
    """Sanity check: LoadedModelInfo carries an ``id`` field used by Launcher."""
    info = LoadedModelInfo(id="google/gemma-4-26b-a4b")
    assert info.id == "google/gemma-4-26b-a4b"


@pytest.mark.asyncio
async def test_launcher_resume_button_invokes_submit_with_resume_flag(
    tmp_path: Path,
) -> None:
    audits_root = tmp_path / "audits" / tmp_path.name
    prior = audits_root / "2026-04-26-abc"
    prior.mkdir(parents=True)
    (prior / "checkpoint.json").write_text("{}", encoding="utf-8")

    app = _app(tmp_path)
    captured: list[bool] = []

    async def models(self: LauncherScreen) -> list[str]:
        return ["m"]

    async def fake_submit(self: LauncherScreen, resume: bool = False) -> None:
        captured.append(resume)

    with patch.object(LauncherScreen, "_fetch_loaded_models", models), patch.object(
        LauncherScreen, "_submit", fake_submit
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            from textual.widgets._button import Button as _Btn

            await screen.on_button_pressed(
                _Btn.Pressed(screen.query_one("#resume_btn", Button))
            )
            assert captured == [True]


@pytest.mark.asyncio
async def test_launcher_collect_overrides_invalid_inputs_raise(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def models(self: LauncherScreen) -> list[str]:
        return ["m"]

    with patch.object(LauncherScreen, "_fetch_loaded_models", models):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, LauncherScreen)
            screen.query_one("#temperature", Input).value = "not-a-number"
            with pytest.raises(ValueError):
                screen._collect_overrides()


@pytest.mark.asyncio
async def test_launcher_submit_blocks_when_lms_unavailable(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async def boom(self: LauncherScreen) -> list[str]:
        raise RuntimeError("LMS down")

    with patch.object(LauncherScreen, "_fetch_loaded_models", boom):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, LauncherScreen)
            await screen._submit()
            err = screen.query_one("#error_label", Static)
            assert "LM Studio unavailable" in str(err.render())
