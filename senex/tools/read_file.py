"""senex.tools.read_file - safe filesystem slice reader.

Implements spec section 5.11.1 (read_file in v1 tool set), section SEC-1
(path-safety), and section 11.1 threat model row read_file traversal.

Hardening enforced here:

- ``relpath`` validated through ``validate_repo_path`` -> resolves and
  refuses ``..``, UNC, drive-absolute outside repo, AND symlinks (any
  ancestor).
- File size cap of 5MB (defensive; the walker's 512KB cap is for
  audit-eligible files, this allows occasional larger reads).
- UTF-8 with ``errors="replace"`` to tolerate binary-ish files without
  raising.
- Default slice: line_start=1, line_end=line_start+200 when both omitted.
- Hard cap: line_end - line_start <= 2000 (enforced by pydantic).
"""
from __future__ import annotations

import asyncio
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .context import ToolContext
from .exceptions import (
    PathOutsideRepo,
    SymlinkRefused,
    ToolDispatchFailed,
)
from .registry import ToolRegistry
from .safety import validate_repo_path

_TOOL_NAME: Final[str] = "read_file"
_DESCRIPTION: Final[str] = (
    "Read a slice of a file from the audited repo. relpath is validated "
    "against repo_root (refuses traversal, symlinks, drive-absolute escape). "
    "Default slice is 200 lines starting from line 1; pass line_start/"
    "line_end (1-indexed, inclusive) to read elsewhere. Range capped at 2000 lines."
)
_MAX_FILE_SIZE_BYTES: Final[int] = 5_000_000  # 5MB hard cap.
_DEFAULT_SLICE_LINES: Final[int] = 200


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str = Field(min_length=1, max_length=500)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> ReadFileInput:
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end must be >= line_start")
            if self.line_end - self.line_start > 2000:
                raise ValueError("line range cannot exceed 2000 lines")
        return self


class ReadFileOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str
    line_start: int
    line_end: int
    content: str


async def read_file_handler(
    inp: ReadFileInput, ctx: ToolContext
) -> ReadFileOutput:
    """Read a slice of `inp.relpath` after path-safety validation.

    File I/O runs on a worker thread (conventions section 3: no sync I/O
    in async paths).
    """
    try:
        resolved = validate_repo_path(inp.relpath, ctx.repo_root)
    except (PathOutsideRepo, SymlinkRefused):
        # Re-raise so the registry sees the structured tooling error and
        # maps it to ToolError(kind="path_rejected").
        raise

    def _read_sync() -> ReadFileOutput:
        if not resolved.exists():
            raise ToolDispatchFailed(f"file not found: {inp.relpath}")
        if not resolved.is_file():
            raise ToolDispatchFailed(f"not a regular file: {inp.relpath}")
        size = resolved.stat().st_size
        if size > _MAX_FILE_SIZE_BYTES:
            raise ToolDispatchFailed(
                f"file too large to read ({size} > {_MAX_FILE_SIZE_BYTES} bytes)"
            )
        text = resolved.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        total = len(lines)

        line_start = inp.line_start if inp.line_start is not None else 1
        if inp.line_end is not None:
            line_end = inp.line_end
        elif inp.line_start is not None:
            line_end = min(total, inp.line_start + _DEFAULT_SLICE_LINES - 1)
        else:
            line_end = min(total, _DEFAULT_SLICE_LINES)
        # Clamp to actual file length when caller exceeds it.
        line_end = min(line_end, total)
        line_start = min(line_start, max(1, line_end))
        sliced = "".join(lines[line_start - 1 : line_end])
        return ReadFileOutput(
            relpath=inp.relpath,
            line_start=line_start,
            line_end=line_end,
            content=sliced,
        )

    return await asyncio.to_thread(_read_sync)


def register_read_file(registry: ToolRegistry) -> None:
    """Register the ``read_file`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        ReadFileInput,
        read_file_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "ReadFileInput",
    "ReadFileOutput",
    "read_file_handler",
    "register_read_file",
]
