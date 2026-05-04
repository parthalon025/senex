"""senex.tools.run_semgrep - semgrep security scanner tool.

Implements spec section 5.11.1 (run_semgrep in v1 tool set), section SEC-1
(path-safety), section SEC-4 (subprocess hardening: list-form args,
shell=False, injection-safe rules validation), and section 11.1 threat model.

Hardening enforced here:

- ``rules`` field constrained to ``^[a-zA-Z0-9/_:. -]+$`` at pydantic
  boundary AND belt-and-suspenders re-validated in the handler before
  subprocess invocation.
- ``relpath`` validated via ``validate_repo_path`` (traversal + symlink
  rejection).
- subprocess launched with ``asyncio.create_subprocess_exec`` (list-form,
  never shell=True).
- ``--quiet`` flag suppresses semgrep's progress output to stdout so only
  JSON flows there.
- semgrep exits with code 1 when findings exist (not an error); we check
  for valid JSON first and only raise on non-zero exit when JSON is absent.
- Engine version fetched best-effort in a 3-second sub-timeout; failure is
  silently degraded to ``"semgrep"``.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .context import ToolContext
from .exceptions import (
    PathOutsideRepo,
    SymlinkRefused,
    ToolDispatchFailed,
    ToolUnavailable,
)
from .registry import ToolRegistry
from .safety import validate_repo_path

_TOOL_NAME: Final[str] = "run_semgrep"
_DESCRIPTION: Final[str] = (
    "Run semgrep security scan on a file. Default rules='auto' auto-detects "
    "language and applies relevant security rules (covers CWE-22 path traversal, "
    "CWE-78 command injection, CWE-89 SQL injection, CWE-208 timing attacks, "
    "CWE-502 deserialization, CWE-798 hardcoded credentials, CWE-918 SSRF). "
    "Returns ToolError(unavailable) when semgrep is not installed."
)

_RULES_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9/_:. -]+$")
_VERSION_TIMEOUT_S: Final[float] = 3.0


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RunSemgrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str = Field(min_length=1, max_length=500)
    rules: str = Field(default="auto", max_length=200)
    max_findings: int = Field(default=20, ge=1, le=50)

    @field_validator("rules")
    @classmethod
    def validate_rules_pattern(cls, v: str) -> str:
        if not _RULES_RE.fullmatch(v):
            raise ValueError(
                "rules must match ^[a-zA-Z0-9/_:. -]+$ to prevent injection"
            )
        return v


class SemgrepFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line: int
    end_line: int | None
    rule_id: str
    severity: str  # "ERROR", "WARNING", "INFO" -- forwarded verbatim from semgrep
    message: str
    fix: str | None  # autofix suggestion if semgrep provides one


class RunSemgrepOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str
    findings: list[SemgrepFinding]
    truncated: bool
    engine: str  # "semgrep" + version string from semgrep --version, best-effort


# ---------------------------------------------------------------------------
# Handler helpers
# ---------------------------------------------------------------------------


async def _get_semgrep_version() -> str:
    """Fetch semgrep version string; return ``"semgrep"`` on any failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "semgrep",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_VERSION_TIMEOUT_S
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return "semgrep"
        raw = stdout.decode("utf-8", errors="replace").strip()
        return f"semgrep {raw}" if raw else "semgrep"
    except Exception:  # noqa: BLE001 -- best-effort; any failure degrades gracefully
        return "semgrep"


def _parse_findings(
    payload: dict[str, object],
    max_findings: int,
) -> tuple[list[SemgrepFinding], bool]:
    """Extract and normalise findings from a semgrep JSON payload."""
    if "results" not in payload:
        raise ToolDispatchFailed("unexpected semgrep output format")

    raw_results = payload["results"]
    if not isinstance(raw_results, list):
        raise ToolDispatchFailed("unexpected semgrep output format: results not a list")

    findings: list[SemgrepFinding] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        start = item.get("start") or {}
        end_blk = item.get("end") or {}
        extra = item.get("extra") or {}

        line = start.get("line") if isinstance(start, dict) else None
        if not isinstance(line, int):
            continue

        end_line_val = end_blk.get("line") if isinstance(end_blk, dict) else None
        end_line: int | None = end_line_val if isinstance(end_line_val, int) else None

        rule_id = str(item.get("check_id") or "")
        severity = str(extra.get("severity") or "INFO") if isinstance(extra, dict) else "INFO"
        message = str(extra.get("message") or "") if isinstance(extra, dict) else ""

        fix_raw = extra.get("fix") if isinstance(extra, dict) else None
        fix: str | None = str(fix_raw) if fix_raw is not None else None

        findings.append(
            SemgrepFinding(
                line=line,
                end_line=end_line,
                rule_id=rule_id,
                severity=severity,
                message=message,
                fix=fix,
            )
        )

    # Sort by line number for deterministic output.
    findings.sort(key=lambda f: f.line)
    truncated = len(findings) > max_findings
    return findings[:max_findings], truncated


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def run_semgrep_handler(
    inp: RunSemgrepInput, ctx: ToolContext
) -> RunSemgrepOutput:
    """Run semgrep on ``inp.relpath`` with hardened subprocess invocation.

    The handler:
    1. Belt-and-suspenders validates ``rules`` (pydantic already checked).
    2. Validates path via ``validate_repo_path``.
    3. Checks semgrep availability via ``shutil.which``.
    4. Launches semgrep with ``--json --quiet --config <rules> <path>``.
    5. Parses stdout JSON; treats non-zero exit as error only when no JSON.
    6. Fetches engine version best-effort.

    subprocess is invoked via ``asyncio.create_subprocess_exec`` with a
    list-form argv (never shell=True) per SEC-4 subprocess hardening rules.
    """
    # Belt-and-suspenders rules check (pydantic field_validator already ran this).
    if not _RULES_RE.fullmatch(inp.rules):
        raise ToolDispatchFailed(
            f"rules value failed injection check: {inp.rules!r}"
        )

    try:
        resolved = validate_repo_path(inp.relpath, ctx.repo_root)
    except (PathOutsideRepo, SymlinkRefused):
        raise

    if shutil.which("semgrep") is None:
        raise ToolUnavailable("semgrep is not installed or not on PATH")

    if not resolved.exists():
        raise ToolDispatchFailed(f"file not found: {inp.relpath}")
    if not resolved.is_file():
        raise ToolDispatchFailed(f"not a regular file: {inp.relpath}")

    # List-form argv -- SEC-4: never string-concatenated, never shell=True.
    argv = [
        "semgrep",
        "--json",
        "--quiet",
        "--config",
        inp.rules,
        str(resolved),
    ]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=ctx.tool_timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    stdout_text = stdout.decode("utf-8", errors="replace").strip()

    # semgrep exits 1 when findings exist; check for valid JSON first.
    payload: dict[str, object] | None = None
    if stdout_text:
        try:
            parsed = json.loads(stdout_text)
            if isinstance(parsed, dict):
                payload = parsed
        except ValueError:
            pass

    if proc.returncode != 0 and payload is None:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        raise ToolDispatchFailed(
            stderr_text[:300]
            or f"semgrep exited rc={proc.returncode} with no JSON output"
        )

    if payload is None:
        # Exit 0 but no JSON output -- treat as zero findings.
        payload = {"results": []}

    findings, truncated = _parse_findings(payload, inp.max_findings)
    engine = await _get_semgrep_version()

    return RunSemgrepOutput(
        relpath=inp.relpath,
        findings=findings,
        truncated=truncated,
        engine=engine,
    )


def register_run_semgrep(registry: ToolRegistry) -> None:
    """Register the ``run_semgrep`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        RunSemgrepInput,
        run_semgrep_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "RunSemgrepInput",
    "RunSemgrepOutput",
    "SemgrepFinding",
    "register_run_semgrep",
    "run_semgrep_handler",
]
