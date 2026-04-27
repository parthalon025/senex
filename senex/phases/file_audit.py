"""senex.phases.file_audit — per-file audit loop with §8.2 recovery matrix.

This is the big one. Every spec §8.2 row maps to a try/except branch in
``do_work``:

    | Row                              | Action                                           |
    |---------------------------------|--------------------------------------------------|
    | File read error                  | <file>.SKIPPED.md, FileError, continue.         |
    | Token budget exceeded            | <file>.SKIPPED.md, FileError, continue.         |
    | Graph context fetch fails        | sentinel block, awareness.available=False.     |
    | LMS HTTP 5xx / timeout           | client retries; on final fail -> ERROR + continue |
    | LMS HTTP 4xx                     | <file>.ERROR.md, FileError, continue.           |
    | Connection lost                  | propagate (run aborts).                         |
    | Schema mismatch                  | retry once with stricter prompt; on second     |
    |                                  | failure save RAW + ERROR + continue.            |
    | Renderer crash                   | <file>.RENDER_ERROR.md, FileError, continue.    |
    | Disk full                        | propagate (run aborts).                         |
    | CompactionLoopExceeded           | <file>.ERROR.md, FileError, continue.           |
    | FingerprintChanged               | propagate (run aborts).                         |

Per-file iteration boundary is the canonical command-bus poll point (#1):
Pause / Resume / Skip / Quit are honored only between files. No mid-LLM
interrupts (would break in-flight HTTP).

Atomic per-file write order (§ARCH-11):
    <file>.md.tmp -> findings.partial.jsonl append -> checkpoint.json update
    -> rename <file>.md.tmp -> <file>.md -> events.jsonl append -> FileComplete.

Crash mid-sequence: next resume re-audits this file (idempotent).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback as _tb
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from senex.atomic_io import DiskFatalError
from senex.checkpoint import Checkpoint
from senex.compaction import (
    CompactionFailed,
    CompactionLoopExceeded,
    ContextOverflow,
)
from senex.error_artifacts import (
    write_error_artifact,
    write_raw_response,
    write_render_error_artifact,
    write_skipped_artifact,
)
from senex.events import (
    Command,
    EventBus,
    FileComplete,
    FileContextBuilt,
    FileError,
    FileStart,
)
from senex.findings_partial import FindingsPartialWriter, compute_finding_id
from senex.lmstudio_client import ChatMessage
from senex.lmstudio_errors import (
    FingerprintChanged,
    LMSConnectionLost,
    LMSResponseInvalidJSON,
    LMSResponseSchemaInvalid,
    LMStudioError,
    TokenBudgetExceeded,
)
from senex.prompts._anchor_loader import build_user_prompt, select_anchor
from senex.render_models import (
    FileMetadata,
    FindingRecord,
    LocationRecord,
    ToolCallSummary,
)
from senex.renderer import RenderError, Renderer
from senex.tools import ToolDispatchFailed
from senex.tools.loop import ToolLoop

from .base import RenderFatal

if TYPE_CHECKING:  # pragma: no cover
    from senex.config import SenexConfig
    from senex.events import CommandBus
    from senex.graph_awareness import GraphContextProvider
    from senex.lens import Lens
    from senex.lmstudio_client import LMStudioClient

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _detect_language(path: Path) -> str:
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".rs": "rust",
        ".go": "go",
        ".cs": "csharp",
        ".java": "java",
    }.get(path.suffix.lower(), "text")


def _number_lines(source: str) -> str:
    lines = source.splitlines()
    width = len(str(max(len(lines), 1)))
    return "\n".join(f"{i:>{width}}: {line}" for i, line in enumerate(lines, 1))


def _load_audit_schema(lens: "Lens") -> dict[str, Any]:
    schema_path = lens.response_schema_path
    parsed: Any = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError(f"audit schema not a JSON object: {schema_path}")
    return parsed


async def _pause_until_resume(command_bus: "CommandBus", queue: asyncio.Queue[Command]) -> None:
    """Block on ``queue`` until a Resume command arrives. Re-pushes Quit/Skip."""
    while True:
        cmd = await queue.get()
        if cmd.type == "Resume":
            return
        if cmd.type == "Quit":
            # Re-publish so the outer loop sees it on the next poll.
            await command_bus.publish(cmd)
            return


class FileAuditPhase:
    """Per-file audit loop (spec §4 phase 2-3 + §8.2 recovery)."""

    name = "file_audit"

    def __init__(
        self,
        *,
        audit_dir: Path,
        repo_root: Path,
        repo_name: str,
        run_id: str,
        run_id_short: str,
        client: "LMStudioClient",
        compactor_factory: Any,
        graph_provider: "GraphContextProvider",
        renderer: Renderer,
        partial_writer: FindingsPartialWriter,
        tool_registry: Any,
        lens_tools_resolved: list[str],
        config_hash: str,
        prompt_hash: str,
    ) -> None:
        self._audit_dir = audit_dir
        self._repo_root = repo_root
        self._repo_name = repo_name
        self._run_id = run_id
        self._run_id_short = run_id_short
        self._client = client
        self._compactor_factory = compactor_factory
        self._graph_provider = graph_provider
        self._renderer = renderer
        self._partial_writer = partial_writer
        self._tool_registry = tool_registry
        self._lens_tools_resolved = lens_tools_resolved
        self._config_hash = config_hash
        self._prompt_hash = prompt_hash

    async def read_state(self, audit_dir: Path) -> Any:
        return None

    async def write_state(self, audit_dir: Path, state: Any) -> None:
        # Per-file artifacts are written inside the loop. Nothing to flush here
        # except the partial writer (which tests own the lifecycle of).
        return None

    async def do_work(
        self,
        state: Any,
        lens: "Lens",
        config: "SenexConfig",
        bus: EventBus,
        command_bus: "CommandBus",
    ) -> dict[str, Any]:
        files: list[Path] = list(state.get("files", []))
        relpath_map: dict[str, str] = state.get("relpath_to_report_path", {})
        if not files:
            return {"completed": [], "errored": [], "skipped": []}

        completed: list[str] = []
        errored: list[str] = []
        skipped: list[str] = []
        cmd_queue = command_bus.subscribe()

        schema = _load_audit_schema(lens)

        cp = Checkpoint.load(self._audit_dir)
        already_done = {entry["path"] for entry in cp.data.get("completed_files", [])}

        for idx, file in enumerate(files):
            relpath = file.relative_to(self._repo_root).as_posix()
            report_relpath = relpath_map.get(relpath, relpath)

            # ---- Resume: skip already-completed files (idempotent) ----
            if relpath in already_done:
                completed.append(relpath)
                continue

            # ---- Command bus poll point #1 (CANONICAL) ----
            cmd = self._poll_command(cmd_queue)
            if cmd is not None:
                if cmd.type == "Quit":
                    raise KeyboardInterrupt
                if cmd.type == "Pause":
                    await _pause_until_resume(command_bus, cmd_queue)
                if cmd.type == "Skip" and cmd.target == relpath:
                    write_skipped_artifact(
                        audit_dir=self._audit_dir,
                        relpath=report_relpath,
                        reason="user_skip",
                    )
                    Checkpoint.mark_done(self._audit_dir, relpath)
                    skipped.append(relpath)
                    continue

            await bus.publish(
                FileStart(
                    ts=_now(),
                    run_id=self._run_id,
                    path=relpath,
                    idx=idx,
                    total=len(files),
                )
            )

            try:
                result = await self._audit_one(
                    file=file,
                    relpath=relpath,
                    report_relpath=report_relpath,
                    lens=lens,
                    config=config,
                    schema=schema,
                    bus=bus,
                )
            except (LMSConnectionLost, FingerprintChanged):
                # Run-aborting failures (spec §8.3): propagate.
                raise
            except DiskFatalError as exc:
                raise RenderFatal(f"disk fatal during {relpath}: {exc}") from exc

            if result == "completed":
                completed.append(relpath)
            elif result == "errored":
                errored.append(relpath)
            else:
                skipped.append(relpath)

            Checkpoint.mark_done(self._audit_dir, relpath)

        return {
            "completed": completed,
            "errored": errored,
            "skipped": skipped,
        }

    @staticmethod
    def _poll_command(queue: asyncio.Queue[Command]) -> Command | None:
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def _audit_one(
        self,
        *,
        file: Path,
        relpath: str,
        report_relpath: str,
        lens: "Lens",
        config: "SenexConfig",
        schema: dict[str, Any],
        bus: EventBus,
    ) -> str:
        """Audit one file. Returns "completed" | "errored" | "skipped".

        Per §8.2 each branch writes the right artifact and returns; only
        run-aborting exceptions propagate up.
        """
        # ---- Read source ----
        try:
            source = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError) as exc:
            await bus.publish(
                FileError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=relpath,
                    phase="read",
                    error_kind="read_error",
                    error_message=str(exc),
                )
            )
            write_skipped_artifact(
                audit_dir=self._audit_dir,
                relpath=report_relpath,
                reason=f"read_error: {exc}",
            )
            return "skipped"

        # ---- Graph awareness (graceful fallback) ----
        try:
            awareness = await self._graph_provider.fetch(relpath)
        except Exception as exc:  # noqa: BLE001
            log.warning("graph awareness fetch failed: %s", exc)
            awareness = None

        graph_text = (
            awareness.raw_text_block if awareness is not None else "[graph context unavailable]"
        )
        await bus.publish(
            FileContextBuilt(
                ts=_now(),
                run_id=self._run_id,
                path=relpath,
                graph_context_tokens=len(graph_text) // 4,
            )
        )

        # ---- Build messages ----
        anchor = select_anchor(file) or ""
        system_prompt = lens.system_prompt_path.read_text(encoding="utf-8")
        if anchor:
            system_prompt = f"{system_prompt}\n\n---\n\n{anchor}"

        user_prompt = build_user_prompt(
            file_relpath=relpath,
            language=_detect_language(file),
            graph_context=graph_text,
            numbered_source=_number_lines(source),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # ---- Token budget gate (§8.2 row "Pre-LMS token count > 90%") ----
        chat_messages = [ChatMessage(**m) for m in messages]
        used = self._client.count_tokens(chat_messages, config.lmstudio.model)
        budget = int(
            config.lmstudio.token_budget_pct * config.lmstudio.context_window
        )
        if used > budget:
            await bus.publish(
                FileError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=relpath,
                    phase="prepare",
                    error_kind="token_budget",
                    error_message=(
                        f"messages={used} tokens exceeds {config.lmstudio.token_budget_pct} "
                        f"* {config.lmstudio.context_window} = {budget}"
                    ),
                )
            )
            write_skipped_artifact(
                audit_dir=self._audit_dir,
                relpath=report_relpath,
                reason=f"token_budget_exceeded: {used} / {budget}",
            )
            return "skipped"

        # ---- LMS call with full §8.2 recovery matrix ----
        compactor = self._compactor_factory(file)
        loop = ToolLoop(
            client=self._client,
            registry=self._tool_registry,
            repo_root=self._repo_root,
            repo_name=self._repo_name,
            secret_redactor=self._renderer._redactor,  # share singleton
            compactor=lambda msgs: compactor.maybe_compact(
                msgs, config.lmstudio.context_window
            ),
            max_calls=config.lmstudio.tools.max_calls_per_file,
            tool_timeout_seconds=float(config.lmstudio.tools.tool_timeout_seconds),
            max_result_tokens=config.lmstudio.tools.max_result_tokens,
            bus=bus,
        )
        try:
            response = await loop.run(
                messages,
                schema=schema,
                lens_tools=self._lens_tools_resolved,
                path=relpath,
                run_id=self._run_id,
            )
        except CompactionLoopExceeded as exc:
            write_error_artifact(
                audit_dir=self._audit_dir,
                relpath=report_relpath,
                kind="compaction_loop",
                error_message=str(exc),
                traceback=_tb.format_exc(),
            )
            await bus.publish(
                FileError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=relpath,
                    phase="llm",
                    error_kind="compaction_loop",
                    error_message=str(exc),
                )
            )
            return "errored"
        except (CompactionFailed, ContextOverflow) as exc:
            write_error_artifact(
                audit_dir=self._audit_dir,
                relpath=report_relpath,
                kind="compaction_failed",
                error_message=str(exc),
                traceback=_tb.format_exc(),
            )
            await bus.publish(
                FileError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=relpath,
                    phase="llm",
                    error_kind="compaction_failed",
                    error_message=str(exc),
                )
            )
            return "errored"
        except (LMSResponseSchemaInvalid, LMSResponseInvalidJSON) as exc:
            # Retry ONCE with stricter prompt.
            messages_strict = self._inject_strict_addendum(messages)
            try:
                response = await loop.run(
                    messages_strict,
                    schema=schema,
                    lens_tools=self._lens_tools_resolved,
                    path=relpath,
                    run_id=self._run_id,
                )
            except (LMSResponseSchemaInvalid, LMSResponseInvalidJSON) as exc2:
                # Save raw response when the model offered a body we can extract.
                raw = ""
                try:
                    raw = json.dumps({"error": str(exc2)})
                except Exception:  # noqa: BLE001
                    raw = str(exc2)
                write_raw_response(
                    audit_dir=self._audit_dir,
                    relpath=report_relpath,
                    raw_json=raw,
                )
                write_error_artifact(
                    audit_dir=self._audit_dir,
                    relpath=report_relpath,
                    kind="schema_mismatch",
                    error_message=str(exc2),
                    traceback=_tb.format_exc(),
                )
                await bus.publish(
                    FileError(
                        ts=_now(),
                        run_id=self._run_id,
                        path=relpath,
                        phase="llm",
                        error_kind="schema_mismatch",
                        error_message=str(exc2),
                    )
                )
                return "errored"
            del exc  # initial exc is recorded via traceback chain
        except LMSConnectionLost:
            raise  # run-aborting (§8.3)
        except FingerprintChanged:
            raise  # run-aborting (§8.3)
        except (LMStudioError, ToolDispatchFailed) as exc:
            write_error_artifact(
                audit_dir=self._audit_dir,
                relpath=report_relpath,
                kind="lms_error",
                error_message=str(exc),
                traceback=_tb.format_exc(),
            )
            await bus.publish(
                FileError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=relpath,
                    phase="llm",
                    error_kind="lms_error",
                    error_message=str(exc),
                )
            )
            return "errored"

        # Token-budget exceptions surfaced from inside the loop.
        if isinstance(response, TokenBudgetExceeded):  # pragma: no cover — defensive
            write_skipped_artifact(
                audit_dir=self._audit_dir,
                relpath=report_relpath,
                reason="token_budget_exceeded",
            )
            return "skipped"

        # ---- Render + persist (ARCH-13: render crash NOT run-killing) ----
        try:
            response_dict = response.content_dict or {}
            file_meta = self._build_file_metadata(
                file=file,
                relpath=report_relpath,
                response=response,
                lens=lens,
                config=config,
            )
            markdown = self._renderer.render_file(response_dict, file_meta)
            self._renderer.write_file_atomic(
                self._audit_dir, report_relpath, markdown
            )
            self._append_findings(
                response=response_dict,
                file_relpath=report_relpath,
            )
        except DiskFatalError:
            raise  # propagate; auditor maps to RenderFatal
        except RenderError as exc:
            write_render_error_artifact(
                audit_dir=self._audit_dir,
                relpath=report_relpath,
                traceback=_tb.format_exc(),
                response_dump=json.dumps(response.content_dict or {}, indent=2)
                if response.content_dict
                else str(exc),
            )
            await bus.publish(
                FileError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=relpath,
                    phase="render",
                    error_kind="render_error",
                    error_message=str(exc),
                )
            )
            return "errored"
        except Exception as exc:  # noqa: BLE001 — ARCH-13: render crash never kills run
            write_render_error_artifact(
                audit_dir=self._audit_dir,
                relpath=report_relpath,
                traceback=_tb.format_exc(),
                response_dump=json.dumps(response.content_dict or {}, indent=2)
                if response.content_dict
                else "",
            )
            await bus.publish(
                FileError(
                    ts=_now(),
                    run_id=self._run_id,
                    path=relpath,
                    phase="render",
                    error_kind="render_error",
                    error_message=str(exc),
                )
            )
            return "errored"

        # ---- Optional thinking trace ----
        if config.lmstudio.thinking.save_traces and response.reasoning_content:
            self._write_thinking_trace(
                report_relpath, response.reasoning_content
            )

        # ---- Mark complete ----
        finding_counts = self._summarize_findings(response.content_dict or {})
        await bus.publish(
            FileComplete(
                ts=_now(),
                run_id=self._run_id,
                path=relpath,
                finding_counts=finding_counts,
            )
        )
        return "completed"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_strict_addendum(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        from senex.lmstudio_client import STRICT_RETRY_PREAMBLE

        out = [dict(m) for m in messages]
        if out and out[0].get("role") == "system":
            existing = str(out[0].get("content") or "")
            out[0]["content"] = f"{STRICT_RETRY_PREAMBLE}\n\n{existing}"
        else:
            out.insert(
                0, {"role": "system", "content": STRICT_RETRY_PREAMBLE}
            )
        return out

    def _build_file_metadata(
        self,
        *,
        file: Path,
        relpath: str,
        response: Any,
        lens: "Lens",
        config: "SenexConfig",
    ) -> FileMetadata:
        return FileMetadata(
            relpath=relpath,
            language=_detect_language(file),
            model_id=config.lmstudio.model,
            lens_name=lens.name,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            thinking_seconds=0.0,
            output_seconds=0.0,
            tools_used=[],
            compactions_used=0,
            compactions_max=config.lmstudio.compaction.max_compactions_per_file,
            graph_context_summary="(see graph block in user prompt)",
            run_id_short=self._run_id_short,
            date=time.strftime("%Y-%m-%d"),
        )

    def _append_findings(
        self,
        *,
        response: dict[str, Any],
        file_relpath: str,
    ) -> None:
        findings_raw = response.get("findings") or []
        if not isinstance(findings_raw, list):
            return
        for f in findings_raw:
            if not isinstance(f, dict):
                continue
            location_raw = f.get("location") or {}
            location = LocationRecord(
                line_start=location_raw.get("line_start"),
                line_end=location_raw.get("line_end"),
                symbol=location_raw.get("symbol"),
            )
            fid = compute_finding_id(
                file=file_relpath,
                symbol=location.symbol,
                line_start=location.line_start,
                title=str(f.get("title", "")),
                prompt_hash=self._prompt_hash,
            )
            try:
                rec = FindingRecord(
                    id=fid,
                    file=file_relpath,
                    category=str(f.get("category", "")),
                    priority=f.get("priority", "low"),
                    title=str(f.get("title", "")),
                    issue=str(f.get("issue", "")),
                    why=str(f.get("why", "")),
                    fix=str(f.get("fix", "")),
                    confidence=f.get("confidence", "low"),
                    location=location,
                    report_path=f"{file_relpath}.md",
                    suppressed=False,
                    prompt_hash=self._prompt_hash,
                    config_hash=self._config_hash,
                    model_fingerprint=getattr(
                        self._client, "_fingerprint_pinned", ""
                    )
                    or "",
                    lens_version=str(getattr(self, "_lens_version", "1.0.0")),
                )
            except Exception as exc:  # noqa: BLE001 — drop malformed; ARCH-13-style tolerance
                log.warning("dropping malformed finding from %s: %s", file_relpath, exc)
                continue
            self._partial_writer.append(rec)

    @staticmethod
    def _summarize_findings(response: dict[str, Any]) -> dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0, "healthy": 0}
        for f in response.get("findings") or []:
            if not isinstance(f, dict):
                continue
            prio = f.get("priority", "")
            if prio in counts:
                counts[prio] += 1
        return counts

    def _write_thinking_trace(self, report_relpath: str, reasoning: str) -> None:
        from senex.atomic_io import write_text_atomic

        target = self._audit_dir / f"{report_relpath}.thinking.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        body = f"# Thinking trace: {report_relpath}\n\n```\n{reasoning}\n```\n"
        write_text_atomic(target, body)


# Re-export the small helpers so unit tests can drive them.
__all__ = ["FileAuditPhase", "ToolCallSummary"]
