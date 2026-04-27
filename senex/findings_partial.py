"""senex.findings_partial - append-only NDJSON streaming writer for findings.

Implements spec SCHEMA-3 (`f-<12hex>` stable id) and SCHEMA-4 (NDJSON
append-only persistence). One finding per line. The file is durable: each
`append()` ends with `flush()` + `os.fsync(fd)` so a crash never leaves an
indeterminately buffered line in flight - either the line is fully on disk
or the file ends mid-line.

`Aggregator.run()` (Task 7.3) reads this file, dedupes by id, sorts, and
writes the canonical `findings.json` atomically.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import TracebackType
from typing import IO

from senex.render_models import FindingRecord


def compute_finding_id(
    *,
    file: str,
    symbol: str | None,
    line_start: int | None,
    title: str,
    prompt_hash: str,
) -> str:
    """Stable finding id per spec SCHEMA-3.

    `"f-" + sha256(file | (symbol or '') | (line_start or '') | title | prompt_hash)[:12]`

    Stability:
        - Identical inputs => identical id (re-audit dedupe).
        - Different prompt_hash => different id (lens/prompt change creates a
          new finding identity, so heterogeneous resume buckets stay distinct).
    """
    payload = (
        f"{file}|{symbol or ''}|"
        f"{line_start if line_start is not None else ''}|"
        f"{title}|{prompt_hash}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"f-{digest}"


class FindingsPartialWriter:
    """Append-only NDJSON writer for `<audit_dir>/findings.partial.jsonl`.

    Per-call durability:
        1. Serialize finding via pydantic `model_dump_json()`.
        2. Single `write()` of `line + b"\\n"` (one finding per line).
        3. `flush()` then `os.fsync(fileno)`.

    Crash safety: the writer never holds a half-line in user space; a crash
    after `flush()` returns either commits the line fully or leaves the file
    truncated at a line boundary (the OS-level write granularity is the only
    failure surface here, and the aggregator skips a malformed LAST line).
    """

    def __init__(self, audit_dir: Path) -> None:
        self._audit_dir = audit_dir
        audit_dir.mkdir(parents=True, exist_ok=True)
        self._target = audit_dir / "findings.partial.jsonl"
        # Append-binary, no buffering across calls; we manage flush/fsync ourselves.
        self._fp: IO[bytes] | None = open(self._target, "ab", buffering=0)
        self._closed = False

    def append(self, finding: FindingRecord) -> None:
        """Serialize + durably append one finding."""
        if self._fp is None:
            raise RuntimeError("FindingsPartialWriter is closed")
        line = finding.model_dump_json().encode("utf-8")
        self._fp.write(line + b"\n")
        # buffering=0 means no python-level buffer, but OS may still cache;
        # fsync forces it to disk.
        os.fsync(self._fp.fileno())

    def close(self) -> None:
        """Flush + close the file. Idempotent."""
        if self._closed:
            return
        if self._fp is not None:
            try:
                self._fp.flush()
            finally:
                self._fp.close()
        self._fp = None
        self._closed = True

    def __enter__(self) -> FindingsPartialWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["FindingsPartialWriter", "compute_finding_id"]
