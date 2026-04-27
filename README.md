# senex

Local-LLM repository audit tool. Audits a target repository file by file using a thinking-MoE
model running in LM Studio, with GitNexus as background graph awareness. Produces per-file
Markdown reports, a combined run report, a `findings.json` machine index, and a Claude Code
handoff artifact.

senex is a local-first tool: source code never leaves the machine, no telemetry, secret
redaction is applied to every persisted artifact. See "Security" below.

---

## Overview

senex pairs a small local thinking model (default: `google/gemma-4-26b-a4b` in LM Studio)
with a 6-tool framework (`gitnexus_query`, `gitnexus_context`, `gitnexus_impact`, `read_file`,
`grep`, `search_code`), context compaction safety, and a streaming Textual TUI. Each file
in the audited repo gets one model turn (with optional tool calls). Findings are aggregated
into a stable `findings.json` and a human-readable `combined.md`; a final `claude-handoff.md`
summarizes hot spots for downstream work in Claude Code.

## Prerequisites

- **Windows 10/11** (Linux/macOS supported but the scheduled-task scripts are Windows-only).
- **Python 3.11+** (3.13 supported and recommended).
- **LM Studio** — download from <https://lmstudio.ai>. Load `google/gemma-4-26b-a4b`
  before running the audit (or set `[lmstudio.lifecycle].auto_load = true` for senex
  to load it for you).
- **GitNexus** (optional but strongly recommended) — provides the graph context block
  prepended to every per-file prompt. Install with `npx gitnexus init` in the audited
  repo. Without it, senex still runs but with degraded context.
- **`gh`** (optional) — for the v1.0.0 release flow only.

## Install

### Windows (one-liner)

```powershell
git clone https://github.com/parthalon025/senex.git
cd senex
.\scripts\setup.ps1
```

`setup.ps1` creates `.venv`, runs `pip install -e .`, verifies `senex --version`, and
prints next steps.

### Manual (any platform)

```bash
git clone https://github.com/parthalon025/senex.git
cd senex
python -m venv .venv
.venv\Scripts\activate     # PowerShell: . .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
senex --version
```

## Configure

Copy the example to the working file and edit:

```powershell
copy senex.config.toml.example senex.config.toml
notepad senex.config.toml
```

Minimum edits to ship:

1. **`[lmstudio].base_url`** — leave at `http://localhost:1234/v1` unless you bind LM
   Studio elsewhere.
2. **`[lmstudio].model`** — the loaded model id (default: `google/gemma-4-26b-a4b`).
3. **`[[repos]]`** — at least one `name` + absolute `path` entry. `senex audit --nightly`
   iterates every entry; ad-hoc runs target a single repo by argument.

## Run

```powershell
senex audit                                 # interactive launcher wizard
senex audit C:\path\to\your\repo            # direct TUI launch
senex audit C:\path\to\your\repo --no-tui   # headless (stdout progress)
senex audit --nightly                       # iterate every [[repos]]
senex audit C:\path\to\your\repo --resume   # continue an interrupted run
senex audit --no-wizard                     # error: requires a positional path
```

Run `senex audit` with no path to launch the interactive wizard. The wizard
walks you through repo selection (from `[[repos]]`, on-disk discovery, or a
custom path), model selection (probed live from `GET /v1/models`), and a
handful of yes/no prompts before handing off to the TUI or headless flow.
Pass `--no-wizard` to disable it (a positional path is then required) — useful
for scripts and CI.

Inside the TUI Launcher screen, the **Scan disk for repos** button performs
the same on-disk discovery without leaving the UI — found repos are appended
to the configured-repos dropdown, deduplicated against any already-listed
paths. Tune the scan via `[ui].scan_root` (defaults to your home directory)
and `[ui].scan_max_depth` (default 6) in `senex.config.toml`.

Scheduled-task entrypoint for Windows Task Scheduler: `scripts\run_senex.bat`. It
activates the venv and runs `python -m senex audit --nightly`.

## View results

```powershell
senex view                                 # latest audit (auto-detect)
senex view <audit-dir>                     # specific run
senex view <audit-dir> --speed 10.0        # 10x replay
```

The replay does not call LM Studio; it reads `events.jsonl` from the audit dir and
re-renders the Monitor screen offline.

## Diagnostics

```powershell
senex doctor                               # check every [[repos]] entry
senex doctor C:\path\to\your\repo          # single repo
senex doctor --json                        # CI-friendly output
senex lifecycle status                     # loaded models + runlock holders
senex lifecycle clear-locks                # prune stale-PID holders
senex config show C:\path\to\your\repo     # resolved config (TOML)
senex aggregate <audit-dir>                # re-run Phase 5 after a crash
```

Run `senex doctor` once after install. Every check should return `pass` or `warn`;
`fail` exits non-zero and prints the failing check's diagnostic.

## Where reports go

```
E:\senex-audits\<repo-name>\<DATE>-<run_id_short>\
├── combined.md                # human-readable run report
├── findings.json              # stable machine index (schema-validated)
├── claude-handoff.md          # downstream summary for Claude Code
├── events.jsonl               # canonical event stream (replay source)
├── checkpoint.json            # resume state machine
├── audit.log                  # human-readable log
├── config.snapshot.toml       # resolved config (TOCTOU defense)
├── findings.partial.jsonl     # streaming append (input to Phase 5)
└── <relpath>.md               # per-file report (one per audited file)
└── <relpath>.thinking.md      # per-file thinking trace (when enabled)
```

The output root is configurable via `[output].root` (default: `E:\senex-audits`).

## Security

- **Source stays local.** senex's only outbound network calls are to `localhost`
  (LM Studio HTTP) and local subprocesses (`npx gitnexus`, `lms`). No telemetry,
  no third-party APIs.
- **Loopback-only by default.** Non-loopback `[lmstudio].base_url` requires explicit
  `[lmstudio].allow_non_loopback = true`.
- **Secret redaction is on by default.** Every persisted artifact (`<file>.md`,
  `combined.md`, `claude-handoff.md`, `events.jsonl`, `audit.log`, `config.snapshot.toml`)
  passes through the SecretRedactor: PEM blocks, JWTs, AWS access keys, GitHub PATs,
  `sk-...` style LLM keys, and generic `KEY=value` env-style secrets are masked.
  Disable only for transient debugging via `[output].redact_secrets = false`.
- **Trust boundaries.** Audited source is wrapped in `<UNTRUSTED_FILE_CONTENT>...
  </UNTRUSTED_FILE_CONTENT>` before LLM ingestion; the system prompt instructs the
  model to disregard directives within. Tool-call inputs are path-validated and
  regex-capped per spec section 5.11.4.
- **Checkpoint integrity.** `checkpoint.json` carries an HMAC over the resume hash
  bundle (`config_hash`, `prompt_hash`, `model_fingerprint`, `tool_pack_hash`,
  `lens_version`). Resume is refused on hash drift unless `--allow-mixed-resume`
  is set.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `doctor` reports `lmstudio_reachable: fail` | LM Studio not running | Launch LM Studio and load the configured model. |
| `doctor` reports `model_loaded: fail` | Wrong model id | Update `[lmstudio].model` in `senex.config.toml`. |
| `doctor` reports `gitnexus_index: warn` | Repo not indexed | `cd <repo> && npx gitnexus analyze`. Audit still runs without it. |
| Audit aborts immediately | Bad config / repo path | Run `senex doctor` first; fix the failing checks. |
| Resume rejected: hash mismatch | Config or prompts changed since the original run | Use `--allow-mixed-resume` (advanced) or start a fresh run. |
| Stale runlock blocks startup | Prior crash left a holder | `senex lifecycle clear-locks` (use `--force` only for live PIDs). |
| `combined.md` missing after run | Aggregation phase crashed | `senex aggregate <audit-dir>` to retry. |

## License & contribution

License: TBD (see `LICENSE` at repo root once added). Contributions: please open
an issue or PR; conventions live in `docs/superpowers/conventions.md`.
