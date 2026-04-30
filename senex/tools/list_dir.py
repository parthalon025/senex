"""senex.tools.list_dir - safe directory listing tool.

Implements spec section 5.11.1 (list_dir in v1 tool set), section SEC-1
(path-safety), and section 11.1 threat model row list_dir traversal.

Hardening enforced here:

- ``relpath`` validated through ``validate_repo_path`` when non-empty
  (empty string / "." resolves directly to repo_root, always safe).
- Symlinks skipped entirely — neither counted nor returned.
- Only immediate children listed (not recursive).
- Output capped at ``max_entries``; ``truncated=True`` when more exist.
- All sync I/O dispatched via ``asyncio.to_thread`` (conventions §3).
"""
from __future__ import annotations

import asyncio
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .context import ToolContext
from .exceptions import (
    PathOutsideRepo,
    SymlinkRefused,
    ToolDispatchFailed,
)
from .registry import ToolRegistry
from .safety import validate_repo_path

_TOOL_NAME: Final[str] = "list_dir"
_DESCRIPTION: Final[str] = (
    "List immediate children of a directory inside the audited repo. "
    "Dirs appear before files, both sorted alphabetically. Symlinks are "
    "excluded. Returns up to max_entries entries; truncated=True when more exist."
)


class ListDirInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str = Field(default="", max_length=500)
    max_entries: int = Field(default=50, ge=1, le=200)


class DirEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["file", "dir"]
    size_bytes: int | None  # None for dirs


class ListDirOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str
    entries: list[DirEntry]
    truncated: bool


async def list_dir_handler(
    inp: ListDirInput, ctx: ToolContext
) -> ListDirOutput:
    """List immediate children of ``inp.relpath`` after path-safety validation.

    File I/O runs on a worker thread (conventions §3: no sync I/O in async
    paths). Empty relpath and "." are resolved directly to repo_root without
    calling validate_repo_path (repo_root is always safe by construction).
    """
    normalized = inp.relpath.strip()
    if normalized == "" or normalized == ".":
        resolved = ctx.repo_root
        display_relpath = "."
    else:
        try:
            resolved = validate_repo_path(normalized, ctx.repo_root)
        except (PathOutsideRepo, SymlinkRefused):
            raise
        display_relpath = normalized

    def _list_sync() -> ListDirOutput:
        if not resolved.exists():
            raise ToolDispatchFailed(f"directory not found: {inp.relpath!r}")
        if not resolved.is_dir():
            raise ToolDispatchFailed(f"not a directory: {inp.relpath!r}")

        dirs: list[DirEntry] = []
        files: list[DirEntry] = []

        try:
            children = list(resolved.iterdir())
        except OSError as exc:
            raise ToolDispatchFailed(f"cannot list directory: {exc}") from exc

        for child in children:
            try:
                if child.is_symlink():
                    continue
                if child.is_dir():
                    dirs.append(DirEntry(name=child.name, kind="dir", size_bytes=None))
                elif child.is_file():
                    try:
                        size = child.stat().st_size
                    except OSError:
                        size = None
                    files.append(DirEntry(name=child.name, kind="file", size_bytes=size))
            except OSError:
                continue

        dirs.sort(key=lambda e: e.name.lower())
        files.sort(key=lambda e: e.name.lower())

        all_entries = dirs + files
        truncated = len(all_entries) > inp.max_entries
        return ListDirOutput(
            relpath=display_relpath,
            entries=all_entries[: inp.max_entries],
            truncated=truncated,
        )

    return await asyncio.to_thread(_list_sync)


def register_list_dir(registry: ToolRegistry) -> None:
    """Register the ``list_dir`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        ListDirInput,
        list_dir_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "DirEntry",
    "ListDirInput",
    "ListDirOutput",
    "list_dir_handler",
    "register_list_dir",
]
