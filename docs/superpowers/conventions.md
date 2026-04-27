# senex — Engineering Conventions & Procedures

Reference document cited by every implementation plan task. Each task should comply with the relevant subset; significant deviations require an explicit comment on the deviation.

---

## Table of contents

1. Code style & structure
2. Type discipline
3. Async / concurrency
4. Error handling
5. Security (cross-cutting)
6. Testing
7. Logging & observability
8. Persistence & I/O
9. Subprocess invocations
10. Pydantic / data modeling
11. Git / commit / push procedures
12. Code-review procedures
13. Documentation
14. Performance & resource management
15. The senior-engineering rules (carry-over from spec §5.1)

---

## 1. Code style & structure

- **Python 3.11+.** Use `X | Y` unions, `match`/`case`, structural patterns where they help readability. PEP 8.
- **Pathlib** over `os.path`. **f-strings** over `.format()` / `%`.
- **One responsibility per file.** Target 200–500 lines. If a file hits 600, ask "does this handle two distinct domains?" — if yes, split. Functions over 50 lines are a smell.
- **Module-level docstring** on every `.py` file: purpose, key classes/functions, anchors back to the spec section it implements.
- **No commented-out code.** Git remembers.
- **Imports** at top of file, grouped: stdlib, third-party, internal. No conditional imports unless the import is genuinely platform-conditional.
- **Public API** lives at the package boundary (`senex/__init__.py` re-exports). Internal modules import each other via explicit relative imports (`from .events import EventBus`).

## 2. Type discipline

- **Type hints on every public function and method.** Internal helpers may omit only when blindingly obvious.
- **`mypy --strict`** target on `senex/`. New mypy errors block commits.
- **No `Any`** in public signatures. Use `TypeVar`, `Protocol`, or concrete types. `Any` in private code requires a comment explaining why.
- **`from __future__ import annotations`** at the top of every file (forward refs default to strings; cheap perf win on import).

## 3. Async / concurrency

- **`asyncio` is the concurrency model.** No `threading`/`multiprocessing` in v1 except where forced (e.g., `subprocess.Popen` is fine; `asyncio.create_subprocess_exec` preferred).
- **Bounded queues only.** Every `asyncio.Queue` declares an explicit `maxsize`. Default to 1024 unless the spec says otherwise.
- **`asyncio.create_task` handles must be held** or have a done-callback. Unheld tasks silently swallow exceptions. Pattern:
  ```python
  task = asyncio.create_task(work())
  task.add_done_callback(lambda t: t.exception() and log.error("...", exc_info=t.exception()))
  ```
- **No sync I/O in async paths.** File reads use `asyncio.to_thread(...)` or aiofiles; subprocess calls use `asyncio.create_subprocess_exec`.
- **Cancellation propagates.** Every long-running coroutine must respect `asyncio.CancelledError`: catch at boundaries, run cleanup, re-raise.
- **No locks held across `await`.** If you must, document why and use `asyncio.Lock` (not `threading.Lock`).
- **Bounded iteration.** Every `while` and every loop over external data has an explicit `max_iterations` cap; raise or `break + log` on overflow. "It should terminate eventually" is not a bound.

## 4. Error handling

- **Named exceptions, never bare except.** No `except:`, no `except Exception:` without a type-narrowing intent. If you need to catch everything to log+continue, re-raise after logging unless the spec explicitly allows continuing.
- **No `contextlib.suppress(Exception)`.** Suppress only specific known types, with a one-line comment naming the scenario.
- **Fail closed at boundaries.** On ambiguity, raise; never silently default to the lenient interpretation.
- **Exception classes live in module top-level**, named after the failure (`PreflightFailure`, `SchemaNegotiationFailed`, `FingerprintMismatch`).
- **Result-shaped returns** (`WriteDecision`, `ToolResult`, etc.) MUST be consumed; never `_ = function()` without a comment explaining why.
- **Recovery is event-driven**, not exception-driven, in the audit loop. Per-file failures emit `FileError` events and continue; only `LMSConnectionLost`, `KeyboardInterrupt`, and renderer/disk-fatal errors propagate to the run level.

## 5. Security (cross-cutting)

These rules apply to every task that handles paths, subprocess, secrets, or LLM input.

- **Path safety.** Any user/config-supplied path: `Path.resolve()` then verify `is_relative_to(repo_root_resolved)`. Reject `..`, UNC, drive-absolute outside the allowed root, and symlinks.
- **Subprocess hardening.** List-form args (`subprocess.run(["cmd", "arg", ...], shell=False)`); never string concatenation. Path-like args validated against `^[A-Za-z0-9_./\\-]+$` pre-call. Absolute binary paths cached at preflight.
- **Regex hardening.** Use `regex` (not stdlib `re`) with a timeout (~100ms) when the pattern comes from outside the codebase. Cap pattern length (≤ 256 chars) for user-supplied patterns.
- **Secret redaction at the boundary.** Apply `SecretRedactor` to: log lines, event-string fields, `<file>.thinking.md`, TUI streamed text, `<file>.md` reports, `combined.md`, `claude-handoff.md`, `config.snapshot.toml`. Default ON; opt-out via config only.
- **Timing-safe equality on tokens.** `hmac.compare_digest()` for any auth-relevant comparison; `==` is forbidden.
- **No hardcoded credentials**, even in test fixtures. Test secrets are obvious placeholders (`"sk-test-FAKE"` is fine; `"sk-real..."` is not).
- **ANSI/control-sequence strip** on every string that flows from LLM/tool output to a TUI widget or persisted markdown. Strip `\x00-\x08\x0b-\x1f\x7f` and OSC sequences.
- **Trust boundaries**: file source from audited repo is wrapped in `<UNTRUSTED_FILE_CONTENT>...</UNTRUSTED_FILE_CONTENT>` before LLM ingestion; system prompt instructs the model to disregard directives within. Never paste raw audited source into a system or assistant message.
- **No outbound network calls** other than to `localhost` LM Studio and local `npx gitnexus`/`lms` subprocesses. v1 has no other network surface.

## 6. Testing

- **TDD strictly.** Every task: write failing test, run to verify failure, implement minimum, run to verify pass, commit. Skip steps only on truly trivial config edits (and document why).
- **No mocks of internal code.** Mock only external boundaries (LM Studio HTTP via `respx`/`httpx_mock`; subprocess via `unittest.mock` patches on `subprocess.run`/`asyncio.create_subprocess_exec`).
- **Fixtures over setup/teardown duplication.** Use pytest fixtures with `scope="module"` for expensive setup.
- **Hypothesis property tests** on schema validators and the secret redactor.
- **Golden-file tests** for the renderer: byte-match expected markdown.
- **Recorded LMS replay** for end-to-end tests: capture once with `RECORD_LMS=1`, replay deterministically thereafter.
- **`@pytest.mark.live`** marker on live-LMS tests; excluded from default `pytest` runs. Run only via `pytest -m live` (manual / pre-release).
- **Coverage targets** (enforced in CI):
  - 85% on `auditor.py`, `renderer.py`, `walker.py`, `checkpoint.py`, `events.py`, `secret_redactor.py`, `findings_aggregator.py`, `phases/*`, `tools/registry.py`, `tools/safety.py`, `tools/loop.py`, `lmstudio_lifecycle.py`, `runlock.py`
  - 60% elsewhere
- **Test naming**: `test_<unit>_<scenario>_<expected_outcome>`. Avoid `test_works`.
- **One logical assertion per test** where practical. Multiple `assert`s on the same outcome (e.g., asserting fields of one returned object) is fine.
- **Test data inline or in `conftest.py`**, never in production code paths.

## 7. Logging & observability

- **Two streams, one source of truth.**
  - `audit.log` — human-readable; INFO/WARN/ERROR levels; tailable.
  - `events.jsonl` — machine-readable; schema-validated; one event per line; canonical for replay.
- **Every error path emits a structured event** with `kind` and `error_message`. Never log without emitting; never emit without logging.
- **Log levels**:
  - DEBUG: rarely; only behind `--verbose` or env flag
  - INFO: phase transitions, completion notices, expected control-flow milestones
  - WARN: degraded conditions that are tolerated (graph context unavailable, language anchor missing)
  - ERROR: failures that result in a `*.ERROR.md` artifact or run abort
- **Never log secrets.** Any field whose name matches `*_token`, `*_secret`, `password*`, `api_key*` is redacted before logging.
- **`structlog`-style key-value** preferred over f-string interpolation in logs (easier to filter; no accidental secret interpolation).

## 8. Persistence & I/O

- **Atomic writes.** All `.md` reports, `findings.json`, `checkpoint.json`, `config.snapshot.toml` use `tmp + fsync + rename` pattern. Crash mid-write never leaves a partial canonical file.
- **Append-only NDJSON** for streaming artifacts (`findings.partial.jsonl`, `events.jsonl`). Each line is a single `write()` call after explicit `flush()`. Crashes truncate at line boundaries.
- **Encoding: UTF-8 without BOM.** Line endings: LF for all generated artifacts (the .gitattributes / file write should not depend on platform).
- **Filesystem ops via `pathlib`.** Never raw `open(str(path), ...)`.
- **No global mutable state.** Configuration, event buses, and registries are passed explicitly through constructors.

## 9. Subprocess invocations

- **`asyncio.create_subprocess_exec`** for non-blocking; `subprocess.run` only when sync is genuinely needed (preflight quick-checks).
- **List-form args, `shell=False`.**
- **Absolute binary paths**: `npx`, `lms`, `python` resolved once at preflight via `shutil.which`; cached.
- **Stdin/stdout/stderr**: explicit `PIPE` or `DEVNULL`; never inherit.
- **Timeouts**: every subprocess call has a `timeout=` argument; on timeout, kill + raise.
- **Argument validation**: any argument derived from external state (file paths, model IDs) validated against an explicit regex before invocation.

## 10. Pydantic / data modeling

- **Pydantic v2 models** at every boundary: config, events, schema responses, tool inputs/outputs.
- **`extra="forbid"`** on every model. Unknown keys fail loud.
- **Use `Annotated` + `Field`** for constraints (length, range, regex). Don't validate in business code what pydantic can validate at the boundary.
- **Discriminated unions** for variant types (e.g., `Event` with `type` discriminator).
- **`model_dump_json()` over `json.dumps(model.model_dump())`** — handles datetime/Path/etc. serialization cleanly.

## 11. Git / commit / push procedures

- **Commit per task.** Every task in the plan ends with one commit. Don't batch.
- **Push after every milestone.** Mid-milestone pushes are encouraged; end-of-milestone pushes are required.
- **Commit message format**:
  ```
  <type>(<milestone>): <imperative summary>

  <optional body explaining why, not what>

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```
  Types: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, `release`, `validate`.
- **Atomic commits.** One logical change per commit. If a fix touches 5 files, that's fine; if it makes two unrelated changes, split.
- **Never `--force-push` to main.** Never `--no-verify`. Never bypass signing if it's configured.
- **No commits with failing tests.** Run `pytest` (the relevant subset) before committing.
- **Pre-commit lint+type**: `ruff check && mypy senex/` must pass.

## 12. Code-review procedures

- **Self-review before commit.** Re-read the diff: leftover prints, debug code, stray TODOs, secret-shaped strings.
- **Run targeted tests before commit.** `pytest tests/unit/test_<module>.py -v` for the module you touched.
- **Run lint+type before commit.** `ruff check senex/ tests/ && mypy senex/`.
- **Run impact check before edit** (when GitNexus index is current): `gitnexus_impact({target: "<symbol>", direction: "upstream"})`. d=1 callers MUST be updated; d=2 SHOULD be tested; d=3 MAY need testing on critical paths.
- **PR / branch policy**: implementation work happens on `feature/<milestone>-<slug>` branches when worktrees are used; merge to `main` only after `pytest tests/ -v --ignore=tests/live` is green and `team-review` (when invoked) returns.

## 13. Documentation

- **Module docstring** at every file top: 1-2 lines on what the module does and which spec section it implements.
- **Function docstrings** on public functions: one-line summary, then `Args:`, `Returns:`, `Raises:` if non-trivial. Google or NumPy style — pick one and stick with it (Google is shorter).
- **Inline comments**: only when the WHY is non-obvious. Don't explain WHAT — well-named identifiers do that.
- **CHANGELOG.md** updated per milestone, not per task. Entries are user-facing.
- **README.md** kept current — quickstart should always work against current `main`.
- **CLAUDE.md** at the repo root (auto-managed by GitNexus) — do not edit unless updating the senex-specific section.

## 14. Performance & resource management

- **Context managers everywhere** for files, sockets, subprocesses, asyncio resources. Never manually close.
- **Streaming over batching** for large I/O (file walking, NDJSON appends, LMS chat).
- **Cache expensive computations once** (compiled regexes, tiktoken encoders, `npx` binary path) at module load or first use. Don't recompile per call.
- **Memoize where the value is stable for the run** (`functools.lru_cache` on small bounded caches; explicit dict for larger).
- **Don't leak file handles**, even on exception paths. `try/finally` or context manager.
- **Yield, don't accumulate**, when producing large iterables (walker outputs, event streams).

## 15. The senior-engineering rules (carry-over from spec §5.1 / pensiv discipline)

These were the rules the system prompt instructs the audit MODEL to enforce. The implementation team SHOULD ALSO obey them:

- **Search before write.** Before adding any helper, search existing code (`grep`, `claude-context.search_code`, GitNexus query). If something close exists, reuse or extend; don't re-implement. Document the choice if you decide not to reuse.
- **Root cause before fix.** Never patch a symptom. Understand WHY the failing path exists and WHY it broke before changing anything. A fix without a stated root cause will recur.
- **Prefer deletion over addition.** Ask: should this code exist at all? The best fix is often removing the condition that allowed the bug.
- **Name at the abstraction level.** Functions named `handle`, `process`, `manage`, `do_thing` are placeholders, not names. Rename before merging. Function names say what they DO and what they RETURN.
- **Dependency hygiene.** Before adding a library: (a) check if existing deps cover it; (b) check if stdlib does. New deps need explicit user approval (this is a halt condition).
- **Bounded iteration.** (Already in §3.) Every loop over external data caps iterations.
- **Assertions for invariants in critical pipelines.** In `auditor.py`, `tools/loop.py`, `lmstudio_client.py`, `phases/*`: inline `assert` for pre/post-conditions tests can't catch (non-empty event sequences, monotonic seq, single-writer constraints). Asserts are side-effect free; never replace input validation at boundaries.
- **No ignored returns.** (Already in §4.) Every non-None return is consumed or `_ = expr()` with comment.

---

## Procedures (per-task checklist)

For every task in the plan, follow this checklist in order:

1. **Read the task fully** before opening any file. Note prerequisites; verify they're complete.
2. **Run impact check** (when applicable): `gitnexus_impact` on any symbol you'll modify.
3. **Write the failing test first.** Run it. Verify the failure mode matches expectation.
4. **Implement the minimal code** to make the test pass. Resist scope creep.
5. **Run the targeted test.** Verify pass.
6. **Run the broader test subset** for the module you touched.
7. **Run lint + type**: `ruff check senex/ tests/ && mypy senex/`.
8. **Self-review the diff.** Look for leftover prints, debug logs, TODOs, scope drift.
9. **Stage specific files** (`git add path1 path2`) — never `git add -A` or `git add .`.
10. **Commit** with the format from §11.
11. **Push** if the milestone says to (always at milestone end; encouraged mid-milestone).
12. **Update the task checkbox** to `- [x]` in the plan file.

Deviations from this checklist require an explicit comment in the commit body.
