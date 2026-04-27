"""senex.subscribers.metrics — running totals (M9 Task 9.3).

Pure aggregator: NO I/O. Implements spec §5.6 (event taxonomy) +
§5.6.1 (drop-oldest tick policy). The status-strip widget reads the
latest snapshot via the read-only ``metrics`` property.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from senex.events import (
    BaseEvent,
    CompactionComplete,
    FileComplete,
    OutputComplete,
    ThinkingComplete,
    ToolCall,
)


class Metrics(BaseModel):
    """Read-only snapshot of running totals."""

    model_config = ConfigDict(extra="forbid")

    files_done: int = 0
    findings_by_priority: dict[str, int] = Field(
        default_factory=lambda: {"high": 0, "medium": 0, "low": 0, "healthy": 0}
    )
    tool_calls: int = 0
    compactions: int = 0
    total_thinking_seconds: float = 0.0
    total_output_seconds: float = 0.0


class MetricsCollectorSubscriber:
    """Pure-aggregator subscriber (spec §5.6.1).

    Counters update on phase events only (NEVER on ``*Tick`` — see §5.7
    "no per-token render"). Drop policy is ``"drop_oldest"``.
    """

    name: str = "metrics"
    queue_capacity: int = 1024
    drop_policy: Literal["block", "drop_oldest", "drop_token_only"] = "drop_oldest"

    def __init__(self) -> None:
        self._metrics = Metrics()

    @property
    def metrics(self) -> Metrics:
        """Frozen copy of the current running totals."""
        return Metrics.model_validate(self._metrics.model_dump())

    async def consume(self, event: BaseEvent) -> None:
        match event:
            case FileComplete():
                self._on_file_complete(event)
            case ToolCall():
                self._metrics.tool_calls += 1
            case CompactionComplete():
                self._metrics.compactions += 1
            case ThinkingComplete():
                self._metrics.total_thinking_seconds += event.latency_ms / 1000.0
            case OutputComplete():
                self._metrics.total_output_seconds += event.latency_ms / 1000.0
            case _:
                return

    def _on_file_complete(self, event: FileComplete) -> None:
        self._metrics.files_done += 1
        for priority in ("high", "medium", "low", "healthy"):
            count = int(event.finding_counts.get(priority, 0))
            self._metrics.findings_by_priority[priority] = (
                self._metrics.findings_by_priority.get(priority, 0) + count
            )

    async def shutdown(self) -> None:
        return None


__all__ = ["Metrics", "MetricsCollectorSubscriber"]
