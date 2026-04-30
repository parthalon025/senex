# Python Idiom Review — `senex` (PyPI Pre-Release)

**Repo**: `E:\senex` &nbsp;|&nbsp; **Package**: `senex` v1.0.2 &nbsp;|&nbsp; **Target**: Python 3.11+
**Reviewer**: Read-only audit (no code modifications)
**Date**: 2026-04-29
**Tooling**: mypy 1.18.2 (strict), ruff 0.15.12, Python 3.13.5
**Scope**: `senex/` package, 78 source files, ~17.8k lines (incl. blanks/comments)

---

## TL;DR

Senex is a **mature, idiomatic, async-first Python 3.11+ codebase** that is very close to PyPI-publishable from an idiom standpoint. The architecture is consistently Pythonic: pydantic v2 with `extra="forbid"` everywhere, `from __future__ import annotations` on every source file but one, disciplined `asyncio.to_thread` for blocking I/O, named exceptions with `error_kind` discriminators, no `print()` in library code, and uniform `log = logging.getLogger(__name__)` everywhere.

**Blockers for publish**: 0. Nothing in this review is release-blocking.

**Highly recommended fixes** (small, safe):
1. **mypy strict fails with 10 errors in 6 files** — all real, easy to fix.
2. **`openai>=1.50,<2.0` is a dead dependency** — not imported anywhere. Drop from both `pyproject.toml` and `requirements.txt`.
3. **`requirements.txt` is missing `pathspec`** that `pyproject.toml` declares — files have drifted.
4. **48 exception classes lack the `Error` suffix** (PEP 8 N818) — semver-major rename if you wait.

**Polish items** (cosmetic):
- 21 `datetime.timezone.utc` → `datetime.UTC` (3.11+ alias).
- 53 forward-ref string quotes that are redundant under `from __future__ import annotations` (UP037).
- 14 files with un-sorted import blocks (I001 trivially auto-fixable).
- 16 sites should use `log.exception(...)` instead of `log.error(...)` inside `except` blocks (TRY400).

The project's current `[tool.ruff]` config is essentially unconfigured (just `line-length` and `target-version`). All the `# noqa: BLE001` annotations the codebase carries are *documentary intent markers* — they don't actually suppress anything because BLE001 isn't selected. That's a valid choice but you should be aware of it before publishing.

---

## 1. Type Safety

### 1.1 mypy strict — **FAILS with 10 errors in 6 files**

```
$ python -m mypy senex/
senex\lmstudio_lifecycle.py:591: error: Cannot find implementation or library stub
                                          for module named "lmstudio"  [import-not-found]
senex\lmstudio_client.py:1181:    error: Returning Any from function declared to
                                          return "dict[str, object]"  [no-any-return]
senex\tools\list_symbols.py:188:  error: Unused "type: ignore" comment  [unused-ignore]
senex\tools\list_symbols.py:207:  error: Unused "type: ignore" comment  [unused-ignore]
senex\tools\list_symbols.py:210:  error: Unused "type: ignore" comment  [unused-ignore]
senex\tools\gitnexus_context.py:79:  error: Missing type parameters for generic
                                            type "dict"  [type-arg]
senex\tools\gitnexus_context.py:80:  error: Missing type parameters for generic
                                            type "dict"  [type-arg]
senex\skills.py:13:               error: Missing type parameters for generic type
                                          "dict"  [type-arg]
senex\skills.py:18:               error: Returning Any from function declared to
                                          return "list[dict[Any, Any]]"  [no-any-return]
senex\auditor.py:638:             error: Function is missing a return type
                                          annotation  [no-untyped-def]
Found 10 errors in 6 files (checked 78 source files)
```

**Fix-by-fix**:

| Site | Fix |
|------|-----|
| `lmstudio_lifecycle.py:591` | `import lmstudio  # type: ignore[import-not-found]` (it's an optional vendor SDK; mypy strict has no stubs). |
| `lmstudio_client.py:1181` | `return cast(dict[str, object], _walk(...))` or change return type to `Any`. |
| `tools/list_symbols.py:188,207,210` | Remove the three stale `# type: ignore[override]` — modern mypy handles `ast.NodeVisitor.visit_*` correctly. |
| `tools/gitnexus_context.py:79,80` | `incoming: dict[str, object] = ...` (matching the rest of the file's style). |
| `skills.py:13` | `def _load_skills_cfg(skills_dir: Path) -> list[dict[str, object]]:` and adjust line 18 return type to match. |
| `auditor.py:638` | `def _disk_writer_dispatch(sub: DiskWriterSubscriber) -> Callable[[BaseEvent], Awaitable[None]]:` — only function in the file without an annotation. |

After these fixes, mypy strict will pass cleanly. None require behavior changes.

### 1.2 ruff (current config) — **Passes**

```
$ python -m ruff check senex/
All checks passed!
```

The active config is essentially defaults (`E`/`F` only). Strong recommendation: enable a richer rule set before publish (see §11).

### 1.3 Type-system discipline

- 29 `# type: ignore` comments across 20 files — moderate, mostly legitimate (vendor SDK shims, `ast.NodeVisitor` overrides).
- `Final` is used appropriately (16 sites, mostly in `lmstudio_client.py` for compiled regex literals).
- `Protocol` + `@runtime_checkable` for `LLMClient` and `LifecycleBackend` — exemplary structural typing.
- `Annotated[..., Field(...)]` is only used **once** (`compaction.py:94`); fine as-is given v2 lets you put `max_length=` directly on `Field`. Not worth changing.

### 1.4 pyright strict — not run

You requested `pyright` strict. Pyright was not available in the environment used for this review; mypy 1.18 strict was used as a proxy. Recommend running `pyright --strict senex/` once before tagging the release; pyright tends to flag a few cases mypy misses (especially around `Awaitable` narrowing).

---

## 2. Async Patterns

### 2.1 `asyncio.run` inside async paths — **None**

All 13 `asyncio.run()` call sites are at sync CLI boundaries:

```
senex/cli_audit.py:237        # _bring_up_sglang() helper, called from sync main
senex/cli_audit.py:385        # _run_headless(), sync entrypoint
senex/lifecycle_cli.py:231,233  # sync CLI dispatch
senex/cli_aggregate.py:111    # sync CLI dispatch
senex/cli_wizard.py:307       # sync wizard probe
senex/cli_doctor.py:213       # sync CLI dispatch
senex/cli_handlers.py:53,91,98  # sync CLI dispatch
```

No deadlock risk found. Clean.

### 2.2 Blocking sync I/O in async functions — **3 sites flagged**

`ruff --select ASYNC` reports:

```
senex/findings_aggregator.py:74    if not audit_dir.exists():     # ASYNC240
senex/phases/file_audit.py:312     source = file.read_text(...)   # ASYNC240
senex/tui/launcher.py:265          root = Path(ui.scan_root)...    # ASYNC240 (constructor — false positive risk)
senex/tui/launcher.py:310          if not repo_path.exists():     # ASYNC240
```

**Analysis**:
- `findings_aggregator.py:74` — single `Path.exists()` check on the audit dir at the start of an async aggregation method; this returns immediately and is not a hot path. Acceptable but trivially fixable with `await asyncio.to_thread(audit_dir.exists)`.
- `phases/file_audit.py:312` — `file.read_text(...)` for the source file being audited. **This is a real blocking read on every file** in the file_audit phase. The auditor itself is gated by an LLM call that takes seconds, so the practical impact is small, but on principle this should be `await asyncio.to_thread(file.read_text, encoding="utf-8")`. (Disk-writer subscribers already use `to_thread` correctly — see `subscribers/disk_writer.py:62`. The pattern exists; just apply it here.)
- `tui/launcher.py` — TUI thread is single-event-loop and Textual tolerates short stalls. Inline `Path.exists()` checks are fine; `_discover_repos_on_disk` is intentionally inline because the comment at line 268-271 documents the trade-off.

The codebase is **disciplined elsewhere**: 6 files use `asyncio.to_thread` consistently for blocking work (`disk_writer`, `headless_subscriber`, `tools/grep`, `tools/list_dir`, `tools/list_symbols`, `tools/read_file`). This is the dominant pattern; the four sites above are the exceptions.

### 2.3 Missing `await` on coroutine — **None found**

mypy strict reported no `Coroutine[...] is not Awaited` errors. ruff `RUF006` (unawaited `create_task` reference) passes clean.

### 2.4 Drop-and-forget `asyncio.create_task` — **None found**

There is exactly **one** `asyncio.create_task` call in the package:

```python
# senex/subscribers/tui_subscriber.py:69
self._pump_task = asyncio.create_task(self._pump())
```

The reference is held on `self._pump_task` (declared at line 57 with type `asyncio.Task[None] | None`) and is later awaited in `shutdown()`. Correct.

### 2.5 ASYNC109 (timeout-as-parameter) — **7 sites**

All in `lmstudio_lifecycle.py` (e.g., `async def load(self, model_id: str, timeout: int)` on the `LifecycleBackend` Protocol). ruff prefers using `asyncio.wait_for(coro, timeout=...)` at the call site instead. This is a stylistic preference — the current design propagates `timeout` through to vendor SDK / subprocess calls that handle it natively. **Keep as-is**; it's a defensible Protocol design.

---

## 3. Pydantic v2 Idioms

**This is the strongest area of the codebase.** Senex is a textbook pydantic-v2 implementation.

| Check | Result |
|-------|--------|
| `class Config:` (v1 style) | **0 occurrences** |
| `model_config = ConfigDict(...)` | Used throughout (≥ 35 classes) |
| v1 `@validator` | **0 occurrences** |
| v2 `@field_validator` / `@model_validator` | 7 occurrences across `events.py`, `tools/grep.py`, `tools/read_file.py`, `tools/run_semgrep.py`, `tools/gitnexus_context.py` |
| v1 `parse_obj(...)` | **0 occurrences** |
| v1 `.dict()` | **0 occurrences** (the `.json()` calls in the grep are all `httpx.Response.json()`) |
| v2 `model_validate(...)` / `model_dump(...)` | Used everywhere |
| `Annotated[..., Field(...)]` | 1 occurrence (acceptable; v2 supports `Field(max_length=...)` inline) |

The `_StrictModel(BaseModel)` base with `model_config = ConfigDict(extra="forbid")` is defined locally in three files (`render_models.py`, `events.py`, `config.py`). **Recommendation**: lift this to a single module (e.g., `senex/_models.py`) and import. Three definitions of the same conceptual base class is mild duplication, not a defect.

`config.py:21` adds `validate_assignment=True` on its `_StrictModel` — a sensible stricter variant. Document the difference (or merge into one base with a flag).

---

## 4. Modern Python Features

### 4.1 `tomllib` (3.11+) — **Used everywhere**

```
senex/config.py:8          import tomllib
senex/skills.py:5          import tomllib
senex/lens.py:13           import tomllib
```

No `import toml` or `import tomli`. Clean.

### 4.2 Pattern matching (`match`) — **1 use; ~10 latent opportunities**

The one in-place use is exemplary:

```python
# senex/subscribers/metrics.py:58
async def consume(self, event: BaseEvent) -> None:
    match event:
        case FileComplete():     self._on_file_complete(event)
        case ToolCall():         self._metrics.tool_calls += 1
        case CompactionComplete(): self._metrics.compactions += 1
        case ThinkingComplete(): self._metrics.total_thinking_seconds += event.latency_ms / 1000.0
        case OutputComplete():   self._metrics.total_output_seconds += event.latency_ms / 1000.0
        case _:                  return
```

**Latent opportunities** (clean refactors, not bugs):

- `senex/cli_config_show.py:123-130` — `_redact_strings_recursive(node)` with three `if isinstance(node, T)` branches → ideal `match node` candidate.
- `senex/cli_config_show.py:145-150` — `_strip_none(node)` mirror-image of above; same refactor.
- `senex/cross_cutting.py:188-194` — chained `isinstance` on parsed JSON shapes.
- `senex/auditor.py:695-697` — recursive scrub helper with `dict`/`list` branches.

These are very mild quality-of-life wins, not idiom defects.

### 4.3 `Self` (3.11+) return annotations — **0 uses**

`from typing import Self` is not used anywhere. The codebase has very few builder/cloner patterns where `Self` would shine; the most natural candidate is the `model_validator(mode="after")` returning `BaseEvent` in `events.py:35`:

```python
def _validate_type_discriminator(self) -> BaseEvent:  # could be -> Self
```

Marginal benefit. Not worth a focused pass.

### 4.4 `TypeAlias` — **0 uses**

There are no complex aliases in the codebase that demand the explicit `TypeAlias` marker; pep 695 `type` statements (3.12+) would be cleaner anyway, but you target 3.11. Skip.

### 4.5 `datetime.UTC` (3.11+) — **21 sites still on `timezone.utc`**

ruff flags 21 occurrences of `datetime.timezone.utc` that should be `datetime.UTC`. Scattered across `auditor.py`, `checkpoint.py`, `compaction.py`, `cross_cutting.py`, `graph_awareness.py`, `lmstudio_client.py`, `lmstudio_lifecycle.py` (the heaviest at 7 sites), `phases/aggregate.py`. All trivially auto-fixable with `ruff check --fix --select UP017`.

### 4.6 `TimeoutError` (3.11+) — **10 sites still on `asyncio.TimeoutError`**

PEP 678 unified them. ruff `UP041` flags 10 sites in `graph_awareness.py` and `lmstudio_lifecycle.py`. Auto-fixable.

### 4.7 `from __future__ import annotations` redundant quotes — **53 sites (UP037)**

Quoted forward refs like `def _now() -> "datetime":` are unnecessary because `from __future__ import annotations` already defers evaluation. 53 occurrences, all auto-fixable.

---

## 5. Dead Code (post LM Studio → SGLang Migration)

### 5.1 **`openai>=1.50,<2.0` is a dead dependency**

Confirmed by exhaustive search:

```
$ grep -rE '^(import openai|from openai)' senex/ tests/
(no matches)
```

The strings `openai_tools` / `openai_tools_for` are method names referencing the *OpenAI tool-call wire format*, not the `openai` Python SDK. The codebase calls LM Studio directly via `httpx.AsyncClient`. **Drop the dependency.**

Cost-benefit: removing it shaves a non-trivial transitive dep tree (openai pulls jiter, anyio, distro, sniffio, tqdm, pydantic-already-have). High value, zero risk.

### 5.2 `requirements.txt` ↔ `pyproject.toml` drift

```diff
  pyproject.toml deps                requirements.txt
  ─────────────────                  ────────────────
  openai>=1.50,<2.0                  openai>=1.50,<2.0    ← drop both
  pydantic>=2.5,<3                   pydantic>=2.5,<3
  textual>=0.50                      textual>=0.50
  tiktoken                           tiktoken
  portalocker                        portalocker
  detect-secrets                     detect-secrets
  regex                              regex
  httpx                              httpx
  jsonschema>=4.20                   jsonschema>=4.20
  tomli-w                            tomli-w
  ulid-py                            ulid-py
+ pathspec>=0.12.0                   ── (missing in requirements.txt)
```

`pathspec` is in `pyproject.toml` but not `requirements.txt`. `pathspec` is imported in real code paths (used in walker / ignore-handling). For a published package this doesn't matter (sdist/wheel use `pyproject.toml`), but for contributors using `pip install -r requirements.txt` they'll get an import error. **Fix or delete `requirements.txt`** — for a PyPI package, `pyproject.toml` is canonical.

### 5.3 LM Studio era code — still live, not dead

- `senex/lmstudio_client.py` — actively imported from `cli_audit`, `cli_wizard`, `tui/launcher`, `auditor`, `phases/preflight`, `phases/file_audit`, `cli_doctor`. **Live.**
- `senex/lmstudio_lifecycle.py` — multi-backend lifecycle manager (SDK / CLI / HTTP). The `LMStudioSDKBackend` path tries `import lmstudio` and is an optional fallback when the vendor SDK is installed. **Live.**
- `senex/lmstudio_errors.py` — exception hierarchy used widely. **Live.**

The naming is misleading post-migration ("LMStudio" sounds vendor-specific, but the implementation is OpenAI-compatible HTTP and works for SGLang too). Consider renaming the module triplet to `senex/llm_*` in a future minor — this is **not** a v1 blocker.

### 5.4 Other potential dead spots checked, none found

- No unused public symbols in `__all__` lists.
- No vendored shims left over from the migration.
- One TODO marker (`lmstudio_lifecycle.py:652` — "TODO(M10)…re-export"); already obsolete given M10 shipped.

---

## 6. Naming and Conventions

### 6.1 Exception class `Error` suffix — **48 violations (PEP 8 / N818)**

Per PEP 8: "If a module defines a single exception raised on errors common in any of its functions, it is conventionally called `error` or `Error`." ruff's `N818` enforces this consistently.

Senex names exceptions after **failure modes** rather than the `*Error` convention:

```
LMSConnectionLost          → LMSConnectionLostError
LMSResponseSchemaInvalid   → LMSResponseSchemaInvalidError
TokenBudgetExceeded        → TokenBudgetExceededError
SchemaNegotiationFailed    → SchemaNegotiationFailedError
FingerprintChanged         → FingerprintChangedError
ThinkingTokensExceeded     → ThinkingTokensExceededError
ContextOverflow            → ContextOverflowError
CheckpointCorrupt          → CheckpointCorruptError
WizardCancelled            → WizardCancelledError
CompactionFailed           → CompactionFailedError
CompactionLoopExceeded     → CompactionLoopExceededError
UnknownConfigKey           → UnknownConfigKeyError
UnknownEventType           → UnknownEventTypeError
ValidationGateFailed       → ValidationGateFailedError
FindingsValidationFailed   → FindingsValidationFailedError
AggregatorInputMissing     → AggregatorInputMissingError
GitNexusUnavailable        → GitNexusUnavailableError
GitNexusSubprocessFailed   → GitNexusSubprocessFailedError
RelpathRejected            → RelpathRejectedError
HandoffPolicyViolation     → HandoffPolicyViolationError
LensNotFound               → LensNotFoundError
LifecycleBackendUnavailable→ LifecycleBackendUnavailableError
ModelLoadTimeout           → ModelLoadTimeoutError
ModelLoadFailed            → ModelLoadFailedError
ModelNotLoaded             → ModelNotLoadedError
InvalidModelId             → InvalidModelIdError
FingerprintMismatch        → FingerprintMismatchError
ResumedRunCannotOwnLoad    → ResumedRunCannotOwnLoadError
PreflightFailure           → PreflightFailureError
PhaseAborted               → PhaseAbortedError
ResumeIncompatible         → ResumeIncompatibleError
RenderFatal                → RenderFatalError
AggregateFailed            → AggregateFailedError
PromptTemplateUnsubstituted→ PromptTemplateUnsubstitutedError
RunLockCorrupt             → RunLockCorruptError
SubscriberQueueFull        → SubscriberQueueFullError
ToolInputInvalid           → ToolInputInvalidError
PathOutsideRepo            → PathOutsideRepoError
SymlinkRefused             → SymlinkRefusedError
RegexTooComplex            → RegexTooComplexError
RegexTimeoutExceeded       → RegexTimeoutExceededError
ToolDispatchFailed         → ToolDispatchFailedError
ToolUnavailable            → ToolUnavailableError
ToolBudgetExhausted        → ToolBudgetExhaustedError
RepoPathInvalid            → RepoPathInvalidError
WalkerLimitExceeded        → WalkerLimitExceededError
```

(Plus `PathOutsideRepo` defined twice — `walker.py:38` and `tools/exceptions.py:43` — a separate concern; see §6.3.)

**Recommendation**: this is a **public API** decision. Since these names appear in `__all__` and are imported by users / tests:
- **Option A (purist)**: rename all 48 + add a deprecation-shim layer in 1.0.x → release 1.1 with old names removed. Zero idiom debt going forward.
- **Option B (pragmatic for v1)**: keep names; add `# noqa: N818` per-class with a comment noting the convention; document the choice in CONTRIBUTING. Lower churn, more idiosyncratic.
- **Option C (deferred)**: do nothing for v1.0.2; rename in v2 (semver-major) where breaking renames are expected.

I'd pick **C** for now — these are descriptive and Pythonic-enough; semver-major is the correct vehicle. Just be aware you're locking the names in.

### 6.2 `_private` / `__dunder` consistency

- All module-private helpers consistently use `_leading_underscore` (~ 200 sites).
- `__all__` is declared in 18+ modules as a tuple/list of strings; consistent.
- `__init__.py` (top-level) is minimal and correct: hardcoded `__version__ = "1.0.2"`. **Minor concern**: this duplicates the version in `pyproject.toml`. Recommend `__version__ = importlib.metadata.version("senex")` or the standard `version = {attr = "senex.__version__"}` dance in `pyproject.toml`. Not a blocker.

### 6.3 Duplicate exception class names

`PathOutsideRepo` is defined twice:
- `senex/walker.py:38`
- `senex/tools/exceptions.py:43`

They are separate types (different module paths) but share a name. Tools that mention "PathOutsideRepo" in error messages or logs will be ambiguous. Consider unifying or qualifying (`WalkerPathOutsideRepoError` / `ToolPathOutsideRepoError`) when you do the §6.1 rename pass.

### 6.4 snake_case / function naming

ruff `N802` flags one function: not surfaced in detail, but the codebase otherwise adheres to PEP 8 naming consistently. Check `_SymbolVisitor.visit_ClassDef` — that's `visit_<NodeName>` which is `ast.NodeVisitor`'s contract; legitimate.

---

## 7. Error-Handling Patterns

### 7.1 `# noqa: BLE001` justification

63 `# noqa: BLE001` comments across the codebase, almost all with inline justifications:

| Pattern | Count | Verdict |
|---------|-------|---------|
| `# noqa: BLE001 — convert to TypedError` | ~ 25 | **Justified** — translating to a typed exception. |
| `# noqa: BLE001 — never propagate to bus` | ~ 10 | **Justified** — disk-writer / consume callbacks must not break the run. |
| `# noqa: BLE001 — DOM not yet mounted` | ~ 8 | **Justified** — Textual widget initialization race; defensive. |
| `# noqa: BLE001 — pydantic raises multiple types` | ~ 5 | **Justified** — pydantic + tomllib + jsonschema raise heterogeneous exceptions. |
| `# noqa: BLE001` (no comment) | ~ 15 | **Mixed** — see below. |

**Important caveat**: the project's ruff config does **NOT enable `BLE001`** (it's only in `--select ALL`). `ruff --select RUF100` reports all 63 BLE001 directives as "non-enabled" — meaning they currently document intent but don't actually suppress anything. If you ever turn on `BLE001` (recommended), they will all become active without you doing anything else. Good defensive bookkeeping.

The 15 unjustified `# noqa: BLE001` (e.g., `auditor.py:611,615`, `cli_audit.py:357,377,381`, `phases/aggregate.py:158`) should each grow a one-word inline comment for future-reader benefit — none are **wrong**, just under-documented.

### 7.2 `except Exception:` review

About 8 sites use `except Exception:` *without* a `# noqa: BLE001`:

```
senex/compaction.py:384       except Exception:
senex/compaction.py:451       except Exception as exc:
senex/cli_wizard.py:412       except Exception:
senex/cli_wizard.py:466       except Exception:
senex/lmstudio_lifecycle.py:484, 585, 597, 845, 1013, 1065, 1139
senex/phases/preflight.py:260 except Exception:
```

Most translate the broad catch into a typed exception (`raise CompactionFailed(kind="lms_error", path=..., cause=exc) from exc`) — proper pattern. A few in `lmstudio_lifecycle.py` use `except Exception: pass` (S110 — see below).

### 7.3 Suspect `try/except/pass` (S110/S112)

ruff flagged 7 sites:

```
senex/lmstudio_lifecycle.py:585    try: ...    except Exception: pass    # SDK probe
senex/lmstudio_lifecycle.py:597    try: ...    except Exception: pass    # SDK probe
senex/tui/app.py:200               except (asyncio.CancelledError, Exception): pass
senex/tui/launcher.py:218          except Exception: pass    # DOM not mounted
senex/tui/launcher.py:242          except Exception: pass    # DOM not mounted
senex/tui/launcher.py:274          except Exception: pass    # DOM not mounted
senex/tui/monitor.py:150           except Exception: continue    # DOM transitioning
```

The TUI/DOM ones are defensive against Textual's lifecycle and are appropriate. The two SDK-probe `except Exception: pass` in `lmstudio_lifecycle.py:585,597` are **probe-and-fallback** patterns — you try to call the SDK, if anything blows up you fall through to the next backend. Acceptable but consider `log.debug("LM Studio SDK probe failed: %s", exc)` so the failure is observable in a debug session.

### 7.4 `log.error` vs `log.exception` (TRY400)

16 sites use `log.error(...)` inside an `except` block where `log.exception(...)` (which auto-attaches the traceback) would be better:

```
senex/auditor.py:261, 565, 569, 573, 577, 583, 592, 652
senex/cli_audit.py:352, 358, 448
senex/findings_aggregator.py:167
senex/subscribers/disk_writer.py:84
senex/subscribers/tui_subscriber.py:124
senex/tui/app.py:187
senex/tui/monitor.py:139
```

Fixing these is a one-line change per site and dramatically improves debuggability. **Recommended pre-publish fix.**

### 7.5 Custom exception hierarchies — exemplary

- `LMStudioError` base + 7 subclasses, each with `error_kind: str` class attribute mapped to recovery paths in `auditor.py`. Beautiful pattern, well-documented.
- `LifecycleError` similarly with subclasses; `InvalidModelId(LifecycleError, ValueError)` correctly multi-inherits to participate in `ValueError` clients.
- Tools have `ToolError` hierarchy; phases have `PhaseAborted`/`PreflightFailure`/etc.

---

## 8. Imports

### 8.1 `from __future__ import annotations`

**77 of 78 source files** import `from __future__ import annotations`. The one exception:

```
senex/prompts/__init__.py    (file is empty / 1 line)
```

Empty `__init__.py` doesn't need the import. Effectively 100% coverage.

### 8.2 Import sorting (ruff I001)

14 files have un-sorted import blocks:

```
senex/__main__.py
senex/cli.py
senex/cli_aggregate.py
senex/cli_audit.py
senex/cli_config_show.py
senex/cli_doctor.py
senex/cli_handlers.py            (2 blocks)
senex/cli_wizard.py
senex/memory.py
senex/phases/__init__.py
senex/phases/file_audit.py
senex/tui/launcher.py
senex/walker.py
```

All 14 are auto-fixable: `ruff check --fix --select I001 senex/`. Recommended pre-publish.

### 8.3 Late / inline imports

ruff `PLC0415` flags 75 occurrences of imports inside functions. Most are **deliberate**:
- Avoiding circular imports between `auditor` ↔ `events` ↔ `phases`.
- Lazy-loading vendor SDKs (`import lmstudio` only when a backend probe fires).
- Test isolation (`import tomli_w` only when serializing).

Special case: `senex/compaction.py:505`:

```python
# Late import to avoid pulling datetime at TYPE_CHECKING time only; we need it
# at runtime here.
from datetime import datetime, timezone  # noqa: E402
```

This is at the **bottom** of the module after function definitions. The reasoning in the comment is incorrect (`datetime` is always available at runtime; `from __future__ import annotations` only affects annotations). Move this import to the top of the file with the others — minor cleanup.

### 8.4 TYPE_CHECKING blocks

ruff `TC001` / `TC002` / `TC003` find 88 imports that could be moved into `if TYPE_CHECKING:` blocks. With `from __future__ import annotations`, this is purely a startup-time/cycle-avoidance optimization. Senex uses it correctly in a few places (`llm_client.py:13`); doing it everywhere is overkill. **No action needed.**

---

## 9. Logging

### 9.1 `logging.getLogger(__name__)` consistency

22 modules have `log = logging.getLogger(__name__)`. The remaining modules either:
- Don't log (pure data / pydantic models / utility modules), or
- Use the convention via local imports (e.g., `phases/discovery.py:78` uses `logging.getLogger(__name__).warning(...)` inline — slightly inconsistent but harmless).

**Convention**: 100% of files that log do so via `logging.getLogger(__name__)`. No `logging.basicConfig()` in library code. No global module-level `logging.x()` calls (verified). **Clean.**

### 9.2 `print()` in library code — **1 site, justified**

```python
# senex/cli_wizard.py:132
def _safe_print(msg: str, *, stdout: TextIO) -> None:
    """Print with cp1252 fallback for Windows terminals.""" 
    ...
    print(msg, file=stdout)
```

This is a wizard helper that intentionally writes to user-supplied `stdout` after handling the cp1252 fallback. Library boundary is `cli_wizard` (an interactive wizard module); using `print()` for terminal UI here is appropriate. **Justified.**

No other `print()` calls in the package.

---

## 10. Module-Level Docstrings

ruff `D100` (missing module docstring): **0 violations**. Every `senex/*.py` module has a top-level docstring. Several even document the relevant spec section (e.g., `events.py:1` — *"Implements spec §5.6 (event types), §5.6.1 (bus semantics)…"*). Documentation discipline is excellent.

The single file without a docstring is `senex/prompts/__init__.py` (empty), which doesn't need one.

---

## 11. Recommendations Summary (Prioritized)

### Must-fix before publish (1.0.3 / 1.1.0)

1. **Drop `openai>=1.50,<2.0`** from `pyproject.toml` and `requirements.txt`. Confirmed dead.
2. **Resolve `requirements.txt` ↔ `pyproject.toml` drift** — add `pathspec>=0.12.0` or delete `requirements.txt` (the canonical source for an installable package is `pyproject.toml`).
3. **Fix the 10 mypy-strict errors** in §1.1. All are mechanical; none require behavior changes.
4. **Run `ruff check --fix --select I001,UP017,UP037,UP041 senex/`** to sweep up 95+ trivial modernizations:
   - 14 import-block sorts
   - 21 `timezone.utc` → `datetime.UTC`
   - 53 stale forward-ref quotes
   - 10 `asyncio.TimeoutError` → `TimeoutError`
   Auto-fix is safe; spot-check with `git diff` before commit.

### Should-fix (improves debuggability and readability)

5. **Convert 16 `log.error(...)` → `log.exception(...)`** inside `except` blocks (ruff `--select TRY400` lists them all). One-line changes per site.
6. **Wrap the one blocking I/O in `phases/file_audit.py:312`** with `asyncio.to_thread(file.read_text, encoding="utf-8")` for consistency with the rest of the async codebase.
7. **Annotate the missing return type** at `senex/auditor.py:638` (`_disk_writer_dispatch`).
8. **Add inline justifications** to the ~15 `# noqa: BLE001` directives that lack comments.

### Nice-to-have (cosmetic)

9. **Lift `_StrictModel` to one canonical module** (`senex/_models.py`) instead of redefining in three files.
10. **Move `compaction.py:505` late import** of `datetime` to the top of the file.
11. **Refactor 4 `isinstance` chains** in `cli_config_show.py` and `cross_cutting.py` to `match` statements.
12. **Compute `__version__` from `importlib.metadata`** instead of hardcoding in `senex/__init__.py:8`.

### Defer to v2 (semver-major)

13. **Rename 48 exceptions** to add `Error` suffix (PEP 8 N818). Documented in §6.1; consider in v2.
14. **Disambiguate `PathOutsideRepo`** (defined in both `walker.py` and `tools/exceptions.py`) when doing the rename pass.
15. **Rename `lmstudio_*.py` → `llm_*.py`** to better reflect post-migration scope (it's now generic OpenAI-compat client + lifecycle, not vendor-specific).

### Consider enabling in `[tool.ruff.lint]`

16. The current ruff config is essentially defaults. A modest selection like:
    ```toml
    [tool.ruff.lint]
    select = ["E", "F", "W", "I", "B", "UP", "ASYNC", "RUF", "TRY", "BLE", "S", "PTH", "SIM"]
    ignore = ["TRY003", "EM101", "EM102"]    # exception-message style rules; choose to your taste
    ```
    would activate the rules that are already half-respected by the codebase. ~ 100-200 cosmetic fixes would surface; almost all auto-fixable.

---

## 12. Files Referenced

- `E:\senex\pyproject.toml`
- `E:\senex\requirements.txt`
- `E:\senex\senex\__init__.py`
- `E:\senex\senex\auditor.py`
- `E:\senex\senex\compaction.py`
- `E:\senex\senex\cli_audit.py`
- `E:\senex\senex\cli_config_show.py`
- `E:\senex\senex\cli_handlers.py`
- `E:\senex\senex\cli_wizard.py`
- `E:\senex\senex\config.py`
- `E:\senex\senex\events.py`
- `E:\senex\senex\findings_aggregator.py`
- `E:\senex\senex\lifecycle_cli.py`
- `E:\senex\senex\llm_client.py`
- `E:\senex\senex\lmstudio_client.py`
- `E:\senex\senex\lmstudio_errors.py`
- `E:\senex\senex\lmstudio_lifecycle.py`
- `E:\senex\senex\phases\__init__.py`
- `E:\senex\senex\phases\base.py`
- `E:\senex\senex\phases\file_audit.py`
- `E:\senex\senex\prompts\__init__.py`
- `E:\senex\senex\skills.py`
- `E:\senex\senex\subscribers\metrics.py`
- `E:\senex\senex\subscribers\tui_subscriber.py`
- `E:\senex\senex\tools\exceptions.py`
- `E:\senex\senex\tools\gitnexus_context.py`
- `E:\senex\senex\tools\list_symbols.py`
- `E:\senex\senex\tui\launcher.py`
- `E:\senex\senex\walker.py`

Tooling output saved alongside this review:
- `E:\senex\docs\publishability\_mypy_report.txt`
- `E:\senex\docs\publishability\_ruff_report.txt`
- `E:\senex\docs\publishability\_ruff_all_report.txt`
- `E:\senex\docs\publishability\_async_lints.txt`
