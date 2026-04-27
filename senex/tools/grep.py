"""senex.tools.grep - regex search with timeout and ReDoS guards.

Implements spec section 5.11.1 (grep in v1 tool set), section 5.11.4 (regex
hardening: 256-char cap, ``regex`` library with per-call timeout, syntactic
ReDoS-shape rejection), and threat-model row 11.1 (regex DoS).

Hardening enforced here:

- Pattern length cap = 256 (validated at the pydantic boundary via
  ``validate_regex_pattern``).
- ``regex`` library, NOT stdlib ``re`` — supports per-call ``timeout``.
- Syntactic shape rejection (``(...+)+`` etc.) AT validation time.
- Per-line ``compiled.search(line, timeout=0.05)`` at scan time; a file
  whose individual line exceeds the budget is logged + skipped (the rest
  of the scan continues).
- glob constrained by an allowlist regex covering ``[A-Za-z0-9_./\\-*?[]{}]``.
- Symlinks skipped; files > 1MB skipped.
- ``max_matches`` capped at 20 by the model; loop bounded by file count.
"""
from __future__ import annotations

import asyncio
import logging
import re as _stdlib_re
from pathlib import Path
from typing import Final

import regex  # type: ignore[import-untyped, unused-ignore]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .context import ToolContext
from .exceptions import RegexTimeoutExceeded, RegexTooComplex, ToolDispatchFailed
from .registry import ToolRegistry
from .safety import validate_regex_pattern

log = logging.getLogger(__name__)

_TOOL_NAME: Final[str] = "grep"
_DESCRIPTION: Final[str] = (
    "Search the audited repo for a regex. Pattern length capped at 256 "
    "and rejected if it resembles a catastrophic-backtracking shape. "
    "Returns up to max_matches matches with file/line/text; truncated=True "
    "if more files remain unscanned."
)

# Walker default extensions (subset; mirror conventions).
_DEFAULT_EXTENSIONS: Final[tuple[str, ...]] = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
    ".cs", ".cpp", ".c", ".h", ".hpp", ".swift", ".rb", ".php", ".sh",
    ".ps1", ".scala", ".ex", ".exs", ".dart", ".lua", ".zig", ".nim",
    ".md", ".txt",
)
_FILE_SIZE_CAP_BYTES: Final[int] = 1_000_000  # 1MB per file.
_PER_LINE_TIMEOUT_S: Final[float] = 0.05
_GLOB_ALLOWED: Final[_stdlib_re.Pattern[str]] = _stdlib_re.compile(
    r"^[A-Za-z0-9_./\\\-\*\?\[\]\{\}]+$"
)


class GrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=256)
    glob: str | None = Field(default=None, max_length=200)
    max_matches: int = Field(default=20, ge=1, le=20)

    @field_validator("pattern")
    @classmethod
    def validate_pattern_complexity(cls, v: str) -> str:
        # Convert RegexTooComplex / RegexTimeoutExceeded -> ValueError so
        # pydantic wraps them into a ValidationError that the registry maps
        # to ToolError(kind="schema_invalid"). The original message is
        # preserved for the model to read.
        try:
            validate_regex_pattern(v)
        except (RegexTooComplex, RegexTimeoutExceeded) as exc:
            raise ValueError(str(exc)) from exc
        return v

    @field_validator("glob")
    @classmethod
    def validate_glob(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GLOB_ALLOWED.fullmatch(v):
            raise ValueError(
                "glob must match ^[A-Za-z0-9_./\\\\-*?[]{}]+$"
            )
        return v


class GrepMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    line: int
    text: str


class GrepOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matches: list[GrepMatch]
    truncated: bool


def _candidate_files(repo_root: Path, glob: str | None) -> list[Path]:
    """Enumerate files to scan; skip symlinks and oversize files."""
    if glob is not None:
        # Use pathlib's glob; prefer rglob when caller provides ``**/``.
        candidates = list(repo_root.rglob(glob.removeprefix("**/")))
    else:
        candidates = [
            p for p in repo_root.rglob("*")
            if p.suffix in _DEFAULT_EXTENSIONS
        ]
    out: list[Path] = []
    for p in candidates:
        try:
            if p.is_symlink():
                continue
            if not p.is_file():
                continue
            if p.stat().st_size > _FILE_SIZE_CAP_BYTES:
                continue
        except OSError:
            continue
        out.append(p)
    out.sort()
    return out


def _scan_one_file(
    p: Path, repo_root: Path, compiled: regex.Pattern[str], remaining: int
) -> list[GrepMatch]:
    """Return up to ``remaining`` matches from ``p``; per-line timeout."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    matches: list[GrepMatch] = []
    relpath = p.relative_to(repo_root).as_posix()
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(matches) >= remaining:
            break
        try:
            if compiled.search(line, timeout=_PER_LINE_TIMEOUT_S):
                matches.append(
                    GrepMatch(
                        file=relpath,
                        line=lineno,
                        text=line[:512],  # bound per-line text size.
                    )
                )
        except TimeoutError:
            log.warning(
                "grep per-line timeout in %s:%d (skipping rest of file)",
                relpath,
                lineno,
            )
            break
    return matches


async def grep_handler(inp: GrepInput, ctx: ToolContext) -> GrepOutput:
    """Walk the repo and run ``regex`` search per line; bounded by max_matches."""
    try:
        compiled = regex.compile(inp.pattern, flags=regex.MULTILINE)
    except (regex.error, _stdlib_re.error) as exc:
        # Already validated, but defense in depth.
        raise ToolDispatchFailed(f"pattern compile failed: {exc}") from exc

    def _walk_sync() -> GrepOutput:
        try:
            files = _candidate_files(ctx.repo_root, inp.glob)
        except OSError as exc:
            raise ToolDispatchFailed(f"walk failed: {exc}") from exc
        all_matches: list[GrepMatch] = []
        truncated = False
        for i, f in enumerate(files):
            remaining = inp.max_matches - len(all_matches)
            if remaining <= 0:
                # More files exist; mark truncated.
                if i < len(files):
                    truncated = True
                break
            all_matches.extend(_scan_one_file(f, ctx.repo_root, compiled, remaining))
        if len(all_matches) >= inp.max_matches and len(all_matches) < sum(
            1 for _ in files
        ):
            # Hit the cap before exhausting files.
            truncated = True
        return GrepOutput(matches=all_matches[: inp.max_matches], truncated=truncated)

    return await asyncio.to_thread(_walk_sync)


def register_grep(registry: ToolRegistry) -> None:
    """Register the ``grep`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        GrepInput,
        grep_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "GrepInput",
    "GrepMatch",
    "GrepOutput",
    "grep_handler",
    "register_grep",
]
