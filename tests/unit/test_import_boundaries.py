"""Static-AST import-boundary checks (spec §3.2).

Strict rule: ``senex/phases/*`` and ``senex/auditor.py`` MUST NOT import
from ``senex.tui``. The auditor publishes events on the event bus; the TUI
subscribes. Mixing the two seam violates the producer/consumer separation
that lets the auditor run headless.

This test runs at the AST level (no module import) so a violation is
caught even when the offending module is otherwise broken.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SENEX_ROOT = _REPO_ROOT / "senex"

# Modules / packages that MUST NOT import from ``senex.tui``.
_FORBIDDEN_TUI_IMPORT_FILES: list[Path] = [
    _SENEX_ROOT / "auditor.py",
    *(_SENEX_ROOT / "phases").rglob("*.py"),
]


def _collect_imports(path: Path) -> list[str]:
    """Return the dotted module names imported by ``path``."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.append(node.module)
    return names


@pytest.mark.parametrize(
    "candidate",
    [pytest.param(p, id=str(p.relative_to(_REPO_ROOT))) for p in _FORBIDDEN_TUI_IMPORT_FILES if p.exists()],
)
def test_phase_or_auditor_module_does_not_import_tui(candidate: Path) -> None:
    imports = _collect_imports(candidate)
    bad = [
        name for name in imports
        if name == "senex.tui" or name.startswith("senex.tui.")
    ]
    assert not bad, (
        f"{candidate.relative_to(_REPO_ROOT)} imports forbidden TUI symbols: {bad}. "
        f"Phases / auditor must publish events; TUI subscribes."
    )


def test_at_least_one_phase_module_exists() -> None:
    """Smoke check — proves the parametrized test above isn't a no-op."""
    base = _SENEX_ROOT / "phases" / "base.py"
    assert base.exists(), "senex/phases/base.py must exist (M8 Task 8.1)"
