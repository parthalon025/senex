"""senex.tui.launcher — Launcher screen (M9 Task 9.5).

Implements spec §5.7 Launcher fields: repo path, lens, model dropdown,
sampling overrides, resume detection. Submitting "Start audit" emits a
``RuntimeConfig`` and calls ``app.start_audit``.

M11 addition: a "Scan disk for repos" button that delegates to
``senex.cli_wizard._discover_repos_on_disk`` to populate ``#repo_select``
with candidates found under the configured ``[ui].scan_root`` (default
``Path.home()``).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from senex.cli_wizard import _discover_repos_on_disk
from senex.config import SenexConfig, load_config
from senex.lens import Lens, LensNotFound

log = logging.getLogger(__name__)


_NO_LMS = "[no inference server]"


class LauncherScreen(Screen[None]):
    """Launcher form (spec §5.7).

    Fields (each via a Textual ``#id``):
      * ``#repo_path`` — Input, default = ``repo_path_default``
      * ``#lens_select`` — Select with available lenses
      * ``#model_select`` — Select populated from
        ``client.list_loaded_models()``; falls back to ``[no LM Studio]``
        and disables the Start button
      * ``#temperature``, ``#max_tokens``, ``#seed`` — numeric Inputs
      * ``#include_tests``, ``#save_traces`` — Switches
      * ``#resume_btn`` — visible only if a prior audit dir exists
      * ``#start_btn``, ``#reset_btn`` — Buttons
      * ``#error_label`` — surfaces validation errors
    """

    DEFAULT_CSS = """
    LauncherScreen { align: center middle; }
    LauncherScreen #form { width: 80%; height: auto; padding: 1 2; border: round $primary; }
    LauncherScreen #error_label { color: $error; }
    LauncherScreen Label { padding: 0 1; }
    """

    def __init__(self, config_path: Path, repo_path_default: Path) -> None:
        super().__init__()
        self._config_path = config_path
        self._repo_path_default = repo_path_default
        self._config: SenexConfig | None = None
        self._lms_available: bool = True
        self._resumable_dirs: list[Path] = []

    def compose(self) -> ComposeResult:
        with Container(id="form"):
            yield Static("senex — Launcher", id="title")
            yield Label("Repo path:")
            yield Input(value=str(self._repo_path_default), id="repo_path")
            yield Label("Configured repos:")
            # Pre-seeded with a placeholder; refreshed in on_mount once config
            # is loaded. allow_blank lets the user keep typing into #repo_path
            # without committing to one of the listed candidates.
            yield Select(
                options=[("(none)", "(none)")],
                value="(none)",
                id="repo_select",
                allow_blank=True,
            )
            with Horizontal():
                yield Button("Scan disk for repos", id="scan_btn")
                yield Static("", id="scan_status")
            yield Label("Lens:")
            yield Select(
                options=[("correctness", "correctness")],
                value="correctness",
                id="lens_select",
                allow_blank=False,
            )
            yield Label("Model:")
            yield Select(
                options=[(_NO_LMS, _NO_LMS)],
                value=_NO_LMS,
                id="model_select",
                allow_blank=False,
            )
            yield Label("Temperature:")
            yield Input(value="0.6", id="temperature")
            yield Label("Max tokens:")
            yield Input(value="8192", id="max_tokens")
            yield Label("Seed:")
            yield Input(value="42", id="seed")
            with Horizontal():
                yield Label("Include tests:")
                yield Switch(value=False, id="include_tests")
                yield Label("Save traces:")
                yield Switch(value=True, id="save_traces")
            yield Static("", id="resume_info")
            with Horizontal():
                yield Button("Resume", id="resume_btn")
                yield Button("Reset", id="reset_btn")
                yield Button("Start audit", id="start_btn")
            yield Static("", id="error_label")

    async def on_mount(self) -> None:
        # Load config (best-effort; show error label on failure).
        try:
            self._config = load_config(self._config_path)
        except FileNotFoundError:
            self._config = SenexConfig()
        except Exception as exc:  # noqa: BLE001 — surfaced via #error_label
            self._set_error(f"config load failed: {exc}")
            self._config = SenexConfig()

        self._populate_repo_select()
        await self._populate_models()
        await self._detect_resumable_runs()

    def _populate_repo_select(self) -> None:
        """Seed ``#repo_select`` from ``config.repos``; falls back to ``(none)``."""
        try:
            select = self.query_one("#repo_select", Select)
        except Exception:  # noqa: BLE001 — DOM may not be ready yet (defensive).
            return
        if self._config is None or not self._config.repos:
            select.set_options([("(none)", "(none)")])
            return
        opts: list[tuple[str, str]] = [
            (f"{r.name} ({r.path})", str(r.path)) for r in self._config.repos
        ]
        select.set_options(opts)

    async def _populate_models(self) -> None:
        select = self.query_one("#model_select", Select)
        try:
            models = await self._fetch_loaded_models()
        except Exception:  # noqa: BLE001 — see _NO_LMS placeholder.
            select.set_options([(_NO_LMS, _NO_LMS)])
            select.value = _NO_LMS
            self._lms_available = False
            self._disable_start()
            return

        if not models:
            select.set_options([(_NO_LMS, _NO_LMS)])
            select.value = _NO_LMS
            self._lms_available = False
            self._disable_start()
            return

        opts: list[tuple[str, str]] = [(m, m) for m in models]
        select.set_options(opts)
        select.value = models[0]
        self._lms_available = True

    async def _fetch_loaded_models(self) -> list[str]:
        """Indirection layer so tests can patch without spinning up a client."""
        if self._config is None:
            return []
        from senex.lmstudio_client import LMStudioClient
        from senex.secret_redactor import SecretRedactor
        from senex.events import EventBus

        client = LMStudioClient(
            config=self._config.lmstudio,
            bus=EventBus(),
            redactor=SecretRedactor(),
        )
        try:
            loaded = await client.list_loaded_models()
            return [m.id for m in loaded]
        finally:
            await client.aclose()

    async def _detect_resumable_runs(self) -> None:
        if self._config is None:
            return
        repo_path = Path(self.query_one("#repo_path", Input).value)
        output_root = Path(self._config.output.root) / repo_path.name
        info = self.query_one("#resume_info", Static)
        resume_btn = self.query_one("#resume_btn", Button)
        if not output_root.exists():
            self._resumable_dirs = []
            info.update("")
            resume_btn.display = False
            return
        candidates = sorted(
            [d for d in output_root.iterdir() if d.is_dir() and (d / "checkpoint.json").exists()],
            reverse=True,
        )
        self._resumable_dirs = candidates
        if not candidates:
            info.update("")
            resume_btn.display = False
        else:
            info.update(f"{len(candidates)} prior run(s) detected")
            resume_btn.display = True

    def _disable_start(self) -> None:
        try:
            btn = self.query_one("#start_btn", Button)
            btn.disabled = True
        except Exception:  # noqa: BLE001 — DOM not yet mounted in tests.
            return

    def _set_error(self, msg: str) -> None:
        try:
            self.query_one("#error_label", Static).update(msg)
        except Exception:  # noqa: BLE001 — DOM may not be ready.
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "start_btn":
            await self._submit()
        elif bid == "reset_btn":
            await self._reset()
        elif bid == "resume_btn":
            await self._submit(resume=True)
        elif bid == "scan_btn":
            await self.on_button_scan_repos_pressed()

    async def on_select_changed(self, event: Select.Changed) -> None:
        """Sync ``#repo_select`` picks into ``#repo_path`` so submission works."""
        if event.select.id != "repo_select":
            return
        value = event.value
        # Ignore the placeholder, blank, or non-string sentinel values.
        if not isinstance(value, str) or value in ("(none)", ""):
            return
        try:
            self.query_one("#repo_path", Input).value = value
        except Exception:  # noqa: BLE001 — DOM not yet mounted.
            pass

    async def on_button_scan_repos_pressed(self) -> None:
        """Scan ``[ui].scan_root`` for git working trees; append to ``#repo_select``.

        Reuses :func:`senex.cli_wizard._discover_repos_on_disk` — that helper
        is already exercised by the wizard's unit tests, including depth
        bounding, exclusion list (``node_modules``, ``.venv`` etc.), and
        worktree-pointer-file detection. Surfaces a status line in
        ``#scan_status`` summarizing the result.

        ``scan_root`` defaults to ``Path.home()`` when the config sets it to
        ``None`` (``[ui].scan_root`` omitted) so a fresh install just works.
        """
        try:
            select = self.query_one("#repo_select", Select)
            status = self.query_one("#scan_status", Static)
        except Exception:  # noqa: BLE001 — DOM not yet mounted.
            return

        cfg = self._config or SenexConfig()
        ui = cfg.ui
        root = Path(ui.scan_root).expanduser() if ui.scan_root else Path.home()

        status.update("Scanning...")
        # Force one render tick before the (synchronous) walk so the user
        # sees "Scanning..." while we're in the OS walker. ``_discover`` is
        # fast for typical home-dir scope (well under a second on SSD), so
        # we deliberately don't push it to a worker thread.
        try:
            self.app.refresh()
        except Exception:  # noqa: BLE001 — defensive in detached test contexts.
            pass

        try:
            discovered = _discover_repos_on_disk(root, ui.scan_max_depth)
        except (OSError, PermissionError) as exc:
            status.update(f"Scan failed: {exc}")
            return

        # Dedupe against already-listed paths. ``select._options`` exposes the
        # current option list as ``(label, value)`` pairs; we compare on the
        # value (= absolute path string).
        existing_values = {v for _, v in select._options}
        new_opts: list[tuple[str, str]] = []
        for d in discovered:
            value = str(d)
            if value in existing_values:
                continue
            new_opts.append((f"{d.name} ({value})", value))
            existing_values.add(value)

        if new_opts:
            combined = list(select._options) + new_opts
            select.set_options(combined)
            count = len(new_opts)
            noun = "candidate" if count == 1 else "candidates"
            status.update(f"Found {count} new {noun}")
        else:
            status.update("No new candidates found")

    async def _submit(self, resume: bool = False) -> None:
        if not self._lms_available:
            self._set_error("Inference server unavailable — cannot start audit")
            return

        repo_str = self.query_one("#repo_path", Input).value
        repo_path = Path(repo_str).resolve()
        if not repo_path.exists():
            self._set_error(f"repo path does not exist: {repo_path}")
            return

        # Lens load.
        lens_value = self.query_one("#lens_select", Select).value
        if not isinstance(lens_value, str):
            self._set_error("invalid lens selection")
            return
        try:
            lens = Lens.load(lens_value)
        except LensNotFound as exc:
            self._set_error(f"lens not found: {exc}")
            return

        cfg = self._config or SenexConfig()
        # Apply form overrides into a fresh config.
        cli_overrides = self._collect_overrides()

        from senex.config import resolve_config
        from senex.tui.runtime import RuntimeConfig

        try:
            resolved = resolve_config(cfg, repo_path, cli_overrides, {})
        except Exception as exc:  # noqa: BLE001 — surfaced to user
            self._set_error(f"config validation failed: {exc}")
            return

        rt = RuntimeConfig(
            repo=repo_path,
            config=resolved,
            lens=lens,
            config_path=self._config_path,
            output_root=Path(resolved.output.root),
            resume=resume,
            cli_overrides=cli_overrides,
        )
        # Hand off to the app.
        from senex.tui.app import SenexApp

        app = self.app
        if isinstance(app, SenexApp):
            app.start_audit(rt)

    def _collect_overrides(self) -> dict[str, Any]:
        try:
            temp = float(self.query_one("#temperature", Input).value)
            max_t = int(self.query_one("#max_tokens", Input).value)
            seed = int(self.query_one("#seed", Input).value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid sampling input: {exc}") from exc
        model_value = self.query_one("#model_select", Select).value
        return {
            "lmstudio": {
                "model": model_value if isinstance(model_value, str) else "",
                "sampling": {
                    "temperature": temp,
                    "max_tokens": max_t,
                    "seed": seed,
                },
            },
            "lens": {
                "include_tests": self.query_one("#include_tests", Switch).value,
            },
        }

    async def _reset(self) -> None:
        self.query_one("#repo_path", Input).value = str(self._repo_path_default)
        self.query_one("#temperature", Input).value = "0.6"
        self.query_one("#max_tokens", Input).value = "8192"
        self.query_one("#seed", Input).value = "42"
        self.query_one("#include_tests", Switch).value = False
        self.query_one("#save_traces", Switch).value = True
        self._set_error("")


__all__ = ["LauncherScreen"]
