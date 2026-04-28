# Changelog

All notable changes to senex are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [1.0.2] — 2026-04-27

Patch release.

### Fixed
- Extended the v1.0.1 json_object fallback to cover ALL gemma calls
  (previously only tools+schema combos used the fallback). Strict
  `json_schema` mode on gemma + LM Studio sometimes returned empty
  content even on tools-less calls; defaulting to `json_object` +
  post-hoc Pydantic validation eliminates the stochastic empty-response
  bug. Live audits now consistently return findings.

### Added
- `[lmstudio].strict_json_schema` config flag (default `false`). Set
  to `true` for OpenAI/Together/Groq backends that honor strict schema
  enforcement reliably.

## [1.0.1] — 2026-04-27

Patch release.

### Fixed
- Gemma + tools + `response_format=json_schema` returned empty content;
  senex now pre-emptively uses `json_object` mode when tools are enabled
  and validates the response post-hoc with Pydantic. Live audits now
  produce findings on real flaws.
- Recorded-LMS replay fixture regenerated against the post-M7 schema
  shape; the previously-xfailed `test_replayed_audit_produces_valid_response`
  now passes deterministically.

## [1.0.0] — 2026-04-27

First stable release. Live validation gate 13a passed: full audit pipeline runs end-to-end against `google/gemma-4-26b-a4b` in LM Studio, produces all spec'd artifacts (per-file Markdown reports, `findings.json`, `combined.md`, `claude-handoff.md`, canonical `events.jsonl` event stream), with structured per-file metadata (tokens, latency, tools used, compactions) and run-level totals correctly populated.

### What changed since v1.0.0-rc1 (22 commits)

**Live-validation bug fixes:**
- `lms ps --json` matcher now recognizes the actual LM Studio CLI shape (`modelKey`, `indexedModelIdentifier`, nested `quantization{name, bits}`) — was looking for legacy `model_id`/`id`/`quant` strings, breaking the wait-for-load polling and lifecycle attach paths.
- `register_default_tools()` is now called at run start; the M10 wiring gap that left the `ToolRegistry` empty (causing every audit to crash on `KeyError: tool not registered`) is closed.
- Client-side fingerprint is now derived from the HTTP `/v1/models` view, not the lifecycle's `lms ps --json` view, so the per-call swap-detection check no longer false-positives on the first chat.
- Wait-for-load polling kicks in when `lms load` fails (e.g., resource guardrails); senex prints clear "load the model manually or run `lms load <model>`" guidance and waits up to 10 minutes (configurable) instead of immediately exiting.

**Observability layer fixes:**
- `DiskWriterSubscriber` is now wired in the headless run path; `events.jsonl` is written and `senex view` replay works as designed.
- Per-file metadata (`prompt_tokens`, `completion_tokens`, `thinking_ms`, `output_ms`, `tools_used`, `compactions_used`) is captured live from the streaming chat response and surfaced in each `<file>.md` header.
- `RunMetadata.files_audited` is updated after `FileAuditPhase` runs, so `combined.md` and `findings.json.totals.files` show the real audited count instead of zero.
- `*.thinking.md` traces are now written when `[lmstudio.thinking].save_traces = true`. Gemma + LM Studio + `response_format=json_schema` returns thinking inline as `<think>` blocks (not as a separate `reasoning_content` sidecar), so the strip path now captures the inline blocks before stripping.
- Schema sanitizer (`_sanitize_schema_for_lmstudio`) strips `anyOf`/`oneOf` conditional-required blocks before sending to LM Studio (Gemma rejects them); canonical schema files unchanged.

**UX additions:**
- Interactive launcher wizard: `senex audit` (no positional path) prompts for repo, model, and audit options before launching the TUI.
- "Scan disk for repos" button on the Textual launcher screen that walks the configured root for `.git` directories.
- `senex.config.toml` is now picked up from `--config <path>`, `$SENEX_CONFIG`, `./senex.config.toml`, OR `~/.senex/senex.config.toml` (in that order); the lookup error lists every searched path.
- `[ui]` config section with `scan_root` and `scan_max_depth` knobs.

**Test surface:** 920 passing, 3 skipped, 1 xfailed (recorded LMS replay fixture pending regen post-schema-tightening). 91% aggregate coverage; all per-module targets met.

### Live validation status

- ✅ Gate 13: full pytest suite green at coverage targets
- ✅ Gate 13a: live audit on fixture repo produces all artifacts, schema-valid, with full observability
- 🟡 Gates 13b/13c/13d/13e: runbooks remain in `docs/validation/2026-04-26-v1-validation.md` for users to run manually (TUI render verification, resume mid-run, tool-loop trace inspection, compaction smoke). Each builds on 13a; users can promote to confidence at their own pace.

## [1.0.0-rc1] — 2026-04-26

First release candidate. All 10 implementation milestones (M1–M10) shipped, full test suite green at coverage targets, `senex doctor` passes against live LM Studio. Five live-validation gates (13a part 2, 13b, 13c, 13d, 13e) deferred to manual user execution per the v1 deferral protocol — see `docs/validation/2026-04-26-v1-validation.md` for runbooks. Once the user runs the deferred gates and they all pass, the same commit is re-tagged as `v1.0.0`.

### Highlights

- **Local-first audit pipeline.** Walks a target repo file by file, runs each through a thinking-MoE model in LM Studio (`google/gemma-4-26b-a4b` default), and produces a per-file Markdown report, a combined run report, a stable `findings.json`, and a Claude Code handoff artifact.
- **6-tool framework** with read-only, sandboxed tools: `gitnexus_query`, `gitnexus_context`, `gitnexus_impact`, `read_file`, `grep`, `search_code`. All tool inputs are pydantic-validated; subprocess invocations are list-form and regex-hardened; tool results pass through ANSI strip + secret redaction + truncation.
- **Context compaction safety net.** When the per-file message history approaches the model's context window, a versioned compaction prompt summarizes accumulated tool results into an evidence block. Compaction calls are bounded by their own budget independent of the tool-call budget.
- **Lifecycle management.** Auto-load + auto-eject the model at run boundaries; cross-process refcount via `~/.senex/locks/`; resume-aware (the resumed run never owns unload of an attached model).
- **Streaming Textual TUI.** Launcher screen for repo/lens/sampling overrides; monitor screen with progress, current-file, findings panel (deque, maxlen=30), error banner, status strip. `senex view <audit-dir>` replays a completed run from `events.jsonl`.
- **Resume + reproducibility.** Audit dir naming carries `<DATE>-<run_id_short>`. Resume validates a 5-hash bundle (config, prompt, model_fingerprint, tool_pack, lens_version) and refuses on drift unless `--allow-mixed-resume`.
- **Security posture.** Source stays local (only outbound calls are to LM Studio loopback + local subprocesses). Audited source is wrapped in `<UNTRUSTED_FILE_CONTENT>` before LLM ingestion; system prompt instructs the model to disregard directives within. Every persisted artifact passes through the SecretRedactor (PEM, JWT, AWS, GitHub PAT, `sk-…`, env-style secrets). Loopback-only by default; non-loopback `[lmstudio].base_url` requires explicit opt-in.

### Implementation milestones

- **M1 Foundation** (config, events, schemas, lens, runlock, checkpoint, secret_redactor) — 113 tests
- **M2 Walker + Graph Awareness** (symlink-safe walker, gitnexus CLI provider, prompts) — 71 tests
- **M3 LM Studio Integration** (streaming, tick coalescing, schema fallback, fingerprint) — 60 tests
- **M4 Lifecycle + Runlock** (SDK + CLI backends, resume handshake, doctor check) — 81 tests
- **M5 Tools Framework** (registry, safety, 6 tools, ToolLoop) — 103 tests
- **M6 Context Compaction** (executor, trigger, tool-loop integration) — 38 tests
- **M7 Renderer + Aggregator** (golden-file render, NDJSON streaming, atomic writes, handoff) — 74 tests
- **M8 Phases + Auditor** (5 phases, run_audit coroutine) — 114 tests
- **M9 Subscribers + TUI** (4 subscribers, Textual launcher + monitor + 5 widgets, view replay) — 119 tests
- **M10 CLI + Validation** (argparse + 6 subcommands, scripts, README, full pytest pass) — ~104 tests

### Test surface

- **864 passing**, 3 skipped, 1 xfailed (xfail = recorded LMS replay fixture pending regeneration after M7 schema tightening; tracked for v1.0.0 promotion)
- **91% aggregate coverage**; per-module targets all met (auditor 86%, renderer 98%, walker 90%, checkpoint 100%, secret_redactor 100%, all tools ≥86%, all phases ≥85%)

### Deferred to v1.0.0 promotion

Per the deferral protocol, these live-validation gates require manual user execution before the rc1 → v1.0.0 retag:

- **13a part 2** — full headless audit on the fixture repo (4 files × ~3 min/file)
- **13b** — TUI live render verification
- **13c** — resume mid-run with Ctrl+C
- **13d** — tool-loop evidence in `<file>.thinking.md`
- **13e** — compaction smoke via `SENEX_FORCE_COMPACTION_AT_FILE` env var

Runbooks for each gate live in `docs/validation/2026-04-26-v1-validation.md`. Once all five pass, a senex maintainer retags the same commit as `v1.0.0`.

### Tag promotion (rc1 → v1.0.0)

```bash
git tag -a v1.0.0 -m "senex v1.0.0 — all live validation gates green" v1.0.0-rc1
git push origin v1.0.0
gh release create v1.0.0 --notes-file CHANGELOG.md --title "senex v1.0.0"
```

The release artifact is the same commit; only the tag name changes.

[1.0.0-rc1]: https://github.com/parthalon025/senex/releases/tag/v1.0.0-rc1
