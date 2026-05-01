# senex

> Local-LLM repository audit tool. Reviews a target repo file-by-file with a
> thinking-MoE model running on your own GPU and emits per-file Markdown
> reports, a combined run report, a machine-readable `findings.json`, and a
> Claude Code handoff artifact.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

<!-- TUI screencast goes here once recorded:
     [![asciicast](https://asciinema.org/a/PLACEHOLDER.svg)](https://asciinema.org/a/PLACEHOLDER) -->

`![senex TUI screencast — placeholder]`

## What it does

- Audits each source file in a repo against a configurable lens (default:
  correctness) using a local thinking model — no cloud calls, no telemetry.
- Aggregates findings into a stable `findings.json` (schema-validated), a
  human-readable `combined.md`, and a `claude-handoff.md` for downstream work.
- Streams progress through a Textual TUI; replay any past run offline from
  its `events.jsonl`.

---

## Install

```bash
pip install senex-audit
```

> The PyPI distribution is named **`senex-audit`**; the CLI command stays
> `senex`. Required Python: 3.11+.

### Windows prerequisites

senex's default backend (SGLang) runs in Docker. On Windows you need:

- **WSL2** with a Linux distro (Ubuntu recommended) — `wsl --install Ubuntu`.
- **Docker** reachable from WSL2 — Docker Desktop with the WSL2 backend
  enabled, or `docker` installed inside the WSL distro itself.
- An NVIDIA GPU with up-to-date drivers (the SGLang container binds the GPU
  via `--gpus all`).

Linux/macOS users only need Docker + an NVIDIA GPU; senex skips the WSL
shim automatically when `[lmstudio.sglang].via_wsl = false`.

### Run from a clone (development)

```bash
git clone https://github.com/parthalon025/senex.git
cd senex
python -m venv .venv
. .venv/Scripts/activate     # on Linux/macOS: source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
senex --version
```

---

## Quickstart (5 minutes)

```bash
# 1. Install
pip install senex-audit

# 2. Bring up the local inference server (one-time, runs in the background)
bash senex/infra/sglang/sglang.sh up        # WSL2 / Linux / macOS

# 3. Run an interactive audit — the wizard walks you through repo + model
senex audit

# Or skip the wizard and target a repo directly:
senex audit /path/to/your/repo
```

The first run drops a `senex.config.toml` in the working directory if one
isn't found; tweak it before subsequent runs.

> A future `senex init` command will scaffold the config interactively.
> Until then, copy `senex.config.toml.example` from the repo or let the
> wizard write the defaults.

---

## Run

```bash
senex audit                                 # interactive launcher wizard
senex audit /path/to/your/repo              # direct TUI launch
senex audit /path/to/your/repo --no-tui     # headless (stdout progress)
senex audit --nightly                       # iterate every [[repos]]
senex audit /path/to/your/repo --resume     # continue an interrupted run
```

Run `senex audit` with no path to launch the interactive wizard. The wizard
walks you through repo selection (from `[[repos]]`, on-disk discovery, or a
custom path), model selection (probed live from `GET /v1/models`), and a
handful of yes/no prompts before handing off to the TUI or headless flow.
Pass `--no-wizard` to disable it (a positional path is then required) —
useful for scripts and CI.

Inside the TUI Launcher screen, the **Scan disk for repos** button performs
on-disk discovery without leaving the UI. Tune the scan via `[ui].scan_root`
(defaults to your home directory) and `[ui].scan_max_depth` (default 6) in
`senex.config.toml`.

For Windows Task Scheduler, `scripts\run_senex.bat` activates the venv and
runs `python -m senex audit --nightly`.

## View results

```bash
senex view                                 # latest audit (auto-detect)
senex view <audit-dir>                     # specific run
senex view <audit-dir> --speed 10.0        # 10x replay
```

The replay does not call the inference backend; it reads `events.jsonl`
from the audit dir and re-renders the Monitor screen offline.

## Diagnostics

```bash
senex doctor                               # check every [[repos]] entry
senex doctor /path/to/your/repo            # single repo
senex doctor --json                        # CI-friendly output
senex lifecycle status                     # loaded models + runlock holders
senex lifecycle clear-locks                # prune stale-PID holders
senex config show /path/to/your/repo       # resolved config (TOML)
senex aggregate <audit-dir>                # re-run Phase 5 after a crash
```

Run `senex doctor` once after install. Every check should return `pass` or
`warn`; `fail` exits non-zero and prints the failing diagnostic.

---

## Where reports go

Default: `<user-home>/senex-audits/`. Override via `[output].root`.

```
<output.root>/<repo-name>/<DATE>-<run_id_short>/
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

senex does **not** prune old run directories. Users are responsible for
pruning `<output.root>` per their own retention policy. (An earlier
`output.retention_days` field was advertised but never enforced; it has
been removed in favour of explicit user control.)

---

## Configuration overview

A documented configuration template lives at
[`senex.config.toml.example`](senex.config.toml.example). Key sections:

- `[output]` — where audit run dirs are written; secret-redaction toggle.
- `[lens]` — which lens to apply; minimum priority filter.
- `[walker]` — file discovery: extensions, excludes, gitignore handling.
- `[lmstudio]` — backend endpoint, model id, sampling, thinking budget,
  tool-loop limits.
- `[lmstudio.sglang]` — Docker container management for the SGLang stack.
- `[ui]` — TUI launcher scan settings.
- `[[repos]]` — named repos for `--nightly` iteration.

CLI flags and TUI overrides take precedence over file settings; see
`senex config show <repo>` for the fully-resolved view.

---

## Privacy

senex is a **local-only** tool. The full data-flow:

- **No telemetry.** senex makes no outbound HTTP calls except to localhost
  (the inference server) and local subprocesses (`docker compose`,
  `npx gitnexus`, `lms`).
- **Loopback enforced by default.** Non-loopback `[lmstudio].base_url`
  requires explicit `[lmstudio].allow_non_loopback = true`. The preflight
  phase rejects non-loopback URLs otherwise.
- **Source code never leaves your machine.** The model is local; audited
  files are read from disk, framed in a trust boundary, and sent to
  `localhost`.
- **Audit retention is your responsibility.** senex writes to
  `<output.root>/...` and never prunes. Set up your own cron/Task
  Scheduler job if you have a retention policy.

Audited content is wrapped in `<UNTRUSTED_FILE_CONTENT>...
</UNTRUSTED_FILE_CONTENT>` before LLM ingestion; the system prompt
instructs the model to disregard directives within. Tool-call inputs are
path-validated against the audited repo root.

---

## Limitations

- **Advisory output.** Findings are model-generated. They reflect what
  a small thinking-MoE model believes about the file given its context
  window — they are **not** a formal audit and not a replacement for
  human review.
- **Quality depends on the model.** Default settings target consumer
  GPUs (Qwen3-8B class). Larger models produce noticeably better
  findings; smaller / quantized ones produce more false positives.
- **EU AI Act minimal-risk disclosure.** Under the EU AI Act, senex
  output is advisory information generated by an AI system. **Do not
  gate CI/CD on senex findings without human-in-the-loop review.**
  Treat findings as triage hints, not gates.
- **Coverage is per-file, not whole-repo.** Cross-cutting findings come
  from a separate aggregation pass and are best-effort.

---

## Security

- The vulnerability disclosure policy lives in [SECURITY.md](SECURITY.md).
- Default network posture is loopback-only — see Privacy above.
- Persisted artifacts pass through `SecretRedactor`, which masks PEM
  blocks, JWTs, AWS access keys, GitHub PATs, OpenAI-style `sk-...` keys,
  and generic `KEY=value` env-style secrets. Disable only for transient
  debugging via `[output].redact_secrets = false`.
- `checkpoint.json` carries a hash bundle (`config_hash`, `prompt_hash`,
  `model_fingerprint`, `tool_pack_hash`, `lens_version`) used to refuse
  resume on drift. Pass `--allow-mixed-resume` to override.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

---

## Subprocess tooling and licenses

senex shells out to a few external tools at runtime. None of them are
bundled into the senex distribution; they are user-installed. Their
licenses are noted here for transparency:

- **SGLang** — Apache-2.0. Default local inference server. No license
  obligations beyond standard Apache-2.0 attribution; the bundled
  `senex/infra/sglang/docker-compose.yml` invokes the upstream image.
- **GitNexus** — **PolyForm Noncommercial 1.0.0**. Optional graph
  context provider invoked via `npx gitnexus`. **Commercial use of
  GitNexus requires a separate license from the GitNexus maintainer.**
  senex's own Apache-2.0 license is unaffected: subprocess invocation
  does not contaminate senex's license, but commercial users must
  arrange their own GitNexus license before using `[tools.gitnexus_*]`.
- **semgrep** — LGPL-2.1. Optional static-analysis tool invoked via
  subprocess. LGPL-2.1 obligations apply to semgrep itself, not to
  senex.

See [NOTICE](NOTICE) for the canonical attribution list.

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0    | Success |
| 1    | Generic error (unhandled exception) |
| 2    | Bad CLI usage (argparse) |
| 3    | Preflight failure (`senex doctor` reports a `fail`) |
| 4    | Model load / lifecycle failure |
| 5    | Resume rejected (hash drift; use `--allow-mixed-resume` if appropriate) |
| 6    | Run aborted by user (Ctrl-C / TUI Quit) |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `doctor` reports `lmstudio_reachable: fail` | Inference server not running | `bash senex/infra/sglang/sglang.sh up`, or load the configured model in LM Studio. |
| `doctor` reports `model_loaded: fail` | Wrong model id | Update `[lmstudio].model` in `senex.config.toml` to a value listed by `GET /v1/models`. |
| `doctor` reports `gitnexus_index: warn` | Repo not indexed | `cd <repo> && npx gitnexus analyze`. Audit still runs without it. |
| Audit aborts immediately | Bad config / repo path | Run `senex doctor` first; fix the failing checks. |
| Resume rejected: hash mismatch | Config or prompts changed since the original run | Use `--allow-mixed-resume` (advanced) or start a fresh run. |
| Stale runlock blocks startup | Prior crash left a holder | `senex lifecycle clear-locks` (use `--force` only for live PIDs). |
| `combined.md` missing after run | Aggregation phase crashed | `senex aggregate <audit-dir>` to retry. |

---

## License

senex is licensed under the [Apache License, Version 2.0](LICENSE).
Third-party attribution lives in [NOTICE](NOTICE). Vulnerability
reporting policy lives in [SECURITY.md](SECURITY.md).

Contributions welcome — open an issue or PR. Conventions live in
`docs/superpowers/conventions.md`.
