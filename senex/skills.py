"""senex.skills — select injectable skill fragments for a file being audited."""
from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from senex.graph_awareness import GraphContext


@lru_cache(maxsize=8)
def _load_skills_cfg(skills_dir: Path) -> list[dict[str, Any]]:
    cfg_path = skills_dir / "skills.toml"
    if not cfg_path.is_file():
        return []
    with cfg_path.open("rb") as f:
        return cast(list[dict[str, Any]], tomllib.load(f).get("skills", []))


@lru_cache(maxsize=32)
def _load_skill_text(skills_dir: Path, filename: str) -> str:
    return (skills_dir / filename).read_text(encoding="utf-8")


def select_skills(
    file_path: str,
    source: str,
    awareness: GraphContext | None,
    lens_dir: Path,
) -> list[tuple[str, str]]:
    """Return ``(name, text)`` tuples for every skill whose triggers match.

    The TUI / event log uses ``name`` to surface which skills fired; the
    audit prompt is built from ``text``.
    """
    skills_dir = lens_dir / "skills"
    skills_cfg = _load_skills_cfg(skills_dir)
    selected: list[tuple[str, str]] = []
    for skill in skills_cfg:
        for trigger in skill.get("triggers", []):
            matched = False
            t = trigger["type"]
            if t == "path_pattern":
                matched = bool(re.search(trigger["pattern"], file_path))
            elif t == "content_pattern":
                matched = bool(re.search(trigger["pattern"], source))
            elif t == "graph_fanin":
                if awareness is not None and awareness.available:
                    total = sum(awareness.callers_d1_count.values())
                    matched = total >= trigger["min_callers"]
            if matched:
                selected.append(
                    (skill["name"], _load_skill_text(skills_dir, skill["file"]))
                )
                break  # one trigger match per skill is sufficient
    return selected
