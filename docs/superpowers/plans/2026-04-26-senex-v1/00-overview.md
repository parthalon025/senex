# senex v1 Implementation Plan — Overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `senex` v1 — a standalone Python tool that audits a target repo file-by-file using a local LM Studio thinking-MoE model (`google/gemma-4-26b-a4b`), with GitNexus graph awareness, integrated Textual TUI, structured Markdown reports, a Claude Code handoff artifact, and full live validation.

**Architecture:** Single-process integrated Textual app. The auditor is a pure async coroutine (`run_audit()`) emitting events via an asyncio-queue event bus. Three always-on subscribers: DiskWriter (write-ahead persistence), MetricsCollector, and TUI/Headless. LM Studio integration uses streaming chat completions with structured-output JSON schemas, a 6-tool framework (gitnexus_query/context/impact, read_file, grep, search_code), context compaction safety net, and full model lifecycle management (auto-load + auto-unload via runlock). Output: per-file Markdown reports + `findings.json` + combined report + `claude-handoff.md` per audit-dir at `E:\senex-audits\<repo>\<DATE>-<run_id_short>\`.

**Tech Stack:** Python 3.11+, `pydantic` v2 (config + schemas), `tomllib` (TOML parsing), `httpx` (LM Studio HTTP client) or `openai` SDK (`>= 1.50, < 2.0`), `lmstudio` Python SDK (lifecycle, optional with `lms` CLI fallback), `textual` (TUI), `tiktoken` (token counting), `portalocker` (runlock advisory file locking), `detect-secrets` (redaction regexes), `regex` (ReDoS-safe regex with timeout), `pytest` + `pytest-asyncio` (testing).

**Spec:** `docs/superpowers/specs/2026-04-26-senex-audit-tool-design.md` — 1809 lines. Reference for any contract details not duplicated here.

---

## Reading order

Start with [`m1-foundation.md`](m1-foundation.md). Each subsequent milestone declares its prerequisites; do not start a milestone until all prerequisites are complete. The dependency graph below is strict — Layer N depends only on Layers 0..N-1.

---

## File Structure

```
E:\senex\
├── senex/                              # Python package
│   ├── __init__.py                     # version, package metadata
│   ├── cli.py                          # entrypoints: audit | view | doctor | aggregate | config | lifecycle
│   ├── config.py                       # TOML loader; pydantic v2 models; deep-merge resolution
│   ├── events.py                       # event bus + 30+ event types (pydantic models)
│   ├── walker.py                       # repo file discovery (gitignore + ext + per-repo + size cap + symlink guard)
│   ├── secret_redactor.py              # detect-secrets-class regex redactor
│   ├── checkpoint.py                   # resume state machine on disk
│   ├── runlock.py                      # interprocess refcount file lock
│   ├── lmstudio_client.py              # OpenAI-compatible client + tool loop + streaming + thinking extraction
│   ├── lmstudio_lifecycle.py           # load/unload via lmstudio SDK or `lms` CLI
│   ├── compaction.py                   # context compaction trigger + execution
│   ├── graph_awareness.py              # PRE-flight graph context block builder (gitnexus CLI)
│   ├── auditor.py                      # async coroutine; orchestrates one full run
│   ├── renderer.py                     # structured response → markdown report
│   ├── findings_partial.py             # streaming append to findings.partial.jsonl
│   ├── findings_aggregator.py          # finalize findings.json from partial + crosscut
│   ├── cross_cutting.py                # post-pass: per-file findings → repo-wide themes
│   ├── handoff.py                      # writes claude-handoff.md
│   ├── lens.py                         # Lens object: loads lens/<name>/ directory
│   ├── phases/
│   │   ├── __init__.py
│   │   ├── base.py                     # Phase protocol
│   │   ├── preflight.py
│   │   ├── discovery.py
│   │   ├── file_audit.py               # per-file loop
│   │   ├── crosscut.py
│   │   └── aggregate.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py                 # tool registration + OpenAI schema export
│   │   ├── safety.py                   # path validation, regex cap, ANSI strip
│   │   ├── loop.py                     # bounded tool-call loop controller
│   │   ├── gitnexus_query.py
│   │   ├── gitnexus_context.py         # the TOOL (distinct from senex/graph_awareness.py)
│   │   ├── gitnexus_impact.py
│   │   ├── read_file.py
│   │   ├── grep.py
│   │   └── search_code.py
│   ├── subscribers/
│   │   ├── __init__.py
│   │   ├── base.py                     # Subscriber protocol
│   │   ├── disk_writer.py
│   │   ├── tui_subscriber.py
│   │   ├── headless_subscriber.py
│   │   └── metrics.py
│   ├── schema/
│   │   ├── audit_response.schema.json
│   │   ├── crosscut_response.schema.json
│   │   ├── compaction_response.schema.json
│   │   ├── findings_index.schema.json
│   │   ├── events.schema.json
│   │   └── checkpoint.schema.json
│   ├── prompts/
│   │   ├── system_senior_dev.md
│   │   ├── per_file_user.md
│   │   ├── cross_cutting.md
│   │   ├── compaction.md
│   │   ├── claude_handoff.md
│   │   ├── lang_python.md
│   │   ├── lang_typescript.md
│   │   ├── lang_rust.md
│   │   ├── lang_go.md
│   │   └── lang_csharp.md
│   ├── lens/
│   │   └── correctness/
│   │       ├── lens.toml               # lens metadata
│   │       └── tools.toml              # enabled tools for this lens
│   └── tui/
│       ├── __init__.py
│       ├── app.py                      # SenexApp (Textual)
│       ├── launcher.py                 # launcher screen
│       ├── monitor.py                  # monitor screen
│       └── widgets/
│           ├── progress.py
│           ├── findings_panel.py
│           ├── current_file.py
│           ├── error_banner.py
│           └── status.py
├── scripts/
│   ├── run_senex.bat                   # Windows scheduler entrypoint
│   └── setup.ps1                       # first-time setup (venv + deps)
├── tests/
│   ├── unit/                           # mirrors senex/ structure
│   ├── golden/                         # renderer fixture comparisons
│   ├── recorded/                       # captured LMS responses
│   ├── tui/                            # Textual Pilot smoke
│   ├── live/                           # @live marker — manual only
│   └── fixtures/
│       ├── repos/tiny_python/          # 3-5 trivially-flawed python files
│       ├── lms_responses/              # captured LMS JSON by hash
│       ├── gitnexus_outputs/           # captured `npx gitnexus` JSON
│       └── rendered/                   # golden markdown
├── senex.config.toml.example
├── pyproject.toml
├── requirements.txt
├── README.md
├── CLAUDE.md                           # already in place from gitnexus init
└── .claude/                            # already in place
```

---

## Module Dependency Graph

Implementation order is driven by dependencies. Lower-numbered modules ship first; higher-numbered modules import them.

```
Layer 0 (no internal deps):
  config, events, secret_redactor, lens, runlock

Layer 1 (use Layer 0):
  checkpoint, walker, graph_awareness

Layer 2 (use Layer 0-1):
  lmstudio_lifecycle, compaction (trigger logic), tools/safety, tools/registry

Layer 3 (use Layer 0-2):
  lmstudio_client (uses lifecycle, compaction, registry, events, secret_redactor)
  tools/loop (uses lmstudio_client, registry)
  tools/* (the 6 tool implementations — use safety, lens, registry)

Layer 4 (use Layer 0-3):
  renderer, findings_partial, cross_cutting, handoff, findings_aggregator

Layer 5 (orchestration):
  phases/* (each phase composes the lower layers)
  auditor.run_audit() (coordinates phases via the bus)

Layer 6 (UI):
  subscribers/* (consume events; disk_writer is critical)
  tui/* (depends on event types only)

Layer 7 (entry):
  cli.py (composes everything)
  scripts/run_senex.bat
```

---

## Milestone Roadmap

| # | Milestone | Layers | Deliverable | Tasks | File |
|---|---|---|---|---|---|
| M1 | Foundation | 0 | Project boots, config loads, events emit | 1.1–1.8 | [m1-foundation.md](m1-foundation.md) |
| M2 | Walker + Graph Awareness | 1 | Discovery phase works | 2.1–2.5 | [m2-walker-graph.md](m2-walker-graph.md) |
| M3 | LM Studio Integration | 2-3 | Single chat completion + structured output | 3.1–3.10 | [m3-lmstudio.md](m3-lmstudio.md) |
| M4 | Lifecycle + Runlock | 2 | Model load/unload + concurrent-safe locks | 4.1–4.6 | [m4-lifecycle.md](m4-lifecycle.md) |
| M5 | Tool Framework | 3 | All 6 tools wired + bounded loop | 5.1–5.10 | [m5-tools.md](m5-tools.md) |
| M6 | Compaction | 3 | Context compaction triggers + executes | 6.1–6.4 | [m6-compaction.md](m6-compaction.md) |
| M7 | Renderer + Aggregator | 4 | Per-file MD + findings.json + combined report | 7.1–7.8 | [m7-renderer.md](m7-renderer.md) |
| M8 | Phases + Auditor | 5 | End-to-end run from CLI (no TUI) works | 8.1–8.7 | [m8-phases-auditor.md](m8-phases-auditor.md) |
| M9 | Subscribers + TUI | 6 | Live TUI launcher + monitor | 9.1–9.9 | [m9-tui.md](m9-tui.md) |
| M10 | CLI + Validation | 7 | All subcommands; live gates 13a-13e green; v1.0.0 tag | 10.1–10.10 | [m10-cli-validation.md](m10-cli-validation.md) |

---

## Self-Review

**Spec coverage:** Every spec section has a corresponding milestone:
- §3.1, §3.2 → M1-M9 file layouts
- §4 (data flow) → M8 phases + auditor
- §5.0 Lens → M1 Task 1.5
- §5.1 prompts → M2 Task 2.4
- §5.4 schemas → M1 Task 1.8
- §5.4.1 crosscut schema → M1 Task 1.8 + M7 Task 7.4
- §5.5 LMStudioClient → M3
- §5.5.1 Compaction → M6
- §5.5.2 Lifecycle (incl. §5.5.2.7 Resume) → M4
- §5.6 Events → M1 Task 1.3
- §5.6.1 Bus semantics → M1 Task 1.3 + M9 subscribers
- §5.6.2 Command bus → M1 Task 1.3 + M9 Task 9.8
- §5.7 TUI → M9
- §5.8 Graph awareness → M2 Task 2.2
- §5.9 Walker → M2 Task 2.1
- §5.10 Secret redactor → M1 Task 1.4
- §5.11 Tool framework → M5
- §6 Config → M1 Task 1.2
- §6.1 Resolution → M1 Task 1.2
- §7 Output → M7
- §8 Error handling → M3 retry, M8 phase failures
- §8.1 Preflight → M8 Task 8.2
- §8.5 Resume → M8 Task 8.7 + M4 Task 4.3
- §9 Tests → distributed across all tasks
- §10 CLI → M10
- §11 Security → all tasks via SecretRedactor + path safety in M1, M2, M5
- §12 Future → not in scope (deferred)

**Placeholder scan:** No TBD, TODO, "implement later" found. Some sub-tasks summarize their step pattern when the same TDD shape repeats (Tasks 5.2-5.7, 8.3-8.6) — this is intentional to reduce plan length without losing information; agents implementing those tasks should follow the explicit pattern shown for the first instance.

**Type consistency:** Module names match across plan (e.g., `lmstudio_client.py` consistently; `graph_awareness.py` distinguished from `tools/gitnexus_context.py` per spec rename).

---

## Execution Handoff

Plan saved to `E:/senex/docs/superpowers/plans/2026-04-26-senex-v1/`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch fresh subagent per task; review between tasks; fast iteration. Best for the M5 tools (which are repetitive across 6 implementations) and M9 widgets.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`; batch execution with checkpoints for review. Best when many tasks share state and you want a single coherent context.

For senex's scale (~85 tasks across 10 milestones), recommend Subagent-Driven with Sonnet workers, batching by milestone (M1, M2, ... ) for review checkpoints. Each milestone file in this directory is self-contained — a subagent handed only that file plus the spec should have enough context to implement.
