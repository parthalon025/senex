"""senex.phases.discovery — DiscoveryPhase wrapping ``Walker.discover()``.

Emits exactly one ``DiscoveryStart`` and one ``DiscoveryComplete``; persists
the file list to ``<audit_dir>/discovery.json`` so a resumed run sees the
exact same set of files (no re-walking surprise files added between runs).

Per spec §ARCH-3: phases are stateless; state flows through arguments. The
walker is intentionally synchronous — no I/O cost worth offloading to a
worker thread for typical repo sizes (<= 50_000 files).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from senex.events import DiscoveryComplete, DiscoveryStart, EventBus
from senex.walker import Walker, WalkerLimitExceeded

if TYPE_CHECKING:  # pragma: no cover
    from senex.config import SenexConfig
    from senex.events import CommandBus
    from senex.lens import Lens


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class DiscoveryPhase:
    """Repo enumeration phase (spec §4 phase 1).

    State out:
        ``{"files": list[Path], "skipped": list[tuple[Path, str]]}``
    """

    name = "discovery"

    def __init__(self, repo: Path, run_id: str) -> None:
        self._repo = repo
        self._run_id = run_id

    async def read_state(self, audit_dir: Path) -> dict[str, Any] | None:
        # On resume, return the persisted file list; otherwise None.
        persisted = audit_dir / "discovery.json"
        if not persisted.exists():
            return None
        loaded: Any = json.loads(persisted.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
        return None

    async def do_work(
        self,
        state: Any,
        lens: "Lens",
        config: "SenexConfig",
        bus: EventBus,
        command_bus: "CommandBus",
    ) -> dict[str, Any]:
        del state, lens, command_bus  # discovery is stateless / lens-agnostic

        await bus.publish(
            DiscoveryStart(ts=_now(), run_id=self._run_id, repo=str(self._repo))
        )

        walker = Walker(bus)
        try:
            result = walker.discover(self._repo, config.walker)
        except WalkerLimitExceeded:
            src_path = self._repo / "src"
            if not config.walker.scan_subdir and src_path.is_dir():
                # Repo exceeded the file limit with no subdir restriction set.
                # Auto-retry scoped to src/ and surface the implicit narrowing
                # via the skipped list so auditors can see it happened.
                logging.getLogger(__name__).warning(
                    "walker limit exceeded; retrying with scan_subdir='src' "
                    "(%s)",
                    self._repo,
                )
                fallback_cfg = config.walker.model_copy(update={"scan_subdir": "src"})
                result = walker.discover(self._repo, fallback_cfg)
            else:
                raise

        files = list(result.kept)
        skipped = [(str(p), reason) for p, reason in result.skipped]

        await bus.publish(
            DiscoveryComplete(
                ts=_now(),
                run_id=self._run_id,
                file_count=len(files),
                skipped=skipped,
            )
        )
        return {
            "files": files,
            "skipped": skipped,
            "relpath_to_report_path": dict(result.relpath_to_report_path),
        }

    async def write_state(self, audit_dir: Path, state: dict[str, Any]) -> None:
        from senex.atomic_io import write_text_atomic

        target = audit_dir / "discovery.json"
        # Serialize Paths as strings; tuples as lists (JSON has no tuple).
        payload = {
            "files": [str(p) for p in state.get("files", [])],
            "skipped": [list(pair) for pair in state.get("skipped", [])],
            "relpath_to_report_path": state.get("relpath_to_report_path", {}),
        }
        body = json.dumps(payload, indent=2) + "\n"
        write_text_atomic(target, body)


__all__ = ["DiscoveryPhase"]
