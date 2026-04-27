"""senex.tui.runtime — Launcher → Auditor handoff value object (M9).

The Launcher screen builds a ``RuntimeConfig`` from form fields and
passes it to ``SenexApp.start_audit``; the app constructs the auditor
task from this object. Per conventions §10: pydantic v2 with
``extra="forbid"``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from senex.config import SenexConfig
from senex.lens import Lens


class RuntimeConfig(BaseModel):
    """Validated launcher form snapshot ready for ``run_audit``."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    repo: Path
    config: SenexConfig
    lens: Lens
    config_path: Path
    output_root: Path | None = None
    resume: bool = False
    allow_mixed_resume: bool = False
    cli_overrides: dict[str, Any] = Field(default_factory=dict)


__all__ = ["RuntimeConfig"]
