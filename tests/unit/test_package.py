"""Smoke tests for the senex package init."""
from __future__ import annotations

import importlib

import senex


def test_package_version_is_pep440_string() -> None:
    assert senex.__version__ == "0.1.0"


def test_package_module_has_nonempty_docstring() -> None:
    # conventions §13: every module gets a top-of-file docstring.
    assert senex.__doc__ is not None
    assert senex.__doc__.strip() != ""


def test_package_imports_clean() -> None:
    # Re-import to catch import-time side effects.
    importlib.reload(senex)
    assert hasattr(senex, "__version__")
