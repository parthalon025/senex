"""senex.memory — cross-file finding accumulator for the audit loop."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal


_PRIORITY_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2, "healthy": 3}

_HEADER = (
    "### PATTERNS SEEN IN EARLIER FILES\n\n"
    "Review these findings from files already audited in this run. "
    "Use them as leads — they may indicate systemic issues worth verifying "
    "in the current file.\n"
)


class MemoryBuffer:
    """Rolling buffer of notable findings from previously audited files.

    Call ``update(audit_dir)`` before each new file audit to ingest any
    findings written since the last call. ``format_injection()`` returns
    a system-message string (or None when the buffer is empty).

    ``update`` is idempotent: it tracks seen finding IDs so repeated calls
    on the same partial file do not double-count entries.
    """

    def __init__(
        self,
        max_tokens: int = 800,
        min_priority: Literal["high", "medium", "low"] = "medium",
    ) -> None:
        self._max_tokens = max_tokens
        self._min_priority = min_priority
        self._seen_ids: set[str] = set()
        self._lines: list[str] = []

    @property
    def finding_count(self) -> int:
        """Number of distinct findings currently held in the buffer."""
        return len(self._seen_ids)

    def update(self, audit_dir: Path) -> None:
        """Ingest new findings from findings.partial.jsonl (idempotent)."""
        partial = audit_dir / "findings.partial.jsonl"
        if not partial.is_file():
            return
        cutoff = _PRIORITY_ORDER[self._min_priority]
        for raw in partial.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            fid = rec.get("id", "")
            if fid in self._seen_ids:
                continue
            if _PRIORITY_ORDER.get(rec.get("priority", "low"), 3) > cutoff:
                continue
            self._seen_ids.add(fid)
            loc = rec.get("location", {})
            line = loc.get("line_start", "?")
            priority = rec.get("priority", "low").upper()
            self._lines.append(
                f"- [{priority}] {rec.get('file', '?')}:{line} — {rec.get('title', '')}"
            )

    def format_injection(self) -> str | None:
        """Return a system message string, or None when the buffer is empty."""
        if not self._lines:
            return None
        budget_chars = self._max_tokens * 4  # approx 1 token = 4 chars
        block = _HEADER
        for ln in self._lines:
            candidate = block + ln + "\n"
            if len(candidate) > budget_chars:
                block += "... (additional findings omitted)\n"
                break
            block = candidate
        return block
