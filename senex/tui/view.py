"""senex.tui.view — ``senex view <audit-dir>`` replay handler (M9 Task 9.9).

Implements spec §5.6 (event-line schema) + §5.7 (Monitor reuse). The
replay loop:

  1. Parses ``audit_dir/events.jsonl`` line-by-line, validating each
     line against the discriminated ``BaseEvent`` union.
  2. Pushes a ``MonitorScreen`` in *replay mode* (Skip/Rerun muted).
  3. Iterates events; awaits ``(ts_n+1 - ts_n) / speed`` between each
     dispatch (capped at 1s for sanity).

Offline: replay does NOT call LM Studio, does NOT need a model loaded.
Truncated runs (no ``RunComplete``) surface a warning banner.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from pydantic import ValidationError

from senex.events import ALL_EVENT_TYPES, BaseEvent, RunComplete
from senex.tui.app import SenexApp
from senex.tui.exceptions import ReplayError
from senex.tui.monitor import MonitorScreen
from senex.tui.widgets.error_banner import ErrorBannerWidget

log = logging.getLogger(__name__)

_TYPE_INDEX: dict[str, type[BaseEvent]] = {cls.__name__: cls for cls in ALL_EVENT_TYPES}

# Sleep-between-events safety cap: even at 1× wall-clock speed, never
# block the replay for more than this. Keeps tests fast and prevents
# pathological gaps from stalling the UI.
_MAX_SLEEP_S = 1.0


def parse_events_jsonl(path: Path) -> Iterator[BaseEvent]:
    """Yield ``BaseEvent`` instances from an ``events.jsonl`` file.

    Raises:
        ReplayError: file missing OR a line fails JSON parsing OR the
            ``type`` discriminator is not a known event class OR the
            payload fails pydantic validation.
    """
    if not path.exists():
        raise ReplayError(f"events.jsonl not found at {path}")
    text = path.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReplayError(
                f"line {lineno}: invalid JSON ({exc.msg})"
            ) from exc
        type_name = payload.get("type")
        if not isinstance(type_name, str) or type_name not in _TYPE_INDEX:
            raise ReplayError(
                f"line {lineno}: unknown event type {type_name!r}"
            )
        cls = _TYPE_INDEX[type_name]
        try:
            yield cls.model_validate(payload)
        except ValidationError as exc:
            raise ReplayError(
                f"line {lineno}: validation failed for {type_name}: {exc}"
            ) from exc


async def _replay(
    events: list[BaseEvent], app: SenexApp, speed: float
) -> None:
    if speed <= 0:
        raise ReplayError(f"speed must be > 0; got {speed!r}")
    bus = app.bus
    prev_ts: Any = None
    for event in events:
        if prev_ts is not None:
            delta = (event.ts - prev_ts).total_seconds()
            sleep_s = max(0.0, min(delta / speed, _MAX_SLEEP_S))
            await asyncio.sleep(sleep_s)
        prev_ts = event.ts
        await bus.publish(event)


async def run_view(
    audit_dir: Path,
    speed: float = 1.0,
    *,
    headless: bool = False,
) -> None:
    """Replay an audit's ``events.jsonl`` through a Monitor screen.

    Args:
        audit_dir: root containing ``events.jsonl``.
        speed: wall-clock multiplier (1.0 = original; 10.0 = 10× faster).
        headless: when True, replays without a Textual UI (used by
            tests that just want to verify event dispatch without Pilot
            startup).

    Raises:
        ReplayError: events file missing or unparseable.
    """
    events_path = audit_dir / "events.jsonl"
    events = list(parse_events_jsonl(events_path))
    has_complete = any(isinstance(e, RunComplete) for e in events)

    if headless:
        return None

    app = SenexApp.in_replay_mode(audit_dir=audit_dir, truncated=not has_complete)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Push Monitor in replay mode.
        screen = MonitorScreen(app.bus, app.command_bus)
        screen.set_replay_mode(True)
        await app.push_screen(screen)
        await pilot.pause()

        if not has_complete:
            try:
                banner = screen.query_one("#error_banner", ErrorBannerWidget)
                banner.show_external_error("audit was interrupted (no RunComplete)")
            except Exception as exc:  # noqa: BLE001 — UI-degrade only.
                log.warning("could not surface truncation banner: %s", exc)

        await _replay(events, app, speed)
        # One final pause for widget paints.
        await pilot.pause()
    return None


__all__ = ["parse_events_jsonl", "run_view"]
