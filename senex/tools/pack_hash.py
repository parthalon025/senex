"""senex.tools.pack_hash - sha256 hash of (name, input_schema) pairs.

Implements M5 Task 5.10. The hash is part of the resume hash discipline
(spec section ARCH and section 8.5): Checkpoint, RunStart event, and
findings.json metadata all record it. Resuming with a different
``tool_pack_hash`` invalidates the run unless ``--allow-mixed-resume``
is passed (M10).

Stability properties:
- Stable across runs for the same (names, schemas).
- Sensitive to: adding/removing tools from ``enabled_tools``.
- Sensitive to: changing any tool's input schema (e.g. raising the
  max_length cap).
- Insensitive to: ``enabled_tools`` ordering (the function sorts).
"""
from __future__ import annotations

import hashlib
import json

from .registry import ToolRegistry


def compute_tool_pack_hash(
    enabled_tools: list[str], registry: ToolRegistry
) -> str:
    """Return sha256 of canonical JSON of ``[(name, input_schema)]`` for sorted names.

    Args:
        enabled_tools: list of registered tool names; ordering is ignored
            (sorted internally).
        registry: ``ToolRegistry`` populated with handlers for every name in
            ``enabled_tools``.

    Returns:
        Lowercase hex sha256 (64 characters).

    Raises:
        KeyError: a name in ``enabled_tools`` is not registered.
    """
    payload = [
        [name, registry.input_schema(name)]
        for name in sorted(enabled_tools)
    ]
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


__all__ = ["compute_tool_pack_hash"]
