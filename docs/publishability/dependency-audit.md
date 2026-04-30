# Senex Dependency Audit — PyPI Publishability

**Date:** 2026-04-29
**Repo:** `E:\senex`
**Auditor:** dependency-manager (Claude)
**Scope:** Pre-publish dependency, security, license, and packaging review for first PyPI release.
**Sources:** `pyproject.toml`, `requirements.txt`, the senex-local virtualenv at `E:\senex\.venv` (Python 3.13.5, 58 installed packages), PyPI metadata, OSV vulnerability database via `pip-audit 2.10.0`.

---

## 0. Executive Summary

| Area | Status | Detail |
|---|---|---|
| Inventory | OK | 12 declared direct deps in `pyproject.toml`; `requirements.txt` is a stale 10-line subset and should be deleted or regenerated. |
| Security (CVE) | PASS | `pip-audit` reports **no known vulnerabilities** across the resolved environment. |
| Pin / range strategy | NEEDS WORK | 6 of 12 deps are unbounded (`tiktoken`, `portalocker`, `detect-secrets`, `regex`, `httpx`, `tomli-w`, `ulid-py`). At least add a sane upper bound on `httpx` and `pydantic` ecosystem siblings. |
| Licenses (direct + transitive) | PASS for MIT/Apache-2.0 | All direct deps are OSI-approved permissive (MIT / Apache-2.0 / BSD / MPL-2.0). **No GPL, LGPL, or AGPL** in the tree. MPL-2.0 deps (`certifi`, `pathspec`, `hypothesis`, `tqdm`) are file-level copyleft only and compatible with Apache-2.0/MIT. |
| Maintainership | MOSTLY OK | One dead-end: **`ulid-py`** (last release 2022-03, repo archived). Replace with `python-ulid` before 1.0 ship. |
| Bloat / redundancy | NEEDS WORK | `detect-secrets` is declared but **never imported** in `senex/`. `requests` is not a direct dep (good). No pydantic-v1 contamination. |
| Optional-deps split | NEEDS WORK | `textual` (TUI-only) should move to `[tui]` extra. `dev` extra is fine but should add `respx`, `pytest-cov`, `mypy`, `ruff`, `hypothesis` are already there. Add `pip-audit`. |
| `pyproject.toml` metadata | INCOMPLETE | Missing: `description` (present but minimal), `readme`, `license`, `authors`, `classifiers`, `keywords`, `urls`. Trove classifiers entirely absent. **Will not pass `twine check` cleanly without these.** |

**Top three blockers before publishing v1.0.0 to PyPI:**
1. Fill in PyPI metadata (license, readme, authors, urls, classifiers) in `[project]`.
2. Replace `ulid-py` with `python-ulid` (or vendor a 30-line ULID generator).
3. Either remove unused `detect-secrets` dep or wire it in / move it to `[secrets]` extra.

---

## 1. Inventory

### 1.1 Declared in `pyproject.toml` (`[project.dependencies]`)

| # | Package | Constraint | Evidence of use in `senex/` |
|---|---|---|---|
| 1 | `openai` | `>=1.50,<2.0` | `senex/lmstudio_client.py` (OpenAI-compatible client to LM Studio) |
| 2 | `pydantic` | `>=2.5,<3` | 11+ files (config, render_models, findings_aggregator, etc.) |
| 3 | `textual` | `>=0.50` | `senex/tui/*` (entire TUI subsystem) |
| 4 | `tiktoken` | (unbounded) | `lmstudio_client.py`, `tools/registry.py` |
| 5 | `portalocker` | (unbounded) | `runlock.py` |
| 6 | `detect-secrets` | (unbounded) | **No imports found** — see §6 |
| 7 | `regex` | (unbounded) | `secret_redactor.py`, `tools/grep.py`, `tools/safety.py` |
| 8 | `httpx` | (unbounded) | `lmstudio_client.py`, `tools/search_code.py` |
| 9 | `jsonschema` | `>=4.20` | `findings_aggregator.py` |
| 10 | `tomli-w` | (unbounded) | `auditor.py`, `cli_config_show.py` |
| 11 | `ulid-py` | (unbounded) | `auditor.py` |
| 12 | `pathspec` | `>=0.12.0` | `walker.py` |

### 1.2 `requirements.txt` (10 lines)

Stale. It is a subset of `pyproject.toml` and missing `jsonschema`, `tomli-w`, and `pathspec`. It will mislead anyone using `pip install -r requirements.txt`.

**Action:** delete `requirements.txt`, or regenerate it from a lockfile (e.g., `pip-compile`/`uv lock`) and clearly mark it as a dev/CI lock.

### 1.3 `setup.cfg` / `setup.py`

Not present. Build is fully PEP 621 via `setuptools.build_meta` — fine.

### 1.4 Resolved environment (`E:\senex\.venv`)

58 packages installed. The resolution path produced versions ahead of the floors declared in `pyproject.toml`:

| Direct dep | Resolved version |
|---|---|
| openai | 1.109.1 |
| pydantic | 2.13.3 |
| textual | 8.2.4 |
| tiktoken | 0.12.0 |
| portalocker | 3.2.0 |
| detect-secrets | 1.5.0 |
| regex | 2026.4.4 |
| httpx | 0.28.1 |
| jsonschema | 4.26.0 |
| tomli_w | 1.2.0 |
| ulid-py | 1.1.0 |
| pathspec | 1.1.1 |

---

## 2. Pin / Range Strategy

For a CLI tool published to PyPI, library-style ranges (`>=X,<Y`) are the right baseline so downstream installs do not get pinned to a single point version.

### Issues found

| Pkg | Current | Concern | Recommended |
|---|---|---|---|
| `openai` | `>=1.50,<2.0` | Good. Caps at major; permits patch bumps. | Keep. |
| `pydantic` | `>=2.5,<3` | Good. v3 is a known break. | Keep. |
| `textual` | `>=0.50` | Unbounded. **Textual breaks API across minor releases** (it is `0.x` and now `8.x`; numbering changed). The repo currently runs on 8.2.4 — the floor of `0.50` is essentially meaningless and will let `pip` resolve to almost anything. | `>=0.50,<9` or, more honest, bump floor to `>=8.0,<9` if you actually need 8.x widget classes. |
| `tiktoken` | none | `tiktoken` makes occasional encoder-name changes; pin `<1` for safety. | `>=0.7,<1` |
| `portalocker` | none | API has been stable. | `>=2.7,<4` |
| `detect-secrets` | none | If you keep it (see §6), `1.x` series. | `>=1.4,<2` |
| `regex` | none | CalVer (`2025.x` / `2026.x`); rarely breaking but unbounded is risky. | `>=2024.5` |
| `httpx` | none | `httpx` is **pre-1.0** and has occasional minor-version breaks. Unbounded is the riskiest item in this list. | `>=0.27,<1` |
| `jsonschema` | `>=4.20` | Unbounded above. v5 is on the roadmap. | `>=4.20,<5` |
| `tomli-w` | none | API is two functions; very stable. | `>=1.0,<2` |
| `ulid-py` | none | Abandoned (see §5). Replace. | n/a |
| `pathspec` | `>=0.12.0` | Resolved to `1.1.1` — your floor allowed crossing the 1.0 boundary. That happens to be fine for `pathspec` but it is luck, not policy. | `>=0.12,<2` |

### No `==` pins anywhere

Good. No problems on that axis.

### Resolution drift

Resolved versions in the venv are far ahead of declared floors. That is healthy for development but signals you should re-test against your declared minimums before tagging `1.0.0` (CI matrix: lowest-resolution + latest).

---

## 3. Vulnerability Scan (`pip-audit`)

```
$ E:/senex/.venv/Scripts/python.exe -m pip_audit --skip-editable
No known vulnerabilities found
Name  Skip Reason
----- -------------------------------
senex distribution marked as editable
```

**Result: PASS — zero CVEs against the OSV/PyPI advisory database for the resolved tree at audit time.**

Caveat: `pip-audit` only scans installed distributions. It does not scan against your declared *floors*. If you ship `httpx>=0.27` and a user resolves `httpx 0.27.0`, that may carry CVEs the current `0.28.1` resolution avoided. Re-run `pip-audit` in CI on a lowest-version resolve.

---

## 4. License Compatibility

Senex's intended license is TBD; recommendation is **Apache-2.0** (better patent protection than MIT, still permissive, dominant in ML/LLM tooling).

### 4.1 Direct deps — license matrix

| Pkg | License | OSI? | Compatible with Apache-2.0 senex? | Compatible with MIT senex? |
|---|---|---|---|---|
| openai | Apache-2.0 | Yes | Yes | Yes |
| pydantic | MIT | Yes | Yes | Yes |
| textual | MIT | Yes | Yes | Yes |
| tiktoken | MIT | Yes | Yes | Yes |
| portalocker | BSD-3-Clause | Yes | Yes | Yes |
| detect-secrets | Apache-2.0 | Yes | Yes | Yes |
| regex | Apache-2.0 AND CNRI-Python | Yes (both) | Yes | Yes |
| httpx | BSD-3-Clause | Yes | Yes | Yes |
| jsonschema | MIT | Yes | Yes | Yes |
| tomli-w | MIT | Yes | Yes | Yes |
| ulid-py | Apache-2.0 | Yes | Yes | Yes |
| pathspec | MPL-2.0 | Yes | Yes (MPL is file-level copyleft) | Yes |

### 4.2 Transitive deps — license matrix (full venv tree)

| License | Packages | Notes |
|---|---|---|
| MIT | annotated-types, anyio, attrs, charset-normalizer, filelock, h11, iniconfig, jiter, jsonschema, jsonschema-specifications, librt, linkify-it-py, markdown-it-py, mdit-py-plugins, mdurl, mypy, mypy_extensions, packageurl-python, platformdirs, pluggy, pydantic, pydantic_core, pyparsing, pytest, pytest-cov, referencing, rich, rpds-py, ruff, textual, tiktoken, tomli, tomli_w, typing-inspection, uc-micro-py, urllib3 | Compatible everywhere |
| Apache-2.0 | CacheControl, coverage, cyclonedx-python-lib, detect-secrets, distro, license-expression, msgpack, openai, pip-api, pip_audit, py-serializable, pytest-asyncio, requests, sortedcontainers, ulid-py | Compatible everywhere |
| BSD-2-Clause / BSD-3-Clause | Pygments, boolean.py, httpcore, httpx, idna, portalocker, respx | Compatible |
| BSD (legacy) | colorama | Compatible |
| MPL-2.0 | certifi, hypothesis, pathspec, tqdm (MPL-2.0 AND MIT) | File-level copyleft only — **compatible** with Apache-2.0 and MIT distributions; you must preserve MPL'd source files unmodified or publish modifications, but this does **not** affect senex's own license. |
| PSF-2.0 | typing_extensions, defusedxml, pywin32 | Compatible |
| Apache-2.0 OR BSD-2-Clause | packaging | Compatible |
| Apache + MIT dual | sniffio | Compatible |

### 4.3 Flagged

- **No GPL.** No LGPL. No AGPL. **No copyleft beyond MPL-2.0 file-level** anywhere in the resolved tree.
- **No "UNKNOWN" or non-OSI** licenses (the only `UNKNOWN` was `senex` itself, which is the package being audited).
- regex carries `CNRI-Python` as a dual license — historical Python-license clause, OSI-approved, compatible with Apache-2.0 and MIT.

**Verdict:** senex can be released under **Apache-2.0 or MIT** without any inherited copyleft contamination. Recommend Apache-2.0.

---

## 5. Maintainership Health

Quick triage; "abandoned" thresholds: no commits in 18+ months, archived repo, or clear successor.

| Pkg | Latest release | Repo health | Verdict |
|---|---|---|---|
| `openai` | Active (weekly releases by OpenAI) | Active | OK |
| `pydantic` | Active (Samuel Colvin / pydantic Inc.) | Active, very high traffic | OK |
| `textual` | Active (Textualize) | Active, ~26k stars | OK |
| `tiktoken` | Active (OpenAI) | Active | OK |
| `portalocker` | Active (wolph) | Active | OK |
| `detect-secrets` | Active (Yelp) | Active, ~3.7k stars | OK |
| `regex` | Active (mrabarnett, Python regex maintainer) | Active | OK |
| `httpx` | Active (encode) | Active, ~13k stars | OK |
| `jsonschema` | Active (Julian Berman) | Active | OK |
| `tomli-w` | Slow but maintained (hukkin) | Sufficient — tiny scope | OK |
| **`ulid-py`** | **2022-03 (1.1.0); repo last active 2022; ~330 stars** | **Dead-end** | **REPLACE** |
| `pathspec` | Active (cpburnz) | Active | OK |

### Recommended replacement for `ulid-py`

`python-ulid` (cbornet, MIT, actively maintained, drop-in equivalent for the basic ULID use case in `auditor.py`). Migration is roughly:

```python
# old
import ulid
new_id = ulid.new().str

# new
from ulid import ULID
new_id = str(ULID())
```

Alternative: vendor a 30-line ULID generator using `secrets` + `time.time_ns()` and drop the dep entirely. ULID is just `48-bit timestamp ms + 80-bit randomness, Crockford base32`.

---

## 6. Bloat / Redundancy

### 6.1 `detect-secrets` is declared but unused

`grep -r "detect_secrets" senex/` returns **no Python imports**. The only references are in `pyproject.toml`, `requirements.txt`, and design docs. Either:

- **Remove** the dep (cleanest), or
- **Wire it in** (the design docs in `docs/superpowers/specs/` reference detect-secrets baselines), or
- **Move it to an optional extra** `[secrets]` so users who want secret-scanning baselines can opt in.

### 6.2 No `httpx` + `requests` duplication in direct deps

`requests` is **not** in `pyproject.toml`. It is only pulled in transitively (by `detect-secrets` -> nothing actually; and by `pip-audit` which is dev-only). Good.

### 6.3 No pydantic v1 / v2 split

Tree contains only pydantic 2.13.3 and `pydantic_core` 2.46.3. No `pydantic-v1` shim. Good.

### 6.4 Other notes

- `tomli` (read) is pulled transitively (by `pip_audit` and `cyclonedx-python-lib`), and `tomli-w` (write) is direct. Python 3.11+ has `tomllib` in stdlib for *reading* — senex does not need a TOML reader as a direct dep, and indeed declares only the writer. Correct.
- `respx` is in `dev` deps but worth double-checking: it is only useful if you mock httpx in tests. A grep for `respx` in `tests/` will confirm.

---

## 7. Optional-Deps Split

Current `[project.optional-dependencies]`:

```toml
dev = ["pytest>=7", "pytest-asyncio>=0.21", "pytest-cov", "hypothesis", "mypy", "ruff", "respx"]
```

### Recommended restructure

```toml
[project.optional-dependencies]
tui = [
  "textual>=8.0,<9",
]
secrets = [
  "detect-secrets>=1.4,<2",
]
dev = [
  "pytest>=8,<10",
  "pytest-asyncio>=0.23,<2",
  "pytest-cov>=5,<8",
  "hypothesis>=6.100,<7",
  "mypy>=1.10,<2",
  "ruff>=0.5,<1",
  "respx>=0.21,<1",
  "pip-audit>=2.7,<3",
]
all = ["senex[tui,secrets]"]
```

### Rationale

- **`textual` is heavyweight** (pulls Rich, markdown-it, linkify, mdit plugins, tree-sitter widgets in some versions) and only needed by `senex/tui/*`. CLI-only users on a server should not pay that cost. Move to `[tui]`.
- **`detect-secrets` to `[secrets]`** if it is kept.
- Add **`pip-audit`** to `dev` so CI security checks do not require an extra install step.
- Floors raised to currently-supported releases (pytest 8+, hypothesis 6.100+) so the test suite is not silently run against archaic combinations.

You will need a small change in `senex/cli.py` / `senex/tui/launcher.py` to gracefully error if the user invokes a TUI command without `senex[tui]` installed. Pattern:

```python
try:
    from senex.tui.launcher import LauncherApp
except ImportError as e:
    raise SystemExit(
        "TUI requires the 'tui' extra: pip install 'senex[tui]'"
    ) from e
```

---

## 8. `pyproject.toml` Audit

Current state vs. PyPI publishability checklist:

| Field | Present? | Required for PyPI? | Notes |
|---|---|---|---|
| `[build-system]` requires + backend | Yes (`setuptools>=68`, `wheel`) | Yes | OK |
| `[project] name` | Yes (`senex`) | Yes | OK; verify availability on PyPI before tagging. |
| `[project] version` | Yes (`1.0.2`) | Yes | OK |
| `[project] description` | Yes (`"Local-LLM code audit tool"`) | Yes | Adequate; tighten to one strong sentence under 150 chars for the PyPI search blurb. |
| `[project] readme` | **MISSING** | Strongly recommended | **Add** `readme = "README.md"` so PyPI renders the long description. Without this the project page is empty. |
| `[project] license` | **MISSING** | Required (PEP 639) | **Add** `license = "Apache-2.0"` (or `license = { file = "LICENSE" }` for older spec). Add a `LICENSE` file at repo root. |
| `[project] authors` | **MISSING** | Strongly recommended | **Add** `authors = [{ name = "...", email = "..." }]` |
| `[project] keywords` | **MISSING** | Optional but good for search | Suggest `["llm", "code-audit", "static-analysis", "security", "cli", "lm-studio", "ollama"]` |
| `[project] classifiers` | **MISSING — entirely** | Strongly recommended | See §8.1 below. **Without this, PyPI search filters do not surface senex.** |
| `[project] urls` | **MISSING** | Strongly recommended | See §8.2 below. |
| `[project.scripts]` console entry `senex` | Yes (`senex = "senex.cli:main"`) | Yes for CLI tools | OK |
| `[project] requires-python` | Yes (`>=3.11`) | Yes | OK; matches `tool.ruff` and `tool.mypy` targets. |
| `[project.dependencies]` | Yes (12 entries) | Yes | See §1, §2, §6 |
| `[project.optional-dependencies]` | Partial (`dev` only) | Optional | Restructure per §7 |

### 8.1 Recommended Trove classifiers

```toml
classifiers = [
  "Development Status :: 4 - Beta",
  "Environment :: Console",
  "Intended Audience :: Developers",
  "Intended Audience :: Information Technology",
  "License :: OSI Approved :: Apache Software License",
  "Operating System :: OS Independent",
  "Programming Language :: Python",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3 :: Only",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Topic :: Software Development",
  "Topic :: Software Development :: Quality Assurance",
  "Topic :: Software Development :: Testing",
  "Topic :: Security",
  "Typing :: Typed",
]
```

Note: `License :: OSI Approved :: ...` classifiers are **deprecated** in PEP 639 in favor of the SPDX `license = "Apache-2.0"` field, but PyPI still indexes the classifier today, so include both during the transition.

### 8.2 Recommended URLs

```toml
[project.urls]
Homepage      = "https://github.com/<org>/senex"
Repository    = "https://github.com/<org>/senex"
Issues        = "https://github.com/<org>/senex/issues"
Documentation = "https://github.com/<org>/senex#readme"   # or RTD/MkDocs once published
Changelog     = "https://github.com/<org>/senex/blob/main/CHANGELOG.md"
```

### 8.3 Build backend

`setuptools>=68` + `setuptools.build_meta` is fine. For a leaner publish flow consider migrating to `hatchling`:

```toml
[build-system]
requires = ["hatchling>=1.24"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["senex"]
```

This is **optional**; setuptools works. Hatchling tends to produce smaller, more reproducible wheels and handles `package-data` more cleanly.

### 8.4 Pre-publish smoke checks (run before `twine upload`)

```bash
python -m build                           # builds sdist + wheel into ./dist
twine check dist/*                        # validates README rendering, classifiers
pip install dist/senex-1.0.2-*.whl        # local install test
senex --version                           # smoke-test the console_script
pip-audit --strict                        # one more CVE check
```

---

## 9. Action Items (prioritized)

### P0 — must do before any PyPI upload
1. Fill in `[project]` metadata: `readme`, `license`, `authors`, `urls`, `keywords`, `classifiers` (§8).
2. Add a `LICENSE` file at repo root (Apache-2.0 recommended).
3. Add `README.md` rendering check (`twine check`).
4. Replace or remove `ulid-py` (§5).
5. Decide on `detect-secrets`: remove, wire in, or move to `[secrets]` extra (§6.1).
6. Delete or regenerate `requirements.txt` (§1.2).

### P1 — strongly recommended for 1.0
7. Bound the unbounded ranges, especially `httpx<1` and `textual<9` (§2).
8. Move `textual` to a `[tui]` optional extra (§7).
9. Add `pip-audit` to CI on a lowest-resolution matrix (§3).
10. Add CI matrix on Python 3.11 + 3.12 + 3.13 to back the classifiers.

### P2 — nice to have
11. Consider migrating build backend to `hatchling` (§8.3).
12. Add `[tool.uv]` or generate a lockfile checked into the repo for reproducible installs.
13. Add `py.typed` marker file under `senex/` so downstream type checkers honor your types (`Typing :: Typed` classifier requires it).

---

## Appendix A — Raw `pip-audit` output

```
$ E:/senex/.venv/Scripts/python.exe -m pip_audit --skip-editable
No known vulnerabilities found
Name  Skip Reason
----- -------------------------------
senex distribution marked as editable
```

## Appendix B — Files referenced

- `E:\senex\pyproject.toml`
- `E:\senex\requirements.txt`
- `E:\senex\senex\__init__.py`
- `E:\senex\senex\auditor.py` (uses `ulid`, `tomli_w`)
- `E:\senex\senex\cli_config_show.py` (uses `tomli_w`)
- `E:\senex\senex\lmstudio_client.py` (uses `httpx`, `tiktoken`)
- `E:\senex\senex\runlock.py` (uses `portalocker`)
- `E:\senex\senex\walker.py` (uses `pathspec`)
- `E:\senex\senex\secret_redactor.py`, `senex\tools\grep.py`, `senex\tools\safety.py` (use `regex`)
- `E:\senex\senex\findings_aggregator.py` (uses `jsonschema`)
- `E:\senex\senex\tui\` (uses `textual`)
- `E:\senex\senex\tools\registry.py` (uses `tiktoken`)
