# tiny_python — synthetic test fixture

Owned by M2; consumed by M3, M5, M8, M10. Each file contains exactly one
intentional defect (or, for `healthy.py`, none) so downstream audit
recorded-LMS responses can deterministically reference these files.

| File | Intentional defect |
|---|---|
| `main.py` | Off-by-one: `first_n(xs, n)` returns `n+1` items via `xs[:n+1]`. |
| `util.py` | Vague name `handle` whose graph-context narrowness (only normalization) makes it misleading. |
| `io_helper.py` | Bare `except Exception` swallows file IO errors silently. |
| `healthy.py` | None — exemplary `safe_read(p)` using context manager and explicit encoding. |

## Layout notes

- `.git/HEAD` exists to satisfy the walker's `.git/` precondition; the
  fixture is **not** a real git repo.
- `.gitignore` excludes `__pycache__/` and `*.pyc` only; no other
  test-specific exclusions.

## Why bytes-stable fixtures matter

M3 captures LMS responses against this exact text. Any whitespace change to
these files invalidates the recorded responses. If you must edit, regenerate
the fixtures in `tests/fixtures/lms_responses/` in the same commit.
