# Security Policy

## Supported Versions

senex follows semantic versioning. Security fixes are issued only against the
**latest minor release**. Older minor lines are not patched; users on older
versions should upgrade to receive fixes.

| Version | Supported          |
|---------|--------------------|
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

**Do not open a public GitHub issue for suspected vulnerabilities.**

Report security issues privately via GitHub Security Advisories:

1. Go to <https://github.com/parthalon025/senex/security/advisories/new>
2. Fill out the advisory form with reproduction steps and impact assessment.
3. The maintainer is notified automatically.

If GitHub Security Advisories is not available to you, email
`justin.p.mcfarland@gmail.com` with the subject prefix `[senex-security]`.

### Expected response time

| Stage                    | Target                  |
|--------------------------|-------------------------|
| Acknowledgement          | Within 3 business days  |
| Triage + severity rating | Within 7 business days  |
| Fix or mitigation plan   | Within 30 days for High/Critical; best-effort for Medium/Low |
| Public disclosure        | Coordinated with reporter; default 90 days from acknowledgement |

## Severity Bracketing (CVSS v3.1)

| Severity | CVSS    | Examples                                                        |
|----------|---------|-----------------------------------------------------------------|
| Critical | 9.0–10  | Remote code execution; arbitrary file write outside audit dir.  |
| High     | 7.0–8.9 | Secret leak in persisted artifact; sandbox escape from tool calls. |
| Medium   | 4.0–6.9 | Path traversal blocked by depth check; DoS in single audit run. |
| Low      | 0.1–3.9 | Log-line redaction gap on transient streams; minor info leak.   |

## Scope

### In scope

- senex source code under `senex/` and shipped configuration.
- Subprocess wrappers around external tools (input validation, argument
  construction, output handling).
- Audit-output redaction (`SecretRedactor`), checkpoint integrity, and
  trust-boundary enforcement around model-ingested file content.
- Default network posture (loopback-only `[lmstudio].base_url`) and
  `allow_non_loopback` opt-in.

### Out of scope

The following are **third-party tools** invoked by senex; report issues
upstream rather than to this project:

- **SGLang** (Apache-2.0) — local inference server. Report at
  <https://github.com/sgl-project/sglang/security>.
- **GitNexus** (PolyForm Noncommercial 1.0.0) — graph indexer invoked via
  `npx gitnexus`. Report to the GitNexus maintainer.
- **semgrep** (LGPL-2.1) — invoked via subprocess. Report at
  <https://github.com/semgrep/semgrep/security>.
- LM Studio (proprietary) — alternative inference backend. Report to LM
  Studio support.
- Models loaded into the inference backend (Qwen, Gemma, etc.). Model-level
  safety / jailbreak issues are not senex bugs.

Audited third-party source code is also out of scope: senex is an
**advisory** static-analysis pipeline. Findings reflect model output and
may be inaccurate; do not treat them as an authoritative security review
of the audited project.

## Safe-Harbor for Researchers

Good-faith research that respects this policy will not be referred for
prosecution. We will not initiate legal action against you for accidental,
good-faith violations, or for circumvention of technical measures used to
enforce this policy, provided you:

- Make a good-faith effort to avoid privacy violations and disruption.
- Only test against systems you own or have explicit permission to test.
- Give us reasonable time to respond before any public disclosure.
