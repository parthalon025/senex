"""senex.events — Event taxonomy + bounded async EventBus + CommandBus.

Implements spec §5.6 (event types), §5.6.1 (bus semantics), §5.6.2 (command bus).
Conventions §3 (bounded queues), §10 (pydantic v2 with extra=forbid).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_CAPACITY = 1024
COALESCE_SAFE_TYPES: frozenset[str] = frozenset({"ThinkingTick", "OutputTick"})


class UnknownEventType(ValueError):
    """Raised when a serialized event has an unknown ``type`` discriminator."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BaseEvent(_StrictModel):
    v: int = 1
    type: str
    ts: datetime
    seq: int = 0
    run_id: str

    @model_validator(mode="after")
    def _validate_type_discriminator(self) -> BaseEvent:
        # When the model class is BaseEvent itself (not a subclass with a Literal
        # discriminator), enforce that ``type`` corresponds to a known event class.
        # Subclasses with ``Literal["..."]`` discriminators rely on pydantic to
        # narrow ``type`` at parse time, so this branch is a no-op for them.
        if type(self) is BaseEvent:
            known = {cls.__name__ for cls in ALL_EVENT_TYPES}
            if self.type not in known:
                raise UnknownEventType(f"unknown event type: {self.type!r}")
        return self


class RunStart(BaseEvent):
    type: Literal["RunStart"] = Field(default="RunStart")
    repo: str
    audit_dir: str
    model: str
    lens: str
    lens_version: str
    config_hash: str
    prompt_hash: str
    model_fingerprint: str
    # M5 Task 5.10: tool_pack_hash is part of the resume hash discipline; it
    # appears here so RunStart fully advertises the run's reproducibility
    # bucket. See senex.tools.pack_hash.compute_tool_pack_hash. Default empty
    # string for backward compatibility with pre-M5 RunStart writers; M8
    # auditor populates it from compute_tool_pack_hash() at run start.
    tool_pack_hash: str = ""
    started_at: datetime


class PreflightWarning(BaseEvent):
    """Non-fatal preflight check produced a WARN result (spec §8.1)."""
    type: Literal["PreflightWarning"] = Field(default="PreflightWarning")
    check: str
    message: str


class DiscoveryStart(BaseEvent):
    """Walker phase begin marker (spec §8.4)."""
    type: Literal["DiscoveryStart"] = Field(default="DiscoveryStart")
    repo: str


class DiscoveryComplete(BaseEvent):
    type: Literal["DiscoveryComplete"] = Field(default="DiscoveryComplete")
    file_count: int
    skipped: list[tuple[str, str]] = Field(default_factory=list)


class SymlinkSkipped(BaseEvent):
    type: Literal["SymlinkSkipped"] = Field(default="SymlinkSkipped")
    path: str
    target: str
    reason: str


class SuspiciousEmptyFinding(BaseEvent):
    type: Literal["SuspiciousEmptyFinding"] = Field(default="SuspiciousEmptyFinding")
    path: str
    loc: str
    reason: str


class FileStart(BaseEvent):
    type: Literal["FileStart"] = Field(default="FileStart")
    path: str
    idx: int
    total: int


class FileContextBuilt(BaseEvent):
    type: Literal["FileContextBuilt"] = Field(default="FileContextBuilt")
    path: str
    graph_context_tokens: int


class GraphContextUnavailable(BaseEvent):
    # Emitted by GitNexusCLIProvider (M2) when the gitnexus CLI fails or
    # returns no data; the auditor proceeds with the sentinel awareness
    # block per §5.8. Non-coalesce-safe (per §5.6.1).
    type: Literal["GraphContextUnavailable"] = Field(default="GraphContextUnavailable")
    path: str
    reason: str


class FileLLMCall(BaseEvent):
    type: Literal["FileLLMCall"] = Field(default="FileLLMCall")
    path: str
    prompt_tokens: int


class ThinkingStarted(BaseEvent):
    type: Literal["ThinkingStarted"] = Field(default="ThinkingStarted")
    path: str


class ThinkingTick(BaseEvent):
    type: Literal["ThinkingTick"] = Field(default="ThinkingTick")
    path: str
    tokens_so_far: int
    delta_since_last_tick: int


class ThinkingComplete(BaseEvent):
    type: Literal["ThinkingComplete"] = Field(default="ThinkingComplete")
    path: str
    total_thinking_tokens: int
    latency_ms: int


class OutputStarted(BaseEvent):
    type: Literal["OutputStarted"] = Field(default="OutputStarted")
    path: str


class OutputTick(BaseEvent):
    type: Literal["OutputTick"] = Field(default="OutputTick")
    path: str
    tokens_so_far: int
    delta_since_last_tick: int


class OutputComplete(BaseEvent):
    type: Literal["OutputComplete"] = Field(default="OutputComplete")
    path: str
    total_output_tokens: int
    latency_ms: int


class FindingSummary(_StrictModel):
    """Minimal subset of a finding carried on FileComplete for the TUI's
    findings panel (M9 Task 9.6c) — see R12."""
    priority: Literal["high", "medium", "low", "healthy"]
    title: str
    location: str | None = None


class FileComplete(BaseEvent):
    type: Literal["FileComplete"] = Field(default="FileComplete")
    path: str
    finding_counts: dict[str, int]
    # Per R12: most-recent finding summary for the file (TUI consumes via M9 Task 9.6c).
    last_finding_summary: FindingSummary | None = None


class FileError(BaseEvent):
    type: Literal["FileError"] = Field(default="FileError")
    path: str
    phase: str
    error_kind: str
    error_message: str


class ToolCall(BaseEvent):
    type: Literal["ToolCall"] = Field(default="ToolCall")
    path: str
    tool_name: str
    tool_input: str  # truncated to 512 chars at emission per §5.6.
    call_id: str


class ToolResult(BaseEvent):
    type: Literal["ToolResult"] = Field(default="ToolResult")
    path: str
    tool_name: str
    call_id: str
    result_tokens: int
    latency_ms: int
    truncated: bool


class ToolError(BaseEvent):
    type: Literal["ToolError"] = Field(default="ToolError")
    path: str
    tool_name: str
    call_id: str
    kind: Literal["timeout", "path_rejected", "schema_invalid", "subprocess",
                  "unavailable", "internal"]
    error_message: str


class ToolBudgetExhausted(BaseEvent):
    type: Literal["ToolBudgetExhausted"] = Field(default="ToolBudgetExhausted")
    path: str
    calls_made: int


class CompactionTriggered(BaseEvent):
    type: Literal["CompactionTriggered"] = Field(default="CompactionTriggered")
    path: str
    message_tokens_before: int
    threshold: int


class CompactionComplete(BaseEvent):
    type: Literal["CompactionComplete"] = Field(default="CompactionComplete")
    path: str
    message_tokens_after: int
    kept_findings: int


class CompactionError(BaseEvent):
    type: Literal["CompactionError"] = Field(default="CompactionError")
    path: str
    error_kind: str


class CrosscutStart(BaseEvent):
    type: Literal["CrosscutStart"] = Field(default="CrosscutStart")


class CrosscutComplete(BaseEvent):
    type: Literal["CrosscutComplete"] = Field(default="CrosscutComplete")
    theme_count: int


class AggregateStart(BaseEvent):
    """Aggregate phase begin marker (spec §8.4)."""
    type: Literal["AggregateStart"] = Field(default="AggregateStart")


class AggregateComplete(BaseEvent):
    """Aggregate phase end marker (spec §8.4)."""
    type: Literal["AggregateComplete"] = Field(default="AggregateComplete")
    finding_count: int
    theme_count: int


class RunComplete(BaseEvent):
    type: Literal["RunComplete"] = Field(default="RunComplete")
    duration_seconds: float
    totals: dict[str, int]
    exit_status: int


class ModelLoadRequested(BaseEvent):
    type: Literal["ModelLoadRequested"] = Field(default="ModelLoadRequested")
    model_id: str
    target_fingerprint: str


class ModelLoadStarted(BaseEvent):
    type: Literal["ModelLoadStarted"] = Field(default="ModelLoadStarted")
    model_id: str


class ModelLoadComplete(BaseEvent):
    type: Literal["ModelLoadComplete"] = Field(default="ModelLoadComplete")
    model_id: str
    duration_seconds: float
    fingerprint: str


class ModelLoadFailed(BaseEvent):
    type: Literal["ModelLoadFailed"] = Field(default="ModelLoadFailed")
    model_id: str
    error_kind: str
    error_message: str


class ModelUnloadStarted(BaseEvent):
    type: Literal["ModelUnloadStarted"] = Field(default="ModelUnloadStarted")
    model_id: str


class ModelUnloadComplete(BaseEvent):
    type: Literal["ModelUnloadComplete"] = Field(default="ModelUnloadComplete")
    model_id: str
    duration_seconds: float


class ModelUnloadSkipped(BaseEvent):
    type: Literal["ModelUnloadSkipped"] = Field(default="ModelUnloadSkipped")
    model_id: str
    reason: Literal["concurrent_holders", "not_loaded_by_us",
                    "auto_unload_disabled", "resumed_run_does_not_own_load"]


class ModelUnloadFailed(BaseEvent):
    type: Literal["ModelUnloadFailed"] = Field(default="ModelUnloadFailed")
    model_id: str
    error_kind: str
    error_message: str


class ModelFingerprintChanged(BaseEvent):
    type: Literal["ModelFingerprintChanged"] = Field(default="ModelFingerprintChanged")
    path: str
    expected_fingerprint: str
    observed_fingerprint: str


class RunLockAcquired(BaseEvent):
    type: Literal["RunLockAcquired"] = Field(default="RunLockAcquired")
    model_fingerprint: str
    holder_count: int


class RunLockReleased(BaseEvent):
    type: Literal["RunLockReleased"] = Field(default="RunLockReleased")
    model_fingerprint: str
    remaining_holders: int


ALL_EVENT_TYPES: tuple[type[BaseEvent], ...] = (
    RunStart, PreflightWarning, DiscoveryStart, DiscoveryComplete,
    SymlinkSkipped, SuspiciousEmptyFinding,
    FileStart, FileContextBuilt, GraphContextUnavailable, FileLLMCall,
    ThinkingStarted, ThinkingTick, ThinkingComplete,
    OutputStarted, OutputTick, OutputComplete,
    FileComplete, FileError,
    ToolCall, ToolResult, ToolError, ToolBudgetExhausted,
    CompactionTriggered, CompactionComplete, CompactionError,
    CrosscutStart, CrosscutComplete,
    AggregateStart, AggregateComplete,
    RunComplete,
    ModelLoadRequested, ModelLoadStarted, ModelLoadComplete, ModelLoadFailed,
    ModelUnloadStarted, ModelUnloadComplete, ModelUnloadSkipped, ModelUnloadFailed,
    ModelFingerprintChanged,
    RunLockAcquired, RunLockReleased,
)


class Command(_StrictModel):
    type: Literal["Pause", "Resume", "Skip", "Rerun", "Quit"]
    target: str | None = None
    ts: datetime


class SubscriptionHandle:
    """Returned by ``EventBus.subscribe_local``; opaque cancel token (R9)."""

    def __init__(self, bus: EventBus, token: int) -> None:
        self._bus = bus
        self._token = token

    def unsubscribe(self) -> None:
        self._bus._cancel_local(self._token)


class EventBus:
    """Per-subscriber bounded asyncio queue fan-out (spec §5.6.1).

    Conventions §3: every queue declares an explicit ``maxsize``.
    """

    def __init__(self, default_capacity: int = DEFAULT_CAPACITY) -> None:
        self._default_capacity = default_capacity
        self._subs: dict[str, tuple[asyncio.Queue[BaseEvent], int]] = {}
        self._local_subs: dict[
            int, tuple[tuple[type[BaseEvent], ...], Callable[[BaseEvent], Awaitable[None]]]
        ] = {}
        self._next_local_token = 0
        self._seq_counter = 0

    def subscribe(self, name: str, capacity: int | None = None) -> asyncio.Queue[BaseEvent]:
        cap = capacity if capacity is not None else self._default_capacity
        q: asyncio.Queue[BaseEvent] = asyncio.Queue(maxsize=cap)
        self._subs[name] = (q, cap)
        return q

    def subscribe_local(
        self,
        name: str,
        event_types: type[BaseEvent] | tuple[type[BaseEvent], ...],
        callback: Callable[[BaseEvent], Awaitable[None]],
        capacity: int = 1024,
    ) -> SubscriptionHandle:
        """Subscribe a callback to specific event types.

        Internal helper for tests and TUI widgets that don't need a Queue
        (R9). Only fires for events whose ``type(event)`` matches one of
        the provided ``event_types``. Returns a handle for unsubscription.
        ``capacity`` is reserved for future buffering — v1 invokes the
        callback synchronously during ``publish``.
        """
        types_tuple = (
            (event_types,) if isinstance(event_types, type) else tuple(event_types)
        )
        token = self._next_local_token
        self._next_local_token += 1
        self._local_subs[token] = (types_tuple, callback)
        return SubscriptionHandle(self, token)

    def _cancel_local(self, token: int) -> None:
        self._local_subs.pop(token, None)

    async def publish(self, event: BaseEvent) -> None:
        # Stamp seq monotonically. Subclasses MUST NOT generate their own.
        event.seq = self._seq_counter
        self._seq_counter += 1
        for name, (q, _cap) in self._subs.items():
            await self._dispatch(name, q, event)
        # R9: fire any locally-registered callbacks for matching event types.
        for types_tuple, cb in list(self._local_subs.values()):
            if isinstance(event, types_tuple):
                await cb(event)

    async def _dispatch(
        self, name: str, q: asyncio.Queue[BaseEvent], event: BaseEvent
    ) -> None:
        # Slow-subscriber policy per spec §5.6.1.
        if name == "DiskWriter":
            await q.put(event)  # blocks the publisher.
            return
        if name == "Tui":
            if event.type in COALESCE_SAFE_TYPES:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    return  # drop this tick.
            else:
                await q.put(event)  # block on non-coalesce-safe.
            return
        if name == "Metrics":
            if event.type in COALESCE_SAFE_TYPES:
                if q.full():
                    try:
                        _ = q.get_nowait()  # drop oldest tick.
                    except asyncio.QueueEmpty:
                        pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    return
            else:
                await q.put(event)
            return
        # Unknown subscriber: default to block (data integrity > liveness).
        await q.put(event)


class CommandBus:
    """TUI -> auditor command channel (spec §5.6.2)."""

    def __init__(self, capacity: int = 64) -> None:
        self._capacity = capacity
        self._queues: list[asyncio.Queue[Command]] = []

    def subscribe(self) -> asyncio.Queue[Command]:
        q: asyncio.Queue[Command] = asyncio.Queue(maxsize=self._capacity)
        self._queues.append(q)
        return q

    async def publish(self, command: Command) -> None:
        for q in self._queues:
            await q.put(command)
