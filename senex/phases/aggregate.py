"""senex.phases.aggregate — final aggregation + finalization (spec §4 phase 5).

Calls ``Aggregator.run()`` -> ``write_handoff()`` -> ``Renderer.render_combined()``
and writes ``combined.md`` atomically. Each sub-step is wrapped so that any
failure raises ``AggregateFailed(exit_code=1)`` — the per-file work has
already survived; the user can recover via ``senex aggregate <audit-dir>``
(M10 CLI).

Per spec §8.3 row "Aggregation failure": exit_code=1 (partial success).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from senex.atomic_io import write_text_atomic
from senex.events import AggregateComplete, AggregateStart, EventBus
from senex.findings_aggregator import Aggregator
from senex.handoff import write_handoff
from senex.render_models import (
    ErroredRecord,
    FindingRecord,
    LocationRecord,
    RunMetadata,
    SkippedRecord,
    Theme,
)
from senex.renderer import Renderer
from senex.secret_redactor import SecretRedactor

from .base import AggregateFailed

if TYPE_CHECKING:  # pragma: no cover
    from senex.config import SenexConfig
    from senex.events import CommandBus
    from senex.lens import Lens

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class AggregatePhase:
    """Phase 5: dedupe findings, write handoff, render combined.md."""

    name = "aggregate"

    def __init__(
        self,
        audit_dir: Path,
        run_metadata: RunMetadata,
        run_id: str,
    ) -> None:
        self._audit_dir = audit_dir
        self._run_metadata = run_metadata
        self._run_id = run_id

    async def read_state(self, audit_dir: Path) -> Any:
        return None

    async def do_work(
        self,
        state: Any,
        lens: "Lens",
        config: "SenexConfig",
        bus: EventBus,
        command_bus: "CommandBus",
    ) -> dict[str, Any]:
        del lens, config, command_bus  # aggregator works on persisted artifacts

        themes_in: list[Theme] | None = None
        if isinstance(state, dict) and state.get("themes") is not None:
            themes_in = list(state["themes"])

        await bus.publish(
            AggregateStart(ts=_now(), run_id=self._run_id)
        )

        try:
            aggregator = Aggregator()
            await aggregator.run(
                self._audit_dir,
                themes=themes_in,
                run_metadata=self._run_metadata,
            )

            findings = self._load_findings_json()
            theme_objs = themes_in or []

            write_handoff(
                self._audit_dir,
                self._run_metadata,
                findings,
            )

            renderer = Renderer(self._audit_dir, SecretRedactor())
            combined = renderer.render_combined(
                run_metadata=self._run_metadata,
                findings=findings,
                themes=theme_objs if themes_in is not None else None,
                skipped=self._collect_skipped(),
                errored=self._collect_errored(),
            )
            target = self._audit_dir / "combined.md"
            write_text_atomic(target, combined)

        except AggregateFailed:
            raise
        except Exception as exc:  # noqa: BLE001 — translate to AggregateFailed
            raise AggregateFailed(
                f"aggregate phase failed: {exc}", exit_code=1
            ) from exc

        await bus.publish(
            AggregateComplete(
                ts=_now(),
                run_id=self._run_id,
                finding_count=len(findings),
                theme_count=len(theme_objs),
            )
        )

        return {
            "finding_count": len(findings),
            "theme_count": len(theme_objs),
        }

    async def write_state(self, audit_dir: Path, state: Any) -> None:
        # All artifacts are written inside do_work; nothing more here.
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_findings_json(self) -> list[FindingRecord]:
        path = self._audit_dir / "findings.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        out: list[FindingRecord] = []
        for raw in data.get("findings", []):
            if not isinstance(raw, dict):
                continue
            location_raw = raw.get("location") or {}
            try:
                rec = FindingRecord(
                    **{
                        **raw,
                        "location": LocationRecord(**location_raw),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("aggregate: dropping malformed finding: %s", exc)
                continue
            out.append(rec)
        return out

    def _collect_skipped(self) -> list[SkippedRecord]:
        # Walk the audit dir for *.SKIPPED.md sidecars.
        out: list[SkippedRecord] = []
        for path in self._audit_dir.rglob("*.SKIPPED.md"):
            relpath = path.relative_to(self._audit_dir).as_posix().removesuffix(".SKIPPED.md")
            try:
                body = path.read_text(encoding="utf-8")
                reason = ""
                for line in body.splitlines():
                    if line.startswith("**Reason:**"):
                        reason = line.removeprefix("**Reason:**").strip()
                        break
            except OSError:
                reason = ""
            out.append(SkippedRecord(relpath=relpath, reason=reason))
        return out

    def _collect_errored(self) -> list[ErroredRecord]:
        out: list[ErroredRecord] = []
        for path in self._audit_dir.rglob("*.ERROR.md"):
            relpath = path.relative_to(self._audit_dir).as_posix().removesuffix(".ERROR.md")
            kind = ""
            try:
                body = path.read_text(encoding="utf-8")
                for line in body.splitlines():
                    if line.startswith("**Kind:**"):
                        kind = line.removeprefix("**Kind:**").strip()
                        break
            except OSError:
                kind = ""
            out.append(ErroredRecord(relpath=relpath, kind=kind, error_message=""))
        return out


__all__ = ["AggregatePhase"]
