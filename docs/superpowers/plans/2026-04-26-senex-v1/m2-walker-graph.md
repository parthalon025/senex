# Milestone 2: Walker + Graph Awareness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task uses checkbox (`- [ ]`) syntax for tracking. Sequential within this milestone; check Prerequisites before starting.

## Context

M2 delivers Layer 1: file discovery and graph-context construction. The walker turns "a repo path on disk" into "a deterministic, gitignore-aware, safety-checked list of files to audit." The graph awareness builder turns "a target file" into "a small text block describing what GitNexus knows about that file's neighborhood." Together these feed Phase 1 (Discovery) and Phase 2 (per-file audit) of the auditor.

**Architectural intent:** Walker is the security boundary for the filesystem; graph awareness is the security boundary for subprocess invocation of `npx gitnexus`. Both must be hardened before any audit code runs against a real repo. Prompts (§5.1, §5.3) ship in this milestone because they are *static* assets the auditor consumes — keeping them with the discovery layer means M3+ can stop worrying about prompt files existing.

## Prerequisites

- **Completed milestones:** M1 Foundation
- **Required modules from prior work:**
  - `senex/events.py` — `FileSkipped`, `SymlinkSkipped`, `FileContextBuilt` event types
  - `senex/config.py` — `WalkerCfg`, `RepoCfg.gitnexus_repo_name`
- **Required tools/state:**
  - `npx gitnexus` available on PATH (tested via subprocess from `graph_awareness.py`)
  - At least one repo indexed by GitNexus to capture fixture outputs (the `senex` repo itself is indexed per CLAUDE.md — use it for capture)

## Deliverable

This milestone creates the following files:

- `senex/walker.py` — `Walker.discover(repo_path, config) -> (files, skipped)`
- `senex/graph_awareness.py` — `GraphContextProvider` protocol + `GitNexusCLIProvider` impl + `prefetch_all()`
- `senex/prompts/lang_python.md`, `lang_typescript.md`, `lang_rust.md`, `lang_go.md`, `lang_csharp.md` — per-language anchors
- `senex/prompts/_anchor_loader.py` — `select_anchor(file_path)` + `build_user_prompt(...)`
- `senex/prompts/system_senior_dev.md` — verbatim from spec §5.1
- `senex/prompts/per_file_user.md` — Jinja-style per-file template
- `senex/prompts/cross_cutting.md` — crosscut instructions
- `senex/prompts/claude_handoff.md` — verbatim from spec §7.4
- `senex/prompts/compaction.md` — compaction instructions
- `tests/fixtures/repos/tiny_python/` — synthetic 3-5 file repo (used by M3+, M8, M10)
- `tests/fixtures/gitnexus_outputs/` — captured `npx gitnexus` JSON
- `tests/fixtures/expected_prompt_hashes.json` — sha256 of every prompt for regression detection

## Downstream consumers

- **M3** uses the per-language anchors and per-file user prompt template via `_anchor_loader`.
- **M5** uses `tests/fixtures/repos/tiny_python/` as the test repo for tool-loop tests.
- **M7** consumes the crosscut/handoff prompts (renderer + handoff writer).
- **M8** Discovery phase wraps `Walker.discover()`; FileAudit phase composes `build_user_prompt()` + `GraphContextProvider.fetch()` per file.
- **M10** live validation runs against `tests/fixtures/repos/tiny_python/`.

## Spec sections referenced

- §5.1 System prompt — `system_senior_dev.md` verbatim source (TRIAGE GATE, TRUST BOUNDARY, CONFIDENCE RUBRIC, etc.)
- §5.3 Per-Language Anchors — paragraph-per-language idioms + pitfalls
- §5.8 GitNexus Context Builder — `GraphContextProvider` protocol shape
- §5.9 Walker — gitignore + ext filter + size cap + symlink guard semantics
- §7.4 Claude handoff — `claude_handoff.md` verbatim source
- §SEC-3 — symlink escape protection (resolve + `is_relative_to`, `followlinks=False`)
- §SEC-4 — subprocess hardening (`shell=False`, list-form args, regex-validated path args)
- §ARCH-6 — `GraphContextProvider` protocol abstraction
- §ARCH-8 — batch fetch / in-memory cache pattern
- §ARCH-14 — case-collision handling on case-insensitive filesystems

## Key contracts

- **`Walker.discover(repo_path, config) -> tuple[list[Path], list[(Path, str)]]`** — returns (kept files, skipped files with reason). Deterministic sort by relpath ascending.
- **`GraphContextProvider`** protocol — `fetch(file_relpath) -> GraphContext`.
- **`GitNexusCLIProvider`** impl — `_resolve_npx_once()` cached at preflight; validates relpath regex pre-subprocess.
- **`GitNexusCLIProvider.prefetch_all(file_paths)`** — populates an in-memory dict; per-file `fetch()` reads from cache.
- **`select_anchor(file_path)`** — extension → anchor text or None.
- **`build_user_prompt(file, source, awareness, language)`** — composes the per-file user message.

## Watch-outs

- **Symlink guard is the difference between safe and exfiltration-vulnerable.** Don't ship a walker without the `Path.resolve().is_relative_to(repo_root_resolved)` check. `os.walk(..., followlinks=False)` is necessary but not sufficient — a top-level symlink can still escape if you `Path.resolve()` it later.
- **Subprocess args MUST be list form.** No string concatenation, no `shell=True`. Path-like args must match `^[A-Za-z0-9_./\\-]+$` *before* the subprocess is constructed (§SEC-4). Mock + assert this in tests.
- **Case-collision handling (§ARCH-14):** on Windows/macOS HFS+, `Foo.py` and `foo.py` collide. Deterministic policy: first wins by sort order; second gets `~<hash>` suffix in the *report path* (not the source path).
- **Prompt hash stability:** `tests/fixtures/expected_prompt_hashes.json` exists to catch unintended prompt drift. If you intentionally edit a prompt, regenerate the hash file in the same commit.
- **`graph_context_tokens=0` on subprocess failure** is the documented sentinel — emit `FileContextBuilt` with that field rather than skipping the event entirely.

## Patterns to follow

- **Capture before implementing** for fixtures (Task 2.2.1): run `npx gitnexus context|query` against the live `senex` repo and save the JSON to `tests/fixtures/gitnexus_outputs/`. Do not hand-craft these.
- **Spec-verbatim copies:** `system_senior_dev.md` (§5.1) and `claude_handoff.md` (§7.4) are copied byte-for-byte from the spec.
- **Hash-stability tests:** every committed prompt is hashed and the hash committed alongside; a mismatch is a test failure.

## Tasks

### Task 2.1: Walker with safety guards

**Files:**
- Create: `senex/walker.py`
- Create: `tests/unit/test_walker.py`
- Create: `tests/fixtures/repos/tiny_python/` (5 files; see §M9)

- [ ] **Step 2.1.1: Failing tests** (use `tmp_path` for synthetic repos):
  - Honors `.gitignore`
  - Applies extension filter (default list)
  - Skips `node_modules`, `.venv`, `dist`, `build`, `__pycache__`
  - `--include-tests=False` excludes `tests/`
  - File > `max_size_bytes` → emits `FileSkipped` reason="too_large"
  - **Symlink escape (§SEC-3)**: symlink to outside repo root → `SymlinkSkipped` event, file NOT included
  - **Case-collision (§ARCH-14)**: `Foo.py` + `foo.py` on case-insensitive FS → second gets `~<hash>` suffix
  - Deterministic order: sorted by relpath ascending

- [ ] **Step 2.1.2: Implement `Walker`** class:
  - `discover(repo_path, config) -> tuple[list[Path], list[(Path, str)]]` returns (files, skipped[])
  - `os.walk(..., followlinks=False)`; for each file `Path.resolve().is_relative_to(repo_root_resolved)` check
  - Use `pathspec` for `.gitignore` (already a popular dep; add to pyproject)
  - Skip case-collisions deterministically (first wins; second renamed report path)
  - Deterministic sort by relpath

- [ ] **Step 2.1.3: Run tests** → green.

- [ ] **Step 2.1.4: Commit** `feat(M2): walker with symlink guard, gitignore, case-collision detection`.

### Task 2.2: Graph awareness builder (gitnexus CLI integration)

**Files:**
- Create: `senex/graph_awareness.py`
- Create: `tests/unit/test_graph_awareness.py`
- Create: `tests/fixtures/gitnexus_outputs/` (captured `npx gitnexus context|query` JSON)

- [ ] **Step 2.2.1: Capture `npx gitnexus context --repo senex --json`** + `npx gitnexus query --repo senex --goal "What does X do?" --limit 3 --json` outputs into fixture directory.

- [ ] **Step 2.2.2: Failing tests**:
  - `build_awareness_block(repo_name, file_relpath, fixture_loader)` returns a string with cluster name, public symbols, d=1 caller counts, top processes
  - Subprocess CalledProcessError → returns "[graph context unavailable]" sentinel + emits `FileContextBuilt` with `graph_context_tokens=0`
  - `subprocess.run(...)` is called with `shell=False` and list-form args (mock + assert)
  - Path-like args in command match `^[A-Za-z0-9_./\\-]+$` (security guard §SEC-4)

- [ ] **Step 2.2.3: Implement `GraphContextProvider`** protocol (per §ARCH-6) and `GitNexusCLIProvider` impl:
  - `fetch(file_relpath) -> GraphContext` — runs gitnexus CLI list-form, parses JSON
  - `_resolve_npx_once()` at preflight; cached. Validates relpath regex pre-subprocess.

- [ ] **Step 2.2.4: Implement batch fetch** (§ARCH-8): `prefetch_all(file_paths)` → in-memory dict; per-file `fetch()` reads from cache.

- [ ] **Step 2.2.5: Run** tests → green.

- [ ] **Step 2.2.6: Commit** `feat(M2): GraphContextProvider with gitnexus CLI backend + batch fetch`.

### Task 2.3: Per-language anchor loader

**Files:**
- Create: `senex/prompts/lang_python.md`, `lang_typescript.md`, `lang_rust.md`, `lang_go.md`, `lang_csharp.md`
- Create: `senex/prompts/_anchor_loader.py`

- [ ] **Step 2.3.1: Write each anchor** as a 1-paragraph file naming idioms, common pitfalls, stdlib preferences. Python anchor example: "Python 3.10+: `X | Y` unions, structural `match`, `pathlib.Path` over `os.path`, `f-strings`. Common pitfalls: bare `except:` catches `KeyboardInterrupt`; `is` vs `==` for None; mutable default args; missing `await` on coroutines."

- [ ] **Step 2.3.2: Implement `select_anchor(file_path) -> str|None`** — maps extension → anchor file; returns anchor text or None.

- [ ] **Step 2.3.3: Test** that `select_anchor("foo.py")` returns Python anchor; `foo.unknown` returns None.

- [ ] **Step 2.3.4: Commit** `feat(M2): per-language prompt anchors`.

### Task 2.4: System prompt + per-file user prompt

**Files:**
- Create: `senex/prompts/system_senior_dev.md`
- Create: `senex/prompts/per_file_user.md`

- [ ] **Step 2.4.1: Write `system_senior_dev.md` verbatim from spec §5.1** (with TRIAGE GATE, TRUST BOUNDARY, CONFIDENCE RUBRIC, healthy-finding shape, name-flag combo rule, code-fence prevention, TOOL USE block).

- [ ] **Step 2.4.2: Write `per_file_user.md`** as a Jinja-style template:
  ```
  ### Awareness
  {graph_context}

  ### File: {file_relpath}
  Language: {language}

  <UNTRUSTED_FILE_CONTENT>
  {numbered_source}
  </UNTRUSTED_FILE_CONTENT>
  ```

- [ ] **Step 2.4.3: Implement `build_user_prompt(file, source, awareness, language)`** in `senex/prompts/_anchor_loader.py` (or `prompts.py`).

- [ ] **Step 2.4.4: Commit** `feat(M2): system + per-file prompt templates`.

### Task 2.5: Crosscut + handoff + compaction prompts

**Files:**
- Create: `senex/prompts/cross_cutting.md`
- Create: `senex/prompts/claude_handoff.md`
- Create: `senex/prompts/compaction.md`

- [ ] **Step 2.5.1: Write `cross_cutting.md`** — instructions to identify repo-wide themes from compressed per-file findings; emit `crosscut_response.schema.json`-conformant output.

- [ ] **Step 2.5.2: Write `claude_handoff.md`** verbatim from spec §7.4.

- [ ] **Step 2.5.3: Write `compaction.md`** — instructions to summarize tool history into evidence_summary + key_findings_so_far + unanswered_questions per `compaction_response.schema.json`.

- [ ] **Step 2.5.4: Test** that all prompts hash deterministically (sha256 of bytes); commit hashes to a `tests/fixtures/expected_prompt_hashes.json` for regression detection.

- [ ] **Step 2.5.5: Commit** `feat(M2): crosscut + handoff + compaction prompts`.

## Acceptance criteria

- `pytest tests/unit/test_walker.py tests/unit/test_graph_awareness.py -v` is 100% green.
- A symlink test (file pointing outside repo root) is verifiably skipped: the test asserts both the file is absent from the kept list AND a `SymlinkSkipped` event was published.
- Mocked subprocess assertions confirm `shell=False` and list-form args for every `npx gitnexus` invocation.
- All 5 per-language anchor files exist and `select_anchor("foo.py")` returns the Python anchor; `select_anchor("foo.unknown")` returns None.
- All 8 prompt files exist (5 anchors + system + per-file + crosscut + handoff + compaction); each has a sha256 entry in `tests/fixtures/expected_prompt_hashes.json` and the hash matches.
- `tests/fixtures/repos/tiny_python/` contains 3-5 trivially-flawed Python files for downstream tests.
- `tests/fixtures/gitnexus_outputs/` contains real captured JSON from `npx gitnexus context|query --repo senex --json`.
