"""senex.lens — Lens dataclass + loader for ``senex/lens/<name>/``.

Implements spec §5.0 (Lens abstraction) and §5.11.2 (per-lens tool packs).
Each Lens is loaded from a directory containing ``lens.toml`` and ``tools.toml``;
the lens fingerprint is sha256 over the concatenated bytes of all files referenced.
"""
from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LensNotFound(FileNotFoundError):
    """Raised when ``Lens.load(name)`` cannot find ``senex/lens/<name>/lens.toml``."""


class LensValidationError(ValueError):
    """Raised when a lens.toml or tools.toml fails schema validation."""


class _LensTomlSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1, le=1)  # v1 only.
    name: str
    version: str
    description: str
    system_prompt: str
    response_schema: str
    crosscut_prompt: str
    crosscut_schema: str
    renderer_template: str
    category_taxonomy: list[str] = Field(min_length=1)


class _ToolsTomlSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled_tools: list[str]


@dataclass(frozen=True)
class Lens:
    """A loaded Lens (spec §5.0)."""

    name: str
    version: str
    description: str
    system_prompt_path: Path
    response_schema_path: Path
    crosscut_prompt_path: Path
    crosscut_schema_path: Path
    renderer_template_path: Path
    category_taxonomy: tuple[str, ...]
    tools: list[str] = field(default_factory=list)
    fingerprint: str = ""

    @classmethod
    def load(cls, name: str) -> Lens:
        """Load + validate the lens directory at ``senex/lens/<name>/``.

        Raises:
            LensNotFound: directory or lens.toml missing.
            LensValidationError: schema mismatch on lens.toml or tools.toml.
        """
        root = _lens_root() / name
        lens_toml = root / "lens.toml"
        tools_toml = root / "tools.toml"
        if not lens_toml.exists():
            raise LensNotFound(f"lens.toml not found at {lens_toml}")
        try:
            ldata: dict[str, Any] = tomllib.loads(lens_toml.read_text(encoding="utf-8"))
            tdata: dict[str, Any] = tomllib.loads(tools_toml.read_text(encoding="utf-8"))
            ls = _LensTomlSchema.model_validate(ldata)
            ts = _ToolsTomlSchema.model_validate(tdata)
        except ValidationError as exc:
            raise LensValidationError(str(exc)) from exc

        system_prompt_path = root / ls.system_prompt
        crosscut_prompt_path = root / ls.crosscut_prompt
        renderer_template_path = root / ls.renderer_template
        # Schemas live in senex/schema/, not in the lens dir.
        schema_root = _schema_root()
        response_schema_path = schema_root / ls.response_schema
        crosscut_schema_path = schema_root / ls.crosscut_schema

        fingerprint = _hash_paths(
            [
                lens_toml, tools_toml,
                system_prompt_path, crosscut_prompt_path, renderer_template_path,
            ]
        )

        return cls(
            name=ls.name,
            version=ls.version,
            description=ls.description,
            system_prompt_path=system_prompt_path,
            response_schema_path=response_schema_path,
            crosscut_prompt_path=crosscut_prompt_path,
            crosscut_schema_path=crosscut_schema_path,
            renderer_template_path=renderer_template_path,
            category_taxonomy=tuple(ls.category_taxonomy),
            tools=list(ts.enabled_tools),
            fingerprint=fingerprint,
        )


def _lens_root() -> Path:
    # Resolved relative to the senex package install location.
    return Path(__file__).parent / "lens"


def _schema_root() -> Path:
    return Path(__file__).parent / "schema"


def _hash_paths(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        if p.exists():
            h.update(p.read_bytes())
        # Missing files contribute nothing; their absence is encoded by the schema test.
    return h.hexdigest()
