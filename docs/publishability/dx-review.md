# senex — Developer Experience (DX) Review

Date: 2026-04-29
Scope: read-only review of `E:\senex` against a brand-new-user install + first-run path.
Reviewer perspective: senior DX optimizer.

This review measures friction in the path from `pip install senex` to "first audit
report visible". It cross-references the live source under `E:\senex\senex\` (CLI,
wizard, doctor, preflight, lifecycle, headless subscriber, infra/sglang) with the
README's onboarding promise.

---

## 0. Top-line summary

senex's onboarding has **one structural blocker**: the install path is documented
as `pip install senex` (or implied by the project metadata) but the actual first-run
path requires Docker + WSL2 + a 15 GB image+model pull + a manual TOML hand-edit
before the first audit can succeed. The doctor command stops at LM-Studio-style
checks and never tells the user that the heavyweight dependency stack (Docker, WSL,
the SGLang container, the model on disk) is even part of the contract.

The good news: most of the plumbing (`_ensure_managed_container_up`, the SGLang
compose file, the `[lmstudio.sglang]` config block with `manage_container=true`,
the wizard's backend auto-detection, the `cli_wizard._select_context_window` model
cap probe) is already in place. The user-facing surface has not caught up to it.

Friction-level legend:

- **BLOCKER** — first-run will fail, or user cannot self-recover.
- **ANNOYING** — first-run succeeds but only after multi-step manual reasoning.
- **MINOR** — polish; degrades trust but not function.

---

## 1. Time-to-first-audit (TTFA)

Estimated wall-clock for a clean Windows 11 box with no prior tooling, broadband:

| Step | Required? | Time | Friction | Notes |
|---|---|---:|---|---|
| Install Python 3.11+ | yes | 5–10 min | minor | Doc'd in README; well-known. |
| `pip install -e .` (or eventual `pip install senex`) | yes | 1–2 min | minor | Currently editable-install only — no PyPI publish. README §Install assumes git clone. |
| Install WSL2 + Ubuntu | Windows only | 15–30 min | **BLOCKER** | `wsl --install` + reboot. Not mentioned in README. |
| Install Docker (Desktop or engine in WSL) | yes | 10–20 min | **BLOCKER** | Not mentioned in README. `docker compose` invoked by `infra/sglang/sglang.sh`. |
| Install Node + `npx` (for gitnexus) | recommended | 5–10 min | annoying | README says optional but every doctor run flags `gitnexus_index: warn` without it; `gitnexus_query` tool then degrades silently. |
| Install `semgrep` | optional | 2–5 min | minor | Tool returns `ToolUnavailable` cleanly (`run_semgrep.py:206`); the system prompt advertises the tool regardless. |
| Pull `lmsysorg/sglang:latest` Docker image | yes | 10–25 min (~10 GB) | **BLOCKER** | First `docker compose up` triggers it; not surfaced as a step. |
| Pull `Qwen/Qwen3-8B-AWQ` weights | yes | 5–15 min (~5 GB) | **BLOCKER** | Same — happens on container start, no progress affordance from senex side. |
| Author `senex.config.toml` | yes | 10–30 min | annoying | `cp .example` then edit `[[repos]]` block + `[lmstudio]` block. Easy to misalign `model` vs `SGLANG_MODEL` in `.env`. |
| Edit `infra/sglang/.env` | yes (live config) | 2–5 min | annoying | Two-file split: `senex.config.toml` AND `infra/sglang/.env` both contain the model id. Drift = hard error at runtime. |
| `cd <repo> && npx gitnexus analyze` on target repo | recommended | 1–5 min/repo | minor | Required for `gitnexus_*` tools to return useful payloads. |
| `senex audit <repo>` first run | yes | 7–8 min/file × N | – | Per-file effort=high. |

**Realistic clean-box TTFA: 60–110 minutes before the first audit even starts**,
plus 7–8 min for the first file to complete with default settings.

### Biggest friction sinks (ranked)

1. **Docker + WSL2 install is invisible in the README.** A user who follows the
   current README §Install will run `pip install -e .` then `senex doctor` and get a
   `lifecycle_backend: fail` (`preflight.py:282`) with the message "no lifecycle
   backend (HTTP, lmstudio SDK, lms CLI); verify SGLang/LM Studio is running". The
   user is not told "you need Docker, you need WSL, here is the script to start it".
   **BLOCKER**.

2. **The README still describes the LM Studio / Gemma path** (lines 26–133) but
   the live `senex.config.toml`, the SGLang compose file, and the wizard's
   auto-detection all assume SGLang + Qwen3-8B-AWQ. New users following the README
   will install LM Studio, load Gemma, and then be confused when the example config
   points at port 30000 and the wizard prints SGLang checklists. **BLOCKER for
   trust** (the doc and the binary disagree).

3. **Two-file model-id split** — `senex.config.toml` `[lmstudio].model =
   "Qwen/Qwen3-8B-AWQ"` and `infra/sglang/.env` `SGLANG_MODEL=Qwen/Qwen3-8B-AWQ`
   must match byte-for-byte. The wizard at `cli_wizard.py:328-338` papers over
   one direction (uses the served id when config drifts) but the reverse case
   (config has model X, .env has model Y, container loads Y) silently picks the
   served model. **ANNOYING**.

4. **Image + model pull happens on first `docker compose up`** with zero
   feedback in the senex CLI surface. `_ensure_managed_container_up` prints
   `"[sglang] Starting container (compose up -d) — this may take a moment on
   first run..."` (`cli_audit.py:231-235`) and then waits up to
   `startup_timeout_seconds=180` (`config.py:134`). On a cold pull this WILL
   exceed 180s — the user sees `ModelLoadFailed` after 3 minutes, with no
   indication that the underlying problem is "downloading 15 GB". **BLOCKER**
   for first-run discoverability.

5. **No `senex init` command.** Today the user must:
   - `cp senex.config.toml.example senex.config.toml`
   - `cp infra/sglang/.env.example infra/sglang/.env` (the .env exists but is
     committed; there is no .example, so a `pip install`-only user from PyPI
     will not get the `infra/` tree at all unless we ship it as package data —
     which the current `pyproject.toml:35` package-data does NOT do).
   - Hand-edit `[[repos]]`.
   - Manually run `npx gitnexus init` per repo.

   **ANNOYING bordering on BLOCKER** for a PyPI-distributed install.

---

## 2. Bootstrap automation — `senex init` recommendation

**Recommend:** add `senex init` (and a separate `senex setup --check` for re-runs).
Effort: **medium** (~1–2 days).

Proposed surface:

```
senex init                  # interactive — generates config + .env + checks deps
senex init --non-interactive --model Qwen/Qwen3-8B-AWQ --repo <path>
senex setup pull            # docker compose pull + model warmup
senex setup verify          # equivalent to doctor, friendly output
```

`senex init` should:

1. **Detect the host** — `shutil.which("docker")`, `shutil.which("wsl")`,
   `shutil.which("node")`, `shutil.which("npx")`, `shutil.which("semgrep")`.
   On Windows, additionally probe `wsl -l -v` to confirm a distro is registered
   and `wsl docker --version` if Docker isn't on the Windows host directly.
   Print a checklist like `senex doctor` does today, but for *install-time* deps,
   not runtime preflight.

2. **Generate `senex.config.toml`** in the cwd (or `~/.senex/`) from the
   shipped template (already at `senex.config.toml.example`). Prompt for:
   - `[output].root` (default `~/senex-audits`, **not** `E:/senex-audits` — the
     current example hardcodes `E:/` which is a Justin-specific drive letter that
     will crash for any user without an E drive).
   - First `[[repos]]` entry (name + path).
   - SGLang vs LM Studio backend choice.
   - Auto-derive `[lmstudio].model` from the chosen `infra/sglang/.env`
     `SGLANG_MODEL` so the two stay in sync.

3. **Optionally pull the SGLang image + model.** Prompt:
   `"Pull lmsysorg/sglang:latest (~10 GB) now? [Y/n]"`. If yes, run the compose
   pull *with progress to stdout* — the user sees layer download bars rather than
   a 3-minute spinner. Then run `compose up -d` and tail the SGLang logs until
   `/health` returns 200, with a heartbeat line every 10s so the user knows
   model load is in progress.

4. **Validate before exit.** Run the doctor check set (`preflight.py`) so the
   user sees a green table before the wizard ever shows up.

**Where to put it:** new module `senex/cli_init.py` + new subparser in `cli.py:106`
(modeled on `senex doctor`). Reuse `_ensure_managed_container_up` from `cli_audit.py`
for the docker-up portion. Effort: medium.

---

## 3. Onboarding documentation — quickstart.md

Currently absent. The README is comprehensive (~290 lines) but front-loads
configuration details before the user has tried anything. **MINOR-to-ANNOYING.**

Recommendation: ship `docs/quickstart.md` as a 5-minute path:

```
1. pip install senex
2. senex init        (interactive; creates config + checks Docker/WSL)
3. senex setup pull  (pulls SGLang + Qwen3-8B-AWQ; ~30 min one-time)
4. senex audit <your-repo>
5. open <printed audit_dir>/combined.md
```

Hard-fail any step with a one-line remediation. Then move the current README's
"LM Studio setup", "Performance tuning", "Security" sections into `docs/install.md`
and `docs/operations.md`.

Effort: **small** (~half a day).

---

## 4. Error messages on missing dependencies

Auditing every config / connectivity error site for "is this actionable?":

### `preflight.py` — most messages are reasonable

| Site | Message | Verdict |
|---|---|---|
| `check_config_parses` (`preflight.py:103`) | `"config parse failed: <exc>"` | OK — exc carries detail. |
| `check_repo_path` (`preflight.py:114`) | Multiple variants ("must be absolute", "no .git/ subdir") | **good** — points at the exact field. |
| `check_output_dir_writable` | `"output dir has only X bytes free; need Y"` | **good**. |
| `check_lifecycle_backend` (`preflight.py:282`) | `"no lifecycle backend (HTTP, lmstudio SDK, lms CLI); verify SGLang/LM Studio is running or set lifecycle.auto_load=false"` | **WEAK** — does not mention Docker, does not link to `infra/sglang/sglang.sh up`, does not mention WSL. **ANNOYING**. |
| `check_lms_reachable` (`preflight.py:330-347`) | `"inference server unreachable at <url>"` | **WEAK** — same problem. The user is not told "if you have manage_container=true, run senex audit and it will start docker for you; otherwise run `bash infra/sglang/sglang.sh up`". **ANNOYING**. |
| `check_model_loaded_or_loadable` | `"model {target!r} not loaded; set auto_load=true to auto-load"` | **WEAK** — for SGLang, auto_load doesn't actually load a model; the model is set at container-start time via `SGLANG_MODEL`. The fix here is "edit `infra/sglang/.env` and restart the container", which the message does not say. **ANNOYING**. |
| `check_gitnexus_index` | `"repo has no .gitnexus/ directory; graph context unavailable"` | **WEAK** — does not say `npx gitnexus analyze`. **MINOR**. |

### `cli_audit.py` — config-not-found

`cli_audit.py:90-95` already prints both candidate paths. **good**.

### `cli_wizard.py` — backend probes

`cli_wizard.py:309-320` is **excellent** — names both LM Studio and SGLang, tells
the user what to do for each. This is the bar; preflight messages should be
elevated to match.

### `_ensure_managed_container_up` — docker startup

`cli_audit.py:239-247` prints `"SGLang container start failed: <exc>"`. The `<exc>`
from `ModelLoadFailed` typically contains a docker-compose stderr line which is
useful for experts but cryptic for new users. **ANNOYING**. Recommend wrapping
common failure modes:

- "docker: command not found" → "Docker is not installed or not on PATH. Install Docker Desktop (Windows) or `apt-get install docker-ce` (Linux). On Windows you also need WSL2."
- "Cannot connect to the Docker daemon" → "Docker daemon is not running. On Windows: open Docker Desktop. On Linux: `sudo systemctl start docker`."
- timeout exceeded → "Container did not become healthy within Xs. First-run model download can take 20+ minutes; tail logs with `bash infra/sglang/sglang.sh logs`."

Effort: **small** (~1 hour each).

### Recommended preflight rewrites

```
# preflight.py:282 (current)
"no lifecycle backend (HTTP, lmstudio SDK, lms CLI); verify SGLang/LM Studio is running or set lifecycle.auto_load=false"

# proposed
"Inference server is not running.
  - SGLang (recommended): bash infra/sglang/sglang.sh up   (or set [lmstudio.sglang].manage_container=true to have senex start it for you)
  - LM Studio: launch the app and click 'Start Server'
  - Or set [lmstudio.lifecycle].auto_load=false to skip this check."
```

Effort to fix all preflight messages: **small** (~half a day).

---

## 5. `senex doctor` UX — current vs recommended

### Current checks (`cli_doctor.py:128-173` + `preflight.py`):

- `config_parses`
- `repo_path`
- `output_dir_writable`
- `gitnexus_index` (warn-only)
- `addendum_safety`
- `language_anchors` (warn)
- `sampling_ranges`
- `lifecycle_backend`
- `runlock_dir_writable`
- `lms_reachable` (async)
- `model_loaded` (async)
- `schema_with_thinking` (async, warn)
- `streaming` (async, warn)

This is good runtime preflight. It is **inadequate for a brand-new install**.

### Missing checks (recommend adding):

| Check | Status | Friction | Effort |
|---|---|---|---|
| **Python version >= 3.11** | covered by argparse + entry-point but not reported in the doctor table | minor | trivial |
| **Docker installed** (`shutil.which("docker")` + `docker --version`) | **missing** | **BLOCKER** | trivial |
| **Docker daemon reachable** (`docker info` exit code) | **missing** | **BLOCKER** | small |
| **WSL2 present** (Windows only — `shutil.which("wsl")` + `wsl --status`) | **missing** | **BLOCKER** on Windows | small |
| **Node + npx installed** (gitnexus dep) | **missing** | annoying | trivial |
| **semgrep installed** (warn-only — optional) | **missing** | minor | trivial |
| **SGLang container running** | partially covered by `lms_reachable` but should be separate so user knows "service is down" vs "wrong url" | annoying | trivial |
| **Disk space at `[output].root`** | covered (`check_output_dir_writable` passes `1024 * 1024` = 1 MB minimum, which is laughably low for an audit run that produces 100+ MD files) | minor — bump min to 500 MB | trivial |
| **Disk space for HF cache** (`~/.cache/huggingface` or the Docker volume) — at least 20 GB free pre-pull | **missing** | annoying | small |
| **`senex.config.toml` model id matches `infra/sglang/.env` SGLANG_MODEL** | **missing** | annoying | small |
| **GPU availability + VRAM** (`nvidia-smi --query-gpu`) — Qwen3-8B-AWQ needs ~7 GB free | **missing** | annoying | small |

### Recommended doctor surface

Two modes:

```
senex doctor                # runtime preflight (what it does today)
senex doctor --install      # install-time deps: Docker, WSL, Node, semgrep, GPU, disk
```

`--install` runs first when invoked together. Effort: **small-to-medium**
(~1 day total).

---

## 6. Default config sanity

Reviewing `senex.config.toml.example` for gotchas a brand-new user will hit:

| Field | Issue | Friction | Fix |
|---|---|---|---|
| `[output].root = "E:/senex-audits"` | **Hardcoded Justin-specific drive letter.** Users without an E: drive will get a `Permission denied` or `OSError` on first audit. | **BLOCKER** | Default to `~/senex-audits` (cross-platform). Trivial. |
| `[lmstudio].base_url = "http://localhost:1234/v1"` | **Wrong** — points at LM Studio. Live config uses `:30000` for SGLang, the wizard auto-detects backend by port, but the example does not match the codebase reality. | annoying | Change example to `:30000`. Trivial. |
| `[lmstudio].model = "google/gemma-4-26b-a4b"` | **Wrong** — points at a model that is not in the live config and is not what `infra/sglang/.env` sets. | annoying | Match SGLang `.env` default `Qwen/Qwen3-8B-AWQ`. Trivial. |
| `context_window` field absent in example | The example does not set `[lmstudio].context_window`. Code default is 32768 (`config.py:188`); Qwen3-8B-AWQ caps at 32768; SGLang `--context-length` defaults to 32768. So defaults align — **but** the wizard documentation references it (you mentioned 131072 was a previous bug). The example should make this explicit. | minor | Add `context_window = 32768` with a comment. Trivial. |
| `strict_json_schema = false` in example, but live `config.py:194` defaults `True` and the live `senex.config.toml` sets it to `True` for SGLang | drift | minor | Set to `true` in the example, comment "set false only for older LM Studio + Gemma". Trivial. |
| `[lmstudio.lifecycle].auto_load = true` | for SGLang this is a no-op (the container starts with the model baked in). Comment is correct in the live config (lines 95–98) but missing from the example. | minor | Copy the live config's comment block into the example. Trivial. |
| `[lmstudio.sglang]` block missing entirely from the example | New users have no idea `manage_container` exists. | annoying | Add a commented-out `[lmstudio.sglang]` block with `manage_container = false` and a comment explaining the trade-off. Trivial. |
| `[walker].max_files = 50000` | Live config bumps this to `500_000_000` because pensiv kept hitting the limit; the example will reject any reasonably-large repo. Wizard now offers `scan_subdir` fallback (good), but the limit itself is off. | annoying | Bump example to `100000` and let `scan_subdir` handle the rest. Trivial. |
| `[[repos]] path = "E:/pensiv"` | **Hardcoded Justin-specific path.** | annoying | Replace with `<set me>` / a placeholder + a comment "uncomment and set this for nightly mode". Trivial. |

Effort to fix all example-config gotchas: **trivial** (~30 min).

---

## 7. Editor integration (VSCode / JetBrains plugin)

**Out of scope for v1.** Confirm. The CLI surface is complete; the audit produces
markdown that any editor opens natively. A plugin makes sense post-v1 once the
report format stabilizes (it has — see `findings.json` schema).

If anything for v1: a `senex.code-workspace` recommended-extensions file in
`docs/` that suggests the user pin Python 3.11+ and adds tasks for `senex audit`
and `senex view`. Effort: trivial. Friction: minor.

---

## 8. Output discoverability

**Friction: ANNOYING.**

The audit output lives at `<output_root>/<repo>/<DATE>-<run_id_short>/` (README §"Where reports go"). The path is **not printed at the end** of a headless run.

`HeadlessSubscriber._format_run_complete` (`headless_subscriber.py:127-143`) prints:

```
--- Run complete ---
Duration: 3m12s
Files: 14 audited, 0 errors, 0 skipped
Findings: HIGH=2 MEDIUM=5 LOW=3 HEALTHY=4
Exit status: success
```

No audit_dir. The user has to either:
- remember the run_id from `RunStart` (printed as `(run abc12345)`) and grep `<output_root>` for it, or
- look at the `audit.log`, or
- find the most recent dir under `<output_root>`.

The TUI Monitor screen does show the path, but headless mode (which is what
scheduled tasks and CI use) does not.

### Fix

Two changes:

1. **Add `audit_dir` to `RunComplete`** (`events.py:280-284`). Backward-compat:
   default to empty string. Effort: trivial. (Note: `RunStart` already carries
   `audit_dir` — alternatively, capture it in `HeadlessSubscriber` from
   `RunStart` and emit it in `_format_run_complete`.)

2. **Print the path explicitly:**

   ```
   --- Run complete ---
   Duration: 3m12s
   Files: 14 audited, 0 errors, 0 skipped
   Findings: HIGH=2 MEDIUM=5 LOW=3 HEALTHY=4
   Exit status: success

   Reports:
     combined report   E:/senex-audits/pensiv/2026-04-29-abc12345/combined.md
     handoff           E:/senex-audits/pensiv/2026-04-29-abc12345/claude-handoff.md
     findings (JSON)   E:/senex-audits/pensiv/2026-04-29-abc12345/findings.json
   Open with:  senex view E:/senex-audits/pensiv/2026-04-29-abc12345
   ```

Impact analysis required before editing `HeadlessSubscriber._format_run_complete`:
run `gitnexus_impact({target: "_format_run_complete", direction: "upstream"})`.
Likely d=1 = test suites that snapshot stdout; will need a test update.

Effort: **small** (~1 hour).

---

## 9. Resume UX after a crash

**Friction: ANNOYING.**

When an audit crashes mid-run (Ctrl+C, OOM, container fell over, SIGTERM from
Task Scheduler), there is **no user-facing hint** that `--resume` exists or what
to type next.

Searched for any "to resume" / "continue with" / "--resume" hints in the runtime
code paths: only present in `cli.py:141` (the `--resume` flag's help text) and
`cli.py:297` (the `--resume`/`--nightly` mutex error). Nothing on crash exit.

### Fix

In `cli_audit.py` and `cli_handlers.py` cmd_audit dispatcher:
- on `KeyboardInterrupt` → exit 130 → before exit, print:
  ```
  Audit interrupted. Resume with:
    senex audit <repo> --resume
  ```
- on partial success / per-file errors → print the same hint at the end of the
  headless run summary.

Note: `senex audit --resume` resolves the latest unfinished audit by config_hash
match, so the user does not need the audit_dir explicitly — but printing the dir
removes ambiguity when multiple repos are configured.

Also: `_run_nightly` (`cli_audit.py:427-454`) catches per-repo failures and prints
them but does NOT suggest resume per repo. Same hint should apply.

Effort: **trivial** (~30 min).

---

## 10. Model swap UX

**Friction: ANNOYING.** Today, switching from Qwen3-8B-AWQ to a different model
requires:

1. Edit `infra/sglang/.env` → change `SGLANG_MODEL=...`
2. Restart the SGLang container: `bash infra/sglang/sglang.sh restart`
3. Wait for the new model to download (5–15 min for first time)
4. Edit `senex.config.toml` → change `[lmstudio].model = ...`
5. Possibly adjust `[lmstudio].context_window` if the new model has a different cap
6. Run `senex audit`

Two files to keep in sync, one container restart, one wizard re-prompt. The
wizard partially mitigates this (`cli_wizard.py:328-338`) by using the served
model when config drifts, but only after `.env` + restart.

### Recommendation

Single source of truth: drive `SGLANG_MODEL` *from* `senex.config.toml`. Two
implementation options:

**Option A (recommended): the senex CLI writes the .env from the toml.**

A new `senex sglang sync` subcommand (or fold into `senex setup`) reads
`[lmstudio].model`, `[lmstudio].context_window`, `[lmstudio.sglang]` from the
TOML and rewrites `infra/sglang/.env` atomically. Then `_ensure_managed_container_up`
gains a `restart_if_changed=True` option that compares the running container's
model id to the configured one and restarts on mismatch.

Effort: **medium** (~half a day). Test surface: read `.env`, write atomically,
hash compare.

**Option B: skip the .env entirely, pass `--env-file` overrides on compose up.**

`infra/sglang/docker-compose.yml` already uses `${SGLANG_MODEL:-Qwen/Qwen3-8B-AWQ}`
defaults, so we could remove the `.env` from the contract entirely and pass
`-e SGLANG_MODEL=<from toml>` via `docker compose run`. Cleaner but breaks anyone
who hand-edits the .env today.

Effort: **medium**. Risk: backward incompat.

I recommend Option A.

### Quick win for v1

Even before Option A, a `senex doctor` check that diffs `.env` `SGLANG_MODEL` vs
`[lmstudio].model` and prints a one-line `WARN: model id drift between
infra/sglang/.env and senex.config.toml — fix one or the other` would catch
~80% of "I changed the model and now it doesn't work" support requests.

Effort: **trivial** (~30 min).

---

## Findings — consolidated triage

| # | Finding | Friction | Effort | Priority |
|--:|---|---|---|---|
| 1 | README still describes LM Studio + Gemma path; live binary uses SGLang + Qwen3 | BLOCKER | small | **P0** |
| 2 | Docker + WSL2 not mentioned in README install steps | BLOCKER | small (doc only) | **P0** |
| 3 | `[output].root` defaults to `E:/senex-audits` (Justin-specific) | BLOCKER | trivial | **P0** |
| 4 | `[[repos]].path = "E:/pensiv"` in example (Justin-specific) | annoying | trivial | **P0** |
| 5 | First-run docker pull (~15 GB) has no progress affordance; 180s timeout will fire | BLOCKER | small (heartbeat + better timeout) | **P0** |
| 6 | No `senex init` to bootstrap config + check deps | annoying-blocker | medium | **P1** |
| 7 | `senex doctor` does not check Docker, WSL, Node, semgrep, GPU, HF disk space | blocker on Windows | small-medium | **P1** |
| 8 | Audit dir not printed at end of headless run | annoying | small | **P1** |
| 9 | No resume hint after crash / Ctrl+C | annoying | trivial | **P1** |
| 10 | Model id drift between `senex.config.toml` and `infra/sglang/.env` is silent | annoying | trivial (warn) → medium (sync) | **P1** for warn, **P2** for sync |
| 11 | `check_lifecycle_backend` / `check_lms_reachable` error messages don't mention Docker/WSL/sglang.sh | annoying | small | **P1** |
| 12 | `check_gitnexus_index` warn doesn't tell user to run `npx gitnexus analyze` | minor | trivial | **P2** |
| 13 | `[walker].max_files = 50000` in example will fail on real repos | annoying | trivial | **P1** |
| 14 | Example config `strict_json_schema=false` contradicts live default `true` | minor | trivial | **P2** |
| 15 | No `quickstart.md`; README front-loads configuration before first success | minor-annoying | small | **P1** |
| 16 | No PyPI package data for `infra/` — `pip install senex` won't include the SGLang compose file | blocker (when published) | small | **P1** |
| 17 | Editor plugin | minor | medium-large | **P3** (out of scope v1) |

---

## Recommended v1 publishability sequence

P0 (must ship before announcing):

1. Fix README backend mismatch (LM Studio → SGLang; Gemma → Qwen3-8B-AWQ).
2. Add Docker + WSL2 install section to README.
3. Replace Justin-specific paths in `senex.config.toml.example` and the wizard's
   default suggestions with `~/senex-audits` and `<set me>`.
4. Add `infra/` to `pyproject.toml` package-data (so `pip install senex` ships
   the SGLang compose file).
5. Print the audit_dir at the end of headless runs.
6. Surface a heartbeat during long docker pulls; bump `startup_timeout_seconds`
   default for first-run scenarios (or detect cold-pull and extend automatically).

P1 (next wave, before broad public adoption):

1. Implement `senex init` (interactive config + dep check).
2. Extend `senex doctor` with `--install` install-time check set.
3. Print resume hint on Ctrl+C / per-file failures.
4. Rewrite the lifecycle-backend / inference-server preflight error messages with
   actionable next-step text.
5. Write `docs/quickstart.md` (5-minute path).
6. Add a `senex doctor` check for `.env` / `senex.config.toml` model-id drift.

P2:

1. `senex sglang sync` (single source of truth).
2. Doctor checks for HF cache disk space, GPU VRAM.

P3:

1. Editor plugin (post-v1).

---

## Closing note

Most of the heavy lifting is already done — the wizard's backend auto-detection,
`_ensure_managed_container_up`, and the SGLang compose file are an excellent
foundation. The gap to publishability is doc + onboarding ergonomics, not
plumbing. The biggest user-facing wins will come from `senex init`, the README
realignment, and printing the audit_dir at the end of every run.

---

### File-path index (absolute)

- `E:\senex\README.md` — needs realignment to SGLang/Qwen3 + Docker/WSL prerequisites.
- `E:\senex\senex.config.toml.example` — Justin-specific paths, wrong base_url, wrong model, missing `[lmstudio.sglang]` block.
- `E:\senex\senex\cli.py` — entry point; add `init` subparser.
- `E:\senex\senex\cli_audit.py` — `_ensure_managed_container_up` (heartbeat); `cmd_audit` (resume hint on KeyboardInterrupt).
- `E:\senex\senex\cli_doctor.py` — extend with install-time checks.
- `E:\senex\senex\cli_wizard.py` — already strong; the model-probe error messages here are the bar.
- `E:\senex\senex\phases\preflight.py` — rewrite `check_lifecycle_backend`, `check_lms_reachable`, `check_model_loaded_or_loadable`, `check_gitnexus_index` messages to be actionable.
- `E:\senex\senex\subscribers\headless_subscriber.py` — `_format_run_complete` to print audit_dir.
- `E:\senex\senex\events.py` — optional: add `audit_dir` to `RunComplete`.
- `E:\senex\senex\config.py` — `OutputCfg` default → `~/senex-audits`; `SglangCfg` exposed in example template.
- `E:\senex\infra\sglang\docker-compose.yml` — fine as-is.
- `E:\senex\infra\sglang\.env` — model id source-of-truth question (Option A vs B above).
- `E:\senex\pyproject.toml` — extend `package-data` to ship `infra/sglang/*` for PyPI installs.
- `E:\senex\scripts\setup.ps1` — could be dropped or replaced by `senex init` once that ships.
