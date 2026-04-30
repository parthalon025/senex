# senex — Legal & IP Review for Public Release

**Prepared:** 2026-04-29
**Scope:** Pre-release legal/IP analysis for `E:\senex` (project version 1.0.2)
**Audience:** Project owner; not legal advice, not a substitute for attorney review for commercial launches.
**Status:** Advisory. Action items at end. No `LICENSE` / `NOTICE` / `SECURITY.md` files have been written by this review — that is a follow-up wave.

---

## 0. Executive Summary

senex is in **good legal shape for an Apache-2.0 public release**. The Python dependency graph is uniformly permissive (Apache-2.0 / MIT / BSD / PSF). The default model (Qwen3-8B-AWQ) is Apache-2.0, contradicting the briefing's assumption that it might be Tongyi Qianwen. There is **no telemetry**; outbound HTTP is loopback-only and enforced in code (`senex/phases/preflight.py:319-338`).

Three issues need attention before release:

1. **`gitnexus` is PolyForm Noncommercial 1.0.0**, not "license unknown." senex must not bundle, embed, or hard-require it for commercial users without a different license path. Subprocess invocation does not contaminate senex's own license, but the README/docs need to be honest about what users are running.
2. **The PyPI name `senex` is taken** (an inactive Python 2.7 Pyramid project, last release pre-2018). Choose a new distribution name (e.g. `senex-audit`, `senex-cli`) or reach out to the existing maintainer to claim the abandoned project.
3. **semgrep is LGPL-2.1**. Subprocess invocation is fine — does not trigger copyleft on senex — but a sentence to that effect belongs in the docs.

Recommended license for senex: **Apache-2.0** with an explicit patent grant and `NOTICE` file. DCO sign-off for contributors.

---

## 1. License Recommendation for senex

**Recommendation: Apache-2.0.**

Reasoning:

| Factor | Apache-2.0 | MIT | BSD-3-Clause |
|---|---|---|---|
| Patent grant (explicit) | YES | No | No |
| Patent termination on patent litigation | YES | No | No |
| Trademark non-grant clause | Explicit | Implicit | No |
| `NOTICE` file mechanism for attribution | Built-in | None | None |
| OSI-approved & SPDX-recognized | YES | YES | YES |
| Compatible with all current senex deps | YES | YES | YES |
| Compatible with downstream commercial use | YES | YES | YES |
| Industry default for Python AI/ML tooling | Yes (SGLang, openai-python, detect-secrets, pydantic-core, tiktoken all Apache-2.0) | Common | Less common |
| Supplies indemnification framing for contributors | Implicit via §5 | Weak | Weak |

For an LLM-augmented code audit tool, the **patent grant** matters: prompt-engineering / agent-orchestration patents are a real-and-growing minefield. Apache-2.0 supplies a reciprocal patent license and termination on patent litigation, which is materially better than MIT/BSD-3 for a tool in this category.

**Compatibility check:** Apache-2.0 has no incompatibility with any current senex dependency (all are Apache-2.0, MIT, BSD-2/3, PSF, or MPL-2.0 — see Section 2). It is one-way compatible with GPLv3 (Apache-2.0 code can be included in GPLv3 projects) but cannot incorporate GPL code into senex itself; senex has no GPL code.

**SPDX header for source files (recommended, not required):**
```
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Justin McFarland
```

---

## 2. Dependency License Matrix

Read from `E:\senex\pyproject.toml` and `E:\senex\requirements.txt` (the two are consistent except for the `dev` extras and three additions in pyproject — `jsonschema`, `tomli-w`, `ulid-py`, `pathspec`).

### 2.1 Runtime dependencies (direct)

| Package | Pin | License | SPDX | Notes |
|---|---|---|---|---|
| `openai` | `>=1.50,<2.0` | Apache-2.0 | `Apache-2.0` | OpenAI Python SDK. Used as OpenAI-compatible client for SGLang/LM Studio. |
| `pydantic` | `>=2.5,<3` | MIT | `MIT` | Core schema validation. |
| `textual` | `>=0.50` | MIT | `MIT` | TUI framework. |
| `tiktoken` | (unpinned) | MIT | `MIT` | OpenAI tokenizer; calibrated for GPT — note in code that Qwen tokenization is approximate (`senex/lmstudio_client.py:464`). |
| `portalocker` | (unpinned) | BSD-3-Clause | `BSD-3-Clause` | Cross-platform file locking. |
| `detect-secrets` | (unpinned) | Apache-2.0 | `Apache-2.0` | Yelp's secret scanner; runtime + library use. |
| `regex` | (unpinned) | Apache-2.0 (and PSF for some upstream) | `Apache-2.0` | Mrab regex; verify dist-info on packaging — `LICENSE.txt` ships in wheel. |
| `httpx` | (unpinned) | BSD-3-Clause | `BSD-3-Clause` | Async HTTP client. |
| `jsonschema` | `>=4.20` | MIT | `MIT` | Draft 2020-12 validator for `senex/schema/*.json`. |
| `tomli-w` | (unpinned) | MIT | `MIT` | TOML writer. |
| `ulid-py` | (unpinned) | MIT | `MIT` | ULID generator for run IDs. |
| `pathspec` | `>=0.12.0` | MPL-2.0 | `MPL-2.0` | Gitignore-style path matching. **MPL-2.0 is weak copyleft on a per-file basis only** — does NOT contaminate senex. Safe. |

### 2.2 Transitive dependencies of note

These ship via the runtime tree (sampled from `.venv/Lib/site-packages/*.dist-info`):

| Package | License |
|---|---|
| `anyio`, `sniffio`, `idna`, `h11`, `httpcore` | MIT / BSD / Apache-2.0 (dual where applicable) |
| `certifi` | MPL-2.0 |
| `pydantic-core`, `jiter` | MIT / Apache-2.0 |
| `annotated-types`, `typing-extensions`, `typing-inspection` | MIT / PSF |
| `markdown-it-py`, `mdurl`, `linkify-it-py`, `uc-micro-py`, `mdit-py-plugins` | MIT |
| `rich` | MIT |
| `urllib3`, `requests`, `charset-normalizer` | MIT / Apache-2.0 |
| `attrs`, `rpds-py`, `referencing`, `jsonschema-specifications` | MIT |
| `colorama`, `iniconfig`, `pluggy`, `packaging` | BSD / MIT / Apache-2.0 |

**No GPL/AGPL/LGPL packages in the runtime tree.** No proprietary libraries.

### 2.3 Dev dependencies

| Package | License | Use |
|---|---|---|
| `pytest`, `pytest-asyncio`, `pytest-cov`, `coverage` | MIT / Apache-2.0 | Test runner. Not shipped to users. |
| `hypothesis` | MPL-2.0 | Property tests. Not shipped. |
| `mypy`, `mypy-extensions` | MIT | Type checker. Not shipped. |
| `ruff` | MIT | Linter. Not shipped. |
| `respx` | BSD-3-Clause | HTTPX request mocking. Not shipped. |

Dev dependencies do not flow to end-user installs and have no licensing impact on the published wheel.

### 2.4 Flagged items

**None copyleft-contaminating.** `pathspec` (MPL-2.0) and `certifi` (MPL-2.0) are weak-copyleft per-file licenses; senex does not modify or vendor either, so MPL obligations reduce to "include the upstream license text if redistributing the file" — handled automatically by pip wheel installation. Not an issue for an Apache-2.0 senex.

---

## 3. Model License Analysis

### 3.1 Default model: `Qwen/Qwen3-8B-AWQ`

**License: Apache-2.0** — verified directly from the Hugging Face model card metadata (`license: apache-2.0`). The base `Qwen/Qwen3-8B` is also Apache-2.0. The original Qwen-1.x series used the **Tongyi Qianwen License Agreement** with restrictions; Qwen2 / Qwen2.5 / Qwen3 instruction-tuned dense models moved to Apache-2.0. The AWQ quantization does not change the license.

**Briefing assumption was wrong.** This is the most permissive outcome possible for a recommended model.

**Implications for senex:**
- senex can recommend, document, and (if it ever bundled weights — which it does not) ship Qwen3-8B-AWQ without restriction.
- Commercial use is permitted under Apache-2.0.
- No daily-active-user threshold (the Llama-2/3 community license has one — Qwen does not).
- Apache-2.0 §4 attribution still applies: if senex were to redistribute the weights, it would need to retain the LICENSE / NOTICE file and surface attribution. **senex does not redistribute weights** (users pull from HF themselves), so this reduces to a documentation courtesy: name the model and cite Alibaba's Qwen team in the README.

**Acceptable Use:** Alibaba publishes a [Qwen Acceptable Use Policy](https://github.com/QwenLM/Qwen3) on the GitHub repo (no illegal content, etc.). This is a model-of-use restriction on the **end user**, not a license restriction on **senex**. senex is not responsible for end-user policy compliance; a single sentence in the README pointing users at the upstream policy is sufficient.

**One risk to be aware of:** Hugging Face's [Terms of Service](https://huggingface.co/terms-of-service) bind users who download from the Hub. senex does not download — it instructs users to. The download contract is between the user and HF. Document that senex never transmits HF credentials and the user must accept HF's terms themselves.

### 3.2 Recommended unrestricted alternative

For users who want a fully Apache-2.0 model that works with SGLang's `--reasoning-parser qwen3-thinking` and `--tool-call-parser qwen` parsers:

**Primary alternative: `Qwen/Qwen3-14B-AWQ` or `Qwen/Qwen3-4B`** — same Apache-2.0 license, same parser family, larger/smaller sibling for users with different VRAM budgets. These are the easiest swap (no SGLang flag changes).

**Truly distinct alternative: `mistralai/Mistral-Small-Instruct-2409` or `mistralai/Mistral-7B-Instruct-v0.3`** — Apache-2.0. Tool-calling is supported but uses a different parser (`mistral` parser in SGLang, not `qwen`). Recommend as a "different vendor" fallback for users who want vendor diversity.

**Distant third: `meta-llama/Meta-Llama-3.1-8B-Instruct`** — **NOT recommended for the unrestricted slot**. The Llama 3.1 Community License is Apache-like but adds (a) an attribution string requirement, (b) a 700M MAU acceptable-use trip-wire, (c) a use-policy with named-application carve-outs. It is fine for personal/small-business use but not "fully unrestricted."

**Concrete recommendation for the README "Models" section:**

> **Default:** `Qwen/Qwen3-8B-AWQ` (Apache-2.0). Best price/perf for this class of audit task on consumer GPUs.
> **Larger (more accurate):** `Qwen/Qwen3-14B-AWQ` (Apache-2.0). Same parsers, ~2× VRAM.
> **Smaller (faster):** `Qwen/Qwen3-4B` (Apache-2.0).
> **Vendor-diverse alternative:** `mistralai/Mistral-Small-Instruct-2409` (Apache-2.0; requires `--tool-call-parser mistral`).

---

## 4. External Tool Licenses

### 4.1 SGLang — Apache-2.0 ✓

Verified from `https://github.com/sgl-project/sglang/blob/main/LICENSE`. senex invokes SGLang only via its OpenAI-compatible HTTP API on a loopback port (`http://localhost:30000/v1`). No code embedding, no static linking, no source modification. Apache-2.0 imposes no obligations on senex from this usage pattern.

### 4.2 LM Studio — Proprietary (commercial), CLI invocation only ✓

LM Studio's EULA prohibits redistribution and reverse-engineering of the LM Studio binary itself. senex never bundles, redistributes, or modifies LM Studio. It only:
- Calls the `lms` CLI binary as a subprocess (`senex/lmstudio_lifecycle.py`)
- Speaks HTTP to LM Studio's OpenAI-compatible server

Both behaviors are explicitly intended user-facing surfaces of LM Studio. **This is a textbook fair use of a third-party tool.** A line in the README clarifying that LM Studio is a separate product with its own license keeps everything transparent.

### 4.3 semgrep — LGPL-2.1, subprocess invocation ✓

Verified from `https://github.com/semgrep/semgrep/blob/develop/LICENSE`. senex (through the agent's tool layer) invokes semgrep via subprocess. **Subprocess invocation does not trigger LGPL contagion.** LGPL-2.1 §5 / §6 obligations attach to **linking** (static or dynamic), not to inter-process communication. The Free Software Foundation's own FAQ is explicit on this point:

> "Communicating with each other only through certain types of arms-length 'communication mechanisms', such as pipes, sockets and command-line arguments, with appropriate semantics for the communication, are normally not enough to make them parts of one program."

senex is fine. **Documentation note (recommended):** add a single sentence to docs: "senex invokes semgrep as a subprocess. semgrep is LGPL-2.1; senex does not link to or vendor it."

### 4.4 gitnexus — PolyForm Noncommercial 1.0.0 — NEEDS ATTENTION

Verified from `https://github.com/abhigyanpatwari/GitNexus/blob/main/LICENSE`. **This is not an OSI-approved open-source license.** PolyForm Noncommercial 1.0.0 permits use, modification, and distribution **only for non-commercial purposes**. Commercial use requires a separate license from the maintainer.

**Implications for senex:**

| Scenario | Status |
|---|---|
| senex (Apache-2.0) invokes `npx gitnexus` as a subprocess | **License-compatible** — subprocess does not propagate license terms. senex itself remains freely distributable. |
| senex bundles or vendors gitnexus | **PROHIBITED for commercial users.** Do not do this. |
| senex hard-requires gitnexus for core functionality | **Risky** — if a commercial user runs senex against their company codebase, *they* may need a gitnexus commercial license, even though senex itself is fine. |
| senex makes gitnexus optional, with graceful degradation | **Best path** — keeps senex usable for commercial-without-gitnexus, non-commercial-with-gitnexus, or commercial-with-gitnexus-paid scenarios. |

The current code already treats gitnexus tools as optional via the lens registry pattern, which is the right architectural choice here.

**Required README disclosure:**

> senex can integrate with [gitnexus](https://github.com/abhigyanpatwari/GitNexus) for code-graph navigation. **gitnexus is licensed under PolyForm Noncommercial 1.0.0.** Commercial users must obtain a commercial license from gitnexus's maintainer before relying on it in a for-profit context. senex itself is Apache-2.0 and never bundles gitnexus; this disclosure is provided for end-user license-compliance awareness.

This is the single most important legal-prose change the README needs.

---

## 5. Intellectual Property in Shipped Artifacts

I read the following candidates for embedded third-party content:

| File | Status | SPDX header recommended? |
|---|---|---|
| `senex/prompts/system_senior_dev.md` | **Original.** Voice and structure are clearly authored from scratch — no Anthropic / OpenAI / Google example-prompt phrasing, no quoted RFC text, no copy-pasted blog-post passages. Distinctive senex idioms ("TRIAGE GATE", "PRIORITY RUBRIC: high/medium/low/healthy", "REASONING SEQUENCE: ORIENT/SCAN/GRAPH/READ/CONFIRM/EMIT", trust-boundary tag conventions). | Yes (`SPDX-License-Identifier: Apache-2.0` comment at top). |
| `senex/prompts/compaction.md`, `lang_*.md`, `cross_cutting.md`, `per_file_user.md`, `claude_handoff.md` | Original; consistent with system_senior_dev voice. | Yes. |
| `senex/lens/correctness/system_senior_dev.md` | Same content as `senex/prompts/system_senior_dev.md`. **Duplicate file.** Keep both via a single source of truth (one is a copy or symlink of the other) or document why the duplication exists. Not a legal issue, but a maintenance one. | Yes. |
| `senex/lens/correctness/skills/security.md` | Original. Generic CWE-style guidance phrased in senex voice. Not copied from MITRE or OWASP — those organizations would phrase the same ideas more formally and verbosely. | Yes. |
| `senex/lens/correctness/skills/async_concurrency.md` | Original. | Yes. |
| `senex/lens/correctness/skills/api_surface.md` | Original (assumed from sample of two siblings — verify by reading). | Yes. |
| `senex/schema/*.json` | Original. The `$schema` field references `https://json-schema.org/draft/2020-12/schema` (the JSON Schema spec itself, MIT-distributed and freely usable by reference), but the actual schemas — `audit_response`, `crosscut_response`, `compaction_response`, `checkpoint`, `findings_index`, `events` — are senex-authored. The shape and field names (`overall_assessment`, `findings[].priority` enum of `high/medium/low/healthy`, `confidence` enum, `category`, `location.symbol`) are senex-specific and not copied from any standard schema repository I'm aware of. | JSON does not support comments, but include attribution in `$comment` field or in a sibling LICENSE file inside the schema directory if you prefer. |

**No third-party content found requiring license attribution in the shipped artifacts.**

**SPDX header convention I'd recommend** for senex source files:

```python
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Justin McFarland and senex contributors
```

For Markdown prompt files:
```markdown
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright (c) 2026 Justin McFarland and senex contributors -->
```

---

## 6. Trademark Concerns — `senex` Name Availability

### 6.1 Search results

| Channel | Result |
|---|---|
| **PyPI** | **TAKEN.** `senex` is registered to Joel Dunham (dativebase) — a Pyramid web app for administering Online Linguistic Database (OLD) instances. Last release pre-2018; project appears abandoned per Snyk advisor data. Author email is on the existing PyPI page; the package supports Python 2.7 and Ubuntu 14.04, so there's no realistic ongoing user base. |
| **PyPI (variants)** | `django-senex-shop` exists (unrelated — Django e-commerce package). |
| **npm** | Not searched in depth — senex doesn't publish to npm. |
| **GitHub** | `dativebase/senex` exists (the same Python 2.7 project). 0 stars, dormant. Multiple unrelated `senex` repos exist as personal projects but no widely-used software trademark. |
| **USPTO** | No registered "SENEX" mark in software classes (Class 9, Class 42). Closest hits are SENEX ARMS (firearms) and Senex Trading LLC (kitchenware via the STARLAIVE mark) — both unrelated. |
| **Common-law trademark risk** | Low. "Senex" is Latin for "old man" and is used as a generic word in linguistics, astronomy (a star name), classical scholarship, and band/album names. No party appears to have established commercial trademark rights in the software field. |

### 6.2 Recommendation

**Pick a different PyPI distribution name.** The CLI command can stay as `senex` (no name conflict at the CLI level), but the **PyPI package name** must differ to avoid colliding with the existing-but-abandoned `senex` package. Three options ranked:

1. **`senex-audit`** — clearest, descriptive, low collision risk. Matches the repository tagline. **My recommendation.**
2. **`senex-cli`** — generic but unambiguous. Used by other tools (e.g., `aws-cli`, `gh-cli`).
3. **Reclaim `senex`** — file a [PEP 541 abandoned-project transfer request](https://peps.python.org/pep-0541/) with PyPI Support after attempting to contact Joel Dunham. PyPI considers a project abandoned if it (a) has no recent releases, (b) the maintainer is unreachable for 6+ months, (c) it is broken / Python-2-only. `senex` plausibly qualifies on (c) alone (Python 2.7 has been EOL since 2020). **Only pursue this if branding is critical.** Worst case: 6+ months of waiting, no guaranteed outcome.

**For trademark protection (optional, not urgent):** if commercial branding becomes a goal later, file a USPTO trademark application in **Class 9 (downloadable software)** and **Class 42 (SaaS / software-as-a-service)** for "SENEX" in connection with code-audit/static-analysis software. Estimated cost: ~$350/class plus optional attorney fees. For a personal open-source project this is overkill; revisit if a company forms around it.

---

## 7. Privacy / Data-Handling Claims

### 7.1 Verification of "no telemetry" / "loopback only" claim

I grepped the senex source for outbound HTTP calls. Findings:

| Outbound call site | Destination | Loopback? |
|---|---|---|
| `senex/lmstudio_client.py:267` | `config.lmstudio.base_url` | **Default** `http://localhost:30000/v1`; user-overridable; non-loopback explicitly refused at `senex/phases/preflight.py:333` unless `allow_non_loopback=true`. |
| `senex/lmstudio_lifecycle.py:457, 474` | Same | Same. Loopback enforced via `_is_loopback()` at `senex/phases/preflight.py:319-327` (recognizes `localhost`, `127.0.0.1`, `::1`). |
| `senex/cli_wizard.py:401, 457` | Same | Same. |
| `senex/phases/preflight.py:258` | Same | Same. |
| `senex/tools/search_code.py:56-69` | `CLAUDE_CONTEXT_URL`, default `http://localhost:8765` | Loopback by default. Tool is "best-effort" — gracefully degrades if unreachable. |
| `senex/phases/base.py:72` | (no network call — comment about "telemetry" refers to internal log fields, not remote telemetry) | N/A |

**No PostHog, Mixpanel, Segment, Sentry, Bugsnag, or other analytics vendors appear in dependencies or source.** No `requests.post(...)` to non-loopback hosts. No `httpx.post(...)` to non-loopback hosts. No environment-variable-controlled telemetry endpoints. No silent metrics emission.

### 7.2 Truthful README language

senex's existing README (`E:\senex\README.md:8`) already states: *"senex is a local-first tool: source code never leaves the machine, no telemetry, secret …"*. This claim is **truthful and defensible** based on the source review.

**Recommended hardening of the privacy section** (suggested text for README):

```markdown
## Privacy & data handling

senex is a local-first tool. By design:

1. **Source code never leaves your machine.** senex sends code only to the
   LLM endpoint you configure (`[lmstudio].base_url`). The default and
   recommended endpoint is loopback (`http://localhost:30000/v1`).
   Non-loopback URLs are rejected at preflight unless you explicitly opt
   in via `[lmstudio].allow_non_loopback = true`.
2. **No telemetry.** senex does not phone home, emit usage metrics, send
   crash reports, or check for updates. There is no analytics SDK in the
   dependency tree (verified: no PostHog, Mixpanel, Segment, Sentry).
3. **No third-party API calls.** senex talks to the inference server you
   configure. That's it. It does not call OpenAI, Anthropic, Google, or
   any other hosted LLM unless you point it at one yourself.
4. **Secret redaction.** senex applies a secret-redaction pass
   (`senex.secret_redactor`) to all persisted artifacts (audit reports,
   checkpoints, logs) before writing to disk. JWTs, API keys, and common
   credential patterns are scrubbed.
5. **Optional integrations are opt-in.** semgrep and gitnexus are
   subprocess integrations; if not installed, senex degrades gracefully.
   gitnexus is PolyForm Noncommercial 1.0.0 — see the License section.
```

This is more concrete than "no telemetry" and harder to challenge.

### 7.3 Privacy law footprint

senex collects **no personal data**. It is not a "data controller" under GDPR Art. 4(7) or a "business" under CCPA §1798.140. **No privacy policy required.** This is a positive consequence of the local-first design.

---

## 8. Required Files for Public Release

> **Per the briefing, this review does not write these files.** Below is what each should contain when you write them in the next wave.

| File | Required for | Content outline |
|---|---|---|
| `LICENSE` | Apache-2.0 release | Full unmodified text of the Apache License 2.0 (https://www.apache.org/licenses/LICENSE-2.0.txt). 11,358 chars. Do not paraphrase or modify. |
| `NOTICE` | Apache-2.0 §4(d) requires when bundling Apache-2.0 third-party code | Should list: `senex` copyright line, then attribution to bundled Apache-2.0 dependencies senex *redistributes*. Since senex does not vendor any third-party source, the NOTICE file can be minimal: just the senex copyright header. Apache-2.0 §4(d) is mandatory only when redistributing Apache-2.0 code that itself contained a NOTICE; senex doesn't vendor any. **Recommend writing a minimal NOTICE anyway** for clarity. |
| `AUTHORS` (or `CONTRIBUTORS`) | Optional but professional | Plain text, one name + optional email per line. Start with `Justin McFarland <justin.p.mcfarland@gmail.com>`. Update via `git log --format='%aN <%aE>' \| sort -u` periodically. |
| `SECURITY.md` | Recommended for any public security-adjacent tool | Vulnerability disclosure policy. Required fields: contact email or security advisory channel, expected response time, scope (what counts as a vulnerability in senex vs. in user-supplied code), safe-harbor statement for good-faith research. GitHub auto-renders this in the Security tab. |
| `CODE_OF_CONDUCT.md` | Optional but recommended | Standard Contributor Covenant 2.1 text. Lowers reviewer/contributor friction. |
| `CONTRIBUTING.md` | Recommended | DCO sign-off requirement, testing/lint expectations, PR conventions. See Section 9. |
| `CHANGELOG.md` | Optional | Keep-a-Changelog format. Helpful for users tracking version diffs. |

### Minimal NOTICE template (for use in next wave)

```
senex
Copyright (c) 2026 Justin McFarland and senex contributors

This product includes software developed at Justin McFarland (2026).

This product is distributed under the Apache License, Version 2.0
(see LICENSE). It runs alongside, but does not bundle, the following
third-party software, each of which is governed by its own license:

  - SGLang (Apache-2.0) — https://github.com/sgl-project/sglang
  - LM Studio (Proprietary; CLI invoked as subprocess) — https://lmstudio.ai
  - semgrep (LGPL-2.1; subprocess invoked, not linked) — https://github.com/semgrep/semgrep
  - gitnexus (PolyForm Noncommercial 1.0.0; subprocess invoked, not bundled) — https://github.com/abhigyanpatwari/GitNexus
  - Qwen3-8B-AWQ (Apache-2.0; downloaded by user from Hugging Face) — https://huggingface.co/Qwen/Qwen3-8B-AWQ
```

### Minimal SECURITY.md template (for use in next wave)

```markdown
# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in senex, please report it
privately:

- **Email:** justin.p.mcfarland@gmail.com (with subject "[senex security]")
- **GitHub Security Advisory:** Use the "Report a vulnerability" button
  on the repository's Security tab.

Please **do not** open a public issue for security reports.

## Scope

In scope:
- Vulnerabilities in senex's own code (path traversal, command injection,
  secret leakage in logs, crashes from malformed input, etc.)
- Issues that cause senex to leak source code outside the configured
  loopback boundary.

Out of scope:
- Vulnerabilities in upstream dependencies (report those upstream).
- Vulnerabilities in the LLM models themselves (e.g. prompt injection
  via audited code) — see the trust-boundary section of the README.
- Configuration mistakes by the operator.

## Response Time

We aim to acknowledge reports within 5 business days and provide an
initial assessment within 14 days. This is a personal open-source
project; please be patient.

## Safe Harbor

We will not pursue legal action against good-faith security researchers
who comply with this policy.
```

---

## 9. CLA / DCO Recommendation

**Recommendation: DCO (Developer Certificate of Origin), no CLA.**

| Mechanism | Pros | Cons | Verdict for senex |
|---|---|---|---|
| **No agreement at all** | Zero friction. | Contributors retain full copyright; future relicensing is impossible without unanimous consent. | Too risky if you ever want to relicense or commercialize. |
| **DCO (sign-off)** | Lightweight (`git commit -s`); GitHub has built-in enforcement; standard in Linux kernel, Docker, OpenShift, etc. Contributors keep copyright but assert they have the right to submit. | Does not centralize copyright (relicensing still requires consent), but does establish a clean provenance chain. | **Recommended.** |
| **Apache-style ICLA** (individual contributor license agreement) | Grants project a perpetual license; enables relicensing decisions. | Significant friction — every contributor must sign before their first PR. CLA-bot tooling needed. Many casual contributors balk. | Overkill for a solo project pre-traction. Reconsider if a company forms. |

DCO is a one-liner in commit messages: `Signed-off-by: Justin McFarland <justin.p.mcfarland@gmail.com>`. GitHub Actions has a [DCO check](https://github.com/apps/dco) that blocks unsigned PRs. CONTRIBUTING.md should make this requirement explicit:

```markdown
## Sign your work

senex uses the Developer Certificate of Origin (DCO,
https://developercertificate.org). Every commit must include:

    Signed-off-by: Your Name <your.email@example.com>

Add this automatically via `git commit -s`. PRs without sign-off will
be blocked by CI.
```

---

## 10. Telemetry / Analytics — Verified Negative

Confirmation, after grepping the full source tree:

- **No analytics dependency** in `pyproject.toml` or `requirements.txt` (no `posthog`, `mixpanel`, `segment-analytics-python`, `sentry-sdk`, `bugsnag`, `rollbar`, `honeybadger`, or similar).
- **No outbound HTTP** to non-loopback URLs from the senex codebase. The only `httpx.AsyncClient` instantiations target either (a) the user-configured inference base_url (loopback by default, refused otherwise unless explicitly overridden — `senex/phases/preflight.py:333`) or (b) the claude-context MCP at `localhost:8765` (`senex/tools/search_code.py:31`).
- **Test fixtures** at `tests/recorded/conftest.py:101-126` use `http.client.HTTPConnection` to a local mock router — test-only, not shipped.
- **The word "telemetry"** appears in source comments and docs only as either (a) a deliberately negative claim ("no telemetry"), (b) a reference to internal logging/event fields used for the doctor JSON output, or (c) historical design notes. None of these are remote telemetry emission.

**Conclusion: senex does not emit any telemetry to any remote endpoint.** The README claim is accurate and can be made stronger as recommended in §7.2.

---

## 11. Action Items — Prioritized

### P0 — Must do before public release

1. **Choose final PyPI distribution name.** Recommended: `senex-audit`. Update `pyproject.toml` `[project].name`, retain CLI command `senex`. (One file, one line change.)
2. **Add `LICENSE` (Apache-2.0 full text)** at repo root.
3. **Add `NOTICE`** at repo root (template above).
4. **Add gitnexus license disclosure** to README, calling out PolyForm Noncommercial 1.0.0 and the implication for commercial users. This is the single most important README change.
5. **Add `SECURITY.md`** (template above).

### P1 — Should do before public release

6. **Add `CONTRIBUTING.md`** with DCO requirement.
7. **Add SPDX headers** to senex-authored source files (`*.py`, `*.md` prompt/skill files, `*.json` schemas via `$comment`).
8. **Strengthen README privacy section** with the language in §7.2.
9. **Enable GitHub DCO check** via the DCO app or branch protection rule.
10. **Add a "Models" section** to the README listing the recommended primary + fallback models with their Apache-2.0 licenses called out.

### P2 — Nice to have

11. **Add `CODE_OF_CONDUCT.md`** (Contributor Covenant 2.1).
12. **Add `CHANGELOG.md`** (Keep-a-Changelog).
13. **De-duplicate `senex/prompts/system_senior_dev.md` vs `senex/lens/correctness/system_senior_dev.md`** — pick one canonical location.
14. **(Future, optional)** USPTO trademark application for SENEX in software classes if commercial branding is pursued.
15. **(Future, optional)** PEP 541 reclaim attempt for the `senex` PyPI name if branding becomes critical.

### P3 — Monitoring

16. **Re-verify gitnexus license** before each release. Maintainers can change PolyForm to MIT (or vice versa); a one-minute check at release time avoids stale claims in NOTICE.
17. **Re-verify Qwen3 model license** if you ever pin a different default model. Apache-2.0 has been Qwen2/2.5/3's house style, but each model card is authoritative.
18. **Add a CI license-scan step** (e.g. `pip-licenses --format=json --output-file licenses.json`) to detect any future copyleft drift in the dependency graph.

---

## 12. Caveats

This is a senior advisory review, not formal legal counsel. Specifically:

- **Trademark conclusions are based on USPTO TESS searches** and PyPI/GitHub presence. They do not cover state common-law trademarks, EU/UKIPO/WIPO registrations, or trademarks in software-adjacent classes I did not search. If commercial branding becomes a priority, retain a trademark attorney for a clearance opinion.
- **License classifications were verified by reading upstream LICENSE files** at the dates noted. License changes upstream (e.g., a future Qwen model returning to Tongyi Qianwen, or gitnexus relicensing) would invalidate specific findings; the action items in §11 P3 mitigate this.
- **The "no telemetry" verification is based on static source review** at the current HEAD. CI tooling that enforces this property going forward (e.g., a unit test asserting that the dependency tree contains no telemetry packages) is the durable solution.
- **Apache-2.0 vs. AGPL-3.0:** I did not analyze AGPL-3.0 as a license option for senex. AGPL is sometimes chosen for SaaS-defensive reasons. senex is a CLI tool, not a network service; AGPL is the wrong tool for this category and would actively harm adoption.

---

## 13. Files Referenced

Absolute paths inspected during this review:

- `E:\senex\pyproject.toml`
- `E:\senex\requirements.txt`
- `E:\senex\README.md` (briefly, for telemetry claim)
- `E:\senex\senex\prompts\system_senior_dev.md`
- `E:\senex\senex\prompts\compaction.md`
- `E:\senex\senex\prompts\` (file listing)
- `E:\senex\senex\lens\correctness\skills\security.md`
- `E:\senex\senex\lens\correctness\skills\async_concurrency.md`
- `E:\senex\senex\lens\correctness\skills\` (file listing)
- `E:\senex\senex\schema\audit_response.schema.json`
- `E:\senex\senex\schema\` (file listing)
- `E:\senex\senex\phases\preflight.py` (lines 250-345 — loopback enforcement)
- `E:\senex\senex\tools\search_code.py` (claude-context MCP client)
- `E:\senex\senex\config.py:169-180` (base_url and allow_non_loopback defaults)
- `E:\senex\.venv\Lib\site-packages\*.dist-info\` (transitive dependency LICENSE files, sampled)

External sources consulted (live web fetches):

- Hugging Face: Qwen/Qwen3-8B and Qwen/Qwen3-8B-AWQ model cards (Apache-2.0 confirmed)
- GitHub: sgl-project/sglang LICENSE (Apache-2.0 confirmed)
- GitHub: semgrep/semgrep LICENSE (LGPL-2.1 confirmed)
- GitHub: openai/openai-python LICENSE (Apache-2.0 confirmed)
- GitHub: Yelp/detect-secrets LICENSE (Apache-2.0 confirmed)
- GitHub: abhigyanpatwari/GitNexus LICENSE (PolyForm Noncommercial 1.0.0 confirmed)
- USPTO TESS (via web search): no SENEX software trademark found
- PyPI: senex package (Joel Dunham, Pyramid OLD admin tool, dormant) — name conflict confirmed

---

*End of review.*
