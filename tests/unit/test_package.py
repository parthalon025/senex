"""Smoke tests for the senex package init."""
from __future__ import annotations

import importlib

from packaging.version import InvalidVersion, Version

import senex


def test_package_version_is_pep440_string() -> None:
    assert isinstance(senex.__version__, str)
    assert senex.__version__ != ""
    try:
        Version(senex.__version__)
    except InvalidVersion as exc:  # pragma: no cover - assertion path
        raise AssertionError(
            f"senex.__version__={senex.__version__!r} is not PEP 440 compliant"
        ) from exc


def test_package_module_has_nonempty_docstring() -> None:
    # conventions §13: every module gets a top-of-file docstring.
    assert senex.__doc__ is not None
    assert senex.__doc__.strip() != ""


def test_package_imports_clean() -> None:
    # Re-import to catch import-time side effects.
    importlib.reload(senex)
    assert hasattr(senex, "__version__")
