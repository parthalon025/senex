"""senex.phases.crosscut — wraps ``CrossCutter.run()`` (spec §4 phase 4).

Reads ``findings.partial.jsonl``, calls ``CrossCutter.run()``, writes
``themes.json``. Per spec §8.3: cross-cutting failure does NOT fail the run
— on any failure the phase returns ``state["themes"] = None`` and the
combined report renders a "[cross-cutting themes unavailable]" gap.

The phase emits ``CrosscutStart`` / ``CrosscutComplete``; the latter
``theme_count`` is 0 when the call failed (themes is None).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from senex.atomic_io import write_text_atomic
from senex.cross_cutting import CrossCutter
from senex.events import EventBus
from senex.render_models import FindingRecord, LocationRecord, Theme

if TYPE_CHECKING:  # pragma: no cover
    from senex.config import SenexConfig
    from senex.events import CommandBus
    from senex.lens import Lens
    from senex.llm_client import LLMClient

log = logging.getLogger(__name__)


class CrosscutPhase:
    """Cross-cutting themes pass over aggregated findings."""

    name = "crosscut"

    def __init__(
        self,
        audit_dir: Path,
        client: LLMClient,
        run_id: str,
    ) -> None:
        self._audit_dir = audit_dir
        self._client = client
        self._run_id = run_id

    async def read_state(self, audit_dir: Path) -> Any:
        return None

    async def do_work(
        self,
        state: Any,
        lens: Lens,
        config: SenexConfig,
        bus: EventBus,
        command_bus: CommandBus,
    ) -> dict[str, Any]:
        del state, lens, command_bus
        findings = self._load_findings()
        cutter = CrossCutter(bus=bus, run_id=self._run_id)
        themes = await cutter.run(
            client=self._client,
            findings=findings,
            cfg=config.crosscut,
        )
        # Persist themes (or None marker) for AggregatePhase.
        if themes is not None:
            self._write_themes(themes)
        return {"themes": themes}

    async def write_state(self, audit_dir: Path, state: Any) -> None:
        # themes.json is written inside do_work for atomicity with aggregator
        # consumption; nothing more to flush here.
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_findings(self) -> list[FindingRecord]:
        partial = self._audit_dir / "findings.partial.jsonl"
        if not partial.exists():
            return []
        out: list[FindingRecord] = []
        for line in partial.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                # Per SCHEMA-4: tolerate corrupt last line silently.
                continue
            location_raw = raw.get("location", {}) or {}
            try:
                rec = FindingRecord(
                    **{
                        **raw,
                        "location": LocationRecord(**location_raw),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("crosscut: dropping malformed finding: %s", exc)
                continue
            out.append(rec)
        return out

    def _write_themes(self, themes: list[Theme]) -> None:
        target = self._audit_dir / "themes.json"
        body = json.dumps([t.model_dump() for t in themes], indent=2) + "\n"
        write_text_atomic(target, body)


__all__ = ["CrosscutPhase"]
