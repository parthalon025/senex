"""Graph awareness builder -- pre-flight per-file context via `npx gitnexus`.

Implements spec section 5.8 (GitNexus context builder) and section SEC-4
(subprocess hardening). v1 ships ``GitNexusCLIProvider``; future
``GitNexusMCPProvider`` / ``NoopProvider`` implement the same Protocol.

Subprocess invariants enforced here:

- list-form args; ``shell=False`` is mandatory
- ``npx`` absolute path resolved once at preflight (defeats PATH hijack)
- relpath validated against ``^[A-Za-z0-9_./\\\\-]+$`` BEFORE args list
- timeout on every subprocess call
- failure -> ``GraphContextUnavailable`` event + sentinel block; never aborts
  the file (auditor proceeds with empty awareness per spec section 5.8
  closing paragraph)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .events import EventBus, GraphContextUnavailable

_UNAVAILABLE_SENTINEL: str = "[graph context unavailable]"
_UNINDEXED_SENTINEL: str = "[graph context: file not in index]"


class GraphAwarenessError(Exception):
    """Base for graph awareness errors."""


class GitNexusUnavailable(GraphAwarenessError):
    """`npx` not on PATH; raised at preflight only."""


class RelpathRejected(GraphAwarenessError):
    """relpath failed the safety regex; never reaches the subprocess layer."""


class GitNexusSubprocessFailed(GraphAwarenessError):
    """Subprocess returned non-zero, timed out, or produced unparseable JSON.

    Caller catches this and emits ``GraphContextUnavailable`` + falls through
    to the sentinel block; it does NOT abort the file.
    """


@dataclass(frozen=True)
class GraphContext:
    """Pre-flight graph context for one source file (spec section 5.8)."""

    cluster: str | None
    cluster_summary: str | None
    public_symbols: list[str]
    callers_d1_count: dict[str, int]
    top_processes: list[tuple[str, str]]
    available: bool
    raw_text_block: str


def _make_unavailable() -> GraphContext:
    return GraphContext(
        cluster=None,
        cluster_summary=None,
        public_symbols=[],
        callers_d1_count={},
        top_processes=[],
        available=False,
        raw_text_block=_UNAVAILABLE_SENTINEL,
    )


class GraphContextProvider(Protocol):
    async def fetch(self, file_relpath: str) -> GraphContext: ...


class GitNexusCLIProvider:
    """v1 graph-context provider that shells out to `npx gitnexus`.

    Hardening (spec section SEC-4):

    - list-form args (never string-concatenated, never ``shell=True``)
    - npx absolute path pinned at preflight (defeats PATH hijack)
    - relpath regex-validated BEFORE args list construction
    - mandatory subprocess timeout
    """

    # Section SEC-4: only ASCII alphanumerics + _ . / \ - allowed in relpath.
    RELPATH_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_./\\-]+$")

    def __init__(
        self,
        repo_name: str,
        npx_path: str,
        bus: EventBus,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._repo = repo_name
        self._npx_path = npx_path
        self._bus = bus
        self._timeout = timeout_seconds
        self._cache: dict[str, GraphContext] = {}

    @classmethod
    def _is_safe_relpath(cls, relpath: str) -> bool:
        """Reject path-traversal / absolute / control-char relpaths.

        The base regex ``RELPATH_RE`` is the byte-set allowlist; this layer
        adds semantic rules:

        - no traversal (``..``)
        - no absolute paths (leading ``/`` or drive letter)
        - no control characters (incl. newline / null)
        - no empty input
        """
        if not relpath:
            return False
        if not cls.RELPATH_RE.match(relpath):
            return False
        if ".." in relpath:
            return False
        if relpath.startswith("/") or relpath.startswith("\\"):
            return False
        # Drive-letter absolute (e.g. "C:/...") -- the regex blocks ":" so
        # this is already covered, but check defensively.
        if len(relpath) >= 2 and relpath[1] == ":":
            return False
        return True

    @classmethod
    async def preflight(
        cls, repo_name: str, bus: EventBus
    ) -> GitNexusCLIProvider:
        """Resolve `npx` once and pin the absolute path for the run.

        On Windows, ``shutil.which`` may return ``None`` when senex is launched
        from Git Bash (which omits the Node.js directory from PATH).  We fall
        back to well-known installation paths before giving up.

        Raises:
            GitNexusUnavailable: npx not found anywhere.
        """
        npx = shutil.which("npx")
        if npx is None and sys.platform == "win32":
            _candidates = [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npx.cmd",
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs" / "npx.cmd",
                Path(os.environ.get("APPDATA", "")) / "npm" / "npx.cmd",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "npx.cmd",
            ]
            for candidate in _candidates:
                if candidate.is_file():
                    npx = str(candidate)
                    break
        if not npx:
            raise GitNexusUnavailable("npx not found on PATH or in common Windows locations")
        return cls(repo_name=repo_name, npx_path=npx, bus=bus)

    async def fetch(self, file_relpath: str) -> GraphContext:
        """Return graph context for ``file_relpath`` (cache hit if available).

        On any subprocess / regex / parse failure, returns the unavailable
        sentinel and emits ``GraphContextUnavailable``. Never raises.
        """
        if file_relpath in self._cache:
            return self._cache[file_relpath]

        # 1. Validate relpath BEFORE any subprocess construction (SEC-4).
        if not self._is_safe_relpath(file_relpath):
            await self._emit_unavailable(
                file_relpath, "relpath rejected by safety regex"
            )
            ctx = _make_unavailable()
            self._cache[file_relpath] = ctx
            return ctx

        # 2. Run gitnexus query (list-form, shell=False).
        try:
            query_json = await self._run(
                [
                    "query",
                    "--repo",
                    self._repo,
                    f"What does {file_relpath} do?",
                ]
            )
        except GitNexusSubprocessFailed as exc:
            await self._emit_unavailable(file_relpath, str(exc))
            ctx = _make_unavailable()
            self._cache[file_relpath] = ctx
            return ctx

        ctx = self._parse(query_json)
        self._cache[file_relpath] = ctx
        return ctx

    async def prefetch_all(
        self, file_relpaths: list[str]
    ) -> dict[str, GraphContext]:
        """Fetch many files concurrently; populate cache; return view."""
        results = await asyncio.gather(
            *(self.fetch(rp) for rp in file_relpaths)
        )
        return {rp: ctx for rp, ctx in zip(file_relpaths, results)}

    def _npx_argv(self) -> tuple[str, ...]:
        """Return the argv prefix for invoking npx.

        On Windows, ``.cmd`` / ``.bat`` wrappers must go through ``cmd.exe``
        because ``CreateProcess`` cannot execute batch files directly.
        """
        if sys.platform == "win32" and Path(self._npx_path).suffix.lower() in (".cmd", ".bat"):
            return ("cmd.exe", "/c", self._npx_path)
        return (self._npx_path,)

    async def _run(self, args: list[str]) -> dict[str, Any]:
        """Subprocess wrapper -- the security boundary (section SEC-4).

        NEVER ``shell=True``; NEVER string-concatenated args.
        """
        _npx_argv = self._npx_argv()
        _full_argv = (*_npx_argv, "gitnexus", *args)
        proc = await asyncio.create_subprocess_exec(
            *_full_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError as cause:
            proc.kill()
            await proc.wait()
            raise GitNexusSubprocessFailed(
                f"timeout after {self._timeout}s"
            ) from cause
        if proc.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")[:500]
            raise GitNexusSubprocessFailed(
                f"npx gitnexus {' '.join(args)} -> rc={proc.returncode}, "
                f"stderr={stderr_text}"
            )
        try:
            return json.loads(stdout.decode("utf-8"))  # type: ignore[no-any-return]
        except json.JSONDecodeError as cause:
            raise GitNexusSubprocessFailed(
                f"unparseable JSON: {cause}"
            ) from cause

    def _parse(self, query_json: dict[str, Any]) -> GraphContext:
        """Convert raw ``query`` JSON into a ``GraphContext``.

        The CLI's ``query`` output shape is:
            ``{processes: [...], process_symbols: [...], definitions: [...]}``
        We extract:

        - ``public_symbols``: definition names
        - ``top_processes``: first 3 ``(name, summary)`` tuples
        - ``cluster``: not in ``query`` -- left None
        - ``callers_d1_count``: not in ``query`` -- left empty
        """
        # Detect "no information" responses (CLI returned an ``error`` key).
        if "error" in query_json:
            return GraphContext(
                cluster=None,
                cluster_summary=None,
                public_symbols=[],
                callers_d1_count={},
                top_processes=[],
                available=True,
                raw_text_block=_UNINDEXED_SENTINEL,
            )

        defs = query_json.get("definitions") or []
        symbols: list[str] = [
            str(d.get("name", "")) for d in defs if d.get("name")
        ]
        processes_raw = query_json.get("processes") or []
        top_processes: list[tuple[str, str]] = []
        for p in processes_raw[:3]:
            name = str(p.get("name", ""))
            summary = str(p.get("summary", "") or p.get("description", ""))
            top_processes.append((name, summary))

        block = self._render_block(symbols, top_processes)
        return GraphContext(
            cluster=None,
            cluster_summary=None,
            public_symbols=symbols,
            callers_d1_count={},
            top_processes=top_processes,
            available=True,
            raw_text_block=block,
        )

    @staticmethod
    def _render_block(
        symbols: list[str],
        processes: list[tuple[str, str]],
    ) -> str:
        lines: list[str] = []
        if symbols:
            lines.append(
                "Public symbols / definitions: " + ", ".join(symbols[:20])
            )
        if processes:
            lines.append("Top processes:")
            for name, summary in processes:
                if summary:
                    lines.append(f"  - {name}: {summary}")
                else:
                    lines.append(f"  - {name}")
        return "\n".join(lines) if lines else _UNINDEXED_SENTINEL

    async def _emit_unavailable(
        self, file_relpath: str, reason: str
    ) -> None:
        await self._bus.publish(
            GraphContextUnavailable(
                ts=datetime.now(tz=timezone.utc),
                seq=0,
                run_id="preflight",
                path=file_relpath,
                reason=reason,
            )
        )
