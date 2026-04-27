"""Allow ``python -m senex`` invocation. M10 Task 10.1.3."""
from __future__ import annotations

import sys

from senex.cli import main


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
