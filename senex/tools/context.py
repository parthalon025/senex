"""senex.tools.context — ``ToolContext`` dataclass passed to every handler.

Implements M5 plan key contracts (§``ToolContext``). The context carries
the per-run resources every tool needs: repo root, repo name (for
``npx gitnexus --repo <name>``), the secret redactor, the absolute
``npx`` path resolved at preflight, and tunable timeout / token caps.

Conventions §1: pathlib over os.path. Conventions §10: pydantic at every
boundary, but ``ToolContext`` is internal and held in memory only — a
frozen dataclass is sufficient and avoids the overhead of pydantic
re-validation on every dispatch.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from senex.secret_redactor import SecretRedactor


@dataclass(frozen=True)
class ToolContext:
    """Per-run resources injected into every tool handler.

    Attributes:
        repo_root: absolute, pre-resolved path to the audited repo.
        repo_name: name passed to ``npx gitnexus --repo <name>``.
        redactor: shared ``SecretRedactor`` for output redaction.
        npx_path: absolute path to the ``npx`` binary, resolved at preflight.
        tool_timeout_seconds: per-call timeout enforced by the registry.
        max_result_tokens: token cap applied during output truncation.
        npx_cmd: full subprocess argv prefix for invoking npx; computed from
            ``npx_path``.  On Windows, ``.cmd`` / ``.bat`` wrappers are
            prefixed with ``["cmd.exe", "/c"]`` so ``asyncio.create_subprocess_exec``
            can execute them (Windows ``CreateProcess`` cannot run batch files
            directly).
    """

    repo_root: Path
    repo_name: str
    redactor: SecretRedactor
    npx_path: Path
    tool_timeout_seconds: float = 30.0
    max_result_tokens: int = 2048
    npx_cmd: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        npx_str = str(self.npx_path)
        if sys.platform == "win32" and Path(npx_str).suffix.lower() in (".cmd", ".bat"):
            object.__setattr__(self, "npx_cmd", ("cmd.exe", "/c", npx_str))
        else:
            object.__setattr__(self, "npx_cmd", (npx_str,))


__all__ = ["ToolContext"]
