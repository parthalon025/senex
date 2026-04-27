"""Session-wide pytest fixtures.

Materializes filesystem-side state that cannot live inside the git tree:
the `tiny_python` fixture's `.git/HEAD` placeholder (git refuses to track
files under a `.git/` directory). This runs once at session start, before
any test imports a fixture path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TINY_PY = _REPO_ROOT / "tests" / "fixtures" / "repos" / "tiny_python"


def _ensure_tiny_python_dot_git() -> None:
    """Create `tests/fixtures/repos/tiny_python/.git/HEAD` if missing.

    Git refuses to track entries under any `.git/` directory, so the
    placeholder cannot be committed. We materialize it once per session.
    """
    git = _TINY_PY / ".git"
    git.mkdir(exist_ok=True)
    head = git / "HEAD"
    if not head.exists():
        head.write_text("ref: refs/heads/main\n", encoding="utf-8")


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_test_fixtures() -> None:
    if _TINY_PY.exists():
        _ensure_tiny_python_dot_git()
