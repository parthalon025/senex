# senex Compliance Review

**Scope:** senex v1.0.2 (`E:\senex`), pre-PyPI publication compliance posture. Local-only Python code-audit tool. Single-machine LLM via LM Studio / SGLang on `localhost`.
**Auditor:** compliance-auditor (read-only)
**Date:** 2026-04-29
**Severity legend:** `BLOCK` = fix before PyPI publish; `ADVISE` = ship-blocker only for regulated adopters; `INFORM` = document for users.

---

## Executive Summary

senex's runtime posture is strong on the points that matter most for an offline tool: source code never leaves the machine, the only outbound calls are to `localhost` (`lmstudio_client.py:266`, `lmstudio_lifecycle.py:456,473`) and to local subprocesses (`npx gitnexus`, `lms`), there is no telemetry framework wired in (`grep -ri "analytics|telemetry|sentry|posthog|mixpanel|datadog"` returns one comment-only false positive at `senex/phases/base.py:72`), every persisted artifact passes through `SecretRedactor` (`senex/renderer.py:122`, `senex/handoff.py:68`), and the disk-writer is genuinely append-only with `fsync` per record (`senex/subscribers/disk_writer.py:54-73`).

The **publication risk is therefore concentrated in documentation, not in code paths**. Two security/compliance claims in the README are not backed by implementation, three regulated-data redaction patterns are missing, and the project is missing the standard open-source compliance documents (LICENSE, SECURITY.md, PRIVACY.md, EU AI Act disclosure, README "Privacy" / "Limitations" sections). One configured user control (`output.retention_days`) is dead — the value is accepted but never read.

| # | Finding | Framework(s) | Severity |
|---|---|---|---|
| 1 | No `LICENSE` file in repo root | OSS / PyPI policy | **BLOCK** |
| 2 | No `SECURITY.md` / vulnerability disclosure policy | SOC 2 CC7.4, ISO 27001 A.5.7 | **BLOCK** |
| 3 | README claims `checkpoint.json` carries an HMAC; code has no HMAC | SOC 2 CC1.4 (accuracy of representations), trust | **BLOCK** |
| 4 | `output.retention_days` accepted but never enforced | GDPR Art. 5(1)(e), CCPA §1798.105 | **BLOCK** |
| 5 | SecretRedactor does not match PAN / IBAN / SSN / email | PCI DSS 3.4, GDPR Art. 32, HIPAA §164.312(a)(2)(iv) | **ADVISE** |
| 6 | Audit artifacts not tamper-evident (no signing of `events.jsonl`, `findings.json`, `combined.md`) | SOC 2 CC7.2, PCI DSS 10.5.2 | **ADVISE** |
| 7 | No README "Privacy" / "Limitations" section; no EU AI Act advisory disclaimer | EU AI Act Art. 50(1), GDPR Art. 13/14 transparency | **ADVISE** |
| 8 | No DSAR / data-subject-rights documentation for shared audit dirs | GDPR Art. 15-22, CCPA §1798.100 | **INFORM** |
| 9 | No export-control statement | US EAR / Wassenaar | **INFORM** |
| 10 | No FIPS / cryptography disclaimer | FIPS 140-3, FedRAMP guidance | **INFORM** |
| 11 | Author/email PII potentially echoed in `<file>.md` reports via LLM-generated `code_snippet` | GDPR Art. 5(1)(c), 32 | **ADVISE** |
| 12 | Network policy "loopback-only" enforced via opt-in flag, not bound socket | Defense in depth (SOC 2 CC6.6) | **INFORM** |

---

## Per-Framework Findings

### 1. GDPR (EU)

#### F-1.1 (ADVISE) — senex does process personal data, indirectly

senex ingests source files via `senex/walker.py` and feeds slices to a local LLM, then writes per-file Markdown reports under `<output.root>/<repo>/<DATE>-<run_id_short>/`. Source files routinely contain personal data: `__author__`, `__email__`, copyright headers, hardcoded test fixtures (`john@example.com`), and git-history-derived comments. The LLM may reproduce that text in its `overall_assessment`, `recommendations[].rationale`, and especially `recommendations[].code_snippet` (`senex/renderer.py:215-221`). Those strings are written to disk after passing only through `SecretRedactor`, which does not target PII (see F-4.1).

senex never *transmits* this data — outbound network is loopback-only — so the GDPR exposure is limited to **on-disk persistence on the user's machine**. That is still "processing" under GDPR Art. 4(2). For a tool published to PyPI:

- **Lawful basis is the user's, not senex's** — senex is a data-processor pattern (the user/organization is controller). Document this.
- **Data minimization** (Art. 5(1)(c)) — senex already minimizes via per-file scope, but the rendered reports inherit whatever the LLM chose to quote.

**Recommendation:** Add a README "Privacy" section explicitly stating: senex is local-only, processes whatever the user feeds it, retains output under user control, transmits nothing. Position senex as a tool the user runs against their own data, not a service.

**Files:** `README.md` (Security section, lines 258-277, is a partial start; rename or split out a Privacy section).

#### F-1.2 (BLOCK) — `output.retention_days` is not implemented

`senex/config.py:28` declares `retention_days: int = Field(default=0, ge=0)` and the field appears in `senex.config.toml.example:7`. A repo-wide search for `retention_days` returns exactly **one** match — the declaration. **No code reads or enforces the value.** Users who set `retention_days = 30` to satisfy a GDPR storage-limitation policy will silently retain forever.

This is a true compliance gap because the field is *advertised* as a control. GDPR Art. 5(1)(e) (storage limitation) and CCPA §1798.105 (deletion) both expect documented retention, and a configured-but-broken control is materially worse than an absent one — it is a misrepresentation.

Two acceptable fixes:
1. Implement a retention sweep on audit start that prunes `<output.root>/<repo>/` directories older than `retention_days`.
2. Remove the field from the schema and example, and document "users are responsible for pruning `<output.root>` per their retention policy."

**Files:** `senex/config.py:28`, `senex.config.toml.example:7`, `README.md` (no current mention).

#### F-1.3 (INFORM) — DSAR support is undocumented

If an admin runs senex against a corporate monorepo and the audit dir is shared with a team, a data subject (e.g., a former employee whose name appears in copyright headers) can request deletion under GDPR Art. 17. Because senex outputs are file-tree artifacts under a documented layout (`README.md:241-253`), this is a manual-but-tractable workflow: `grep -rli "<email>"` in the audit dir, then delete the matching `<relpath>.md` and re-run `senex aggregate`.

**Recommendation:** Add a "Data subject requests" subsection to README documenting the file layout for manual DSAR fulfillment, and noting that `findings.json` and `combined.md` may need rebuilding via `senex aggregate <audit-dir>` after deletions.

**Files:** `README.md` (add subsection under Privacy).

---

### 2. HIPAA (US, Healthcare)

#### F-2.1 (INFORM) — No ePHI is at meaningful risk, but state it

senex never reads patient data — it audits *source code*. A clinical-trial repo or EHR connector codebase may contain references to data structures, schemas, or test fixtures (e.g., `patient_id = "TEST-001"`), but the data plane is untouched. The local-only network posture and SecretRedactor cover the reasonable threat surface for HIPAA §164.312 technical safeguards.

**Recommendation:** README "Limitations" subsection should state: senex audits source code, not data. PHI in test fixtures is the user's responsibility; `[output].redact_secrets = true` is the default; for high-sensitivity codebases, run senex from an offline workstation under that organization's existing HIPAA controls.

**Files:** `README.md` (no current statement).

---

### 3. SOC 2

#### F-3.1 (ADVISE) — Logging is append-only, document it for adopters

`senex/subscribers/disk_writer.py` opens `events.jsonl` in `"ab"` (append-binary), writes one event per line, and `fsync()`s after every write (`disk_writer.py:64-73`). The dispatcher routes the DiskWriter under the "block" policy (`senex/events.py:500-502`), so a slow disk back-pressures the publisher rather than dropping events. **This is a strong CC7.2 (logging) control.** It needs to be documented for SOC 2-audited adopters who must show their auditor that an in-scope tool produces lossless evidence.

**Recommendation:** Document in the Security/SOC 2 section: events are append-only NDJSON, fsync-per-event, lossless under back-pressure, never overwritten. List every artifact and its append/atomic-replace policy.

#### F-3.2 (ADVISE) — Configuration as code, ready for evidence collection

The configuration schema is centralized in `senex/config.py` with `extra="forbid"` (refuses unknown keys with a Levenshtein hint), and the resolved config is snapshotted to `<audit_dir>/config.snapshot.toml` at run start (`senex/auditor.py:_snapshot_config`). This is a SOC 2 CC8.1 (change management) win: every audit dir is self-describing.

**Recommendation:** Document the snapshot file as the "evidence" artifact for audits.

#### F-3.3 (BLOCK) — README claims an HMAC that does not exist

`README.md:273-276`:
> Checkpoint integrity. `checkpoint.json` carries an HMAC over the resume hash bundle (`config_hash`, `prompt_hash`, `model_fingerprint`, `tool_pack_hash`, `lens_version`).

A repo-wide search for `hmac` (`grep "hmac\.|hmac_"` across `senex/`) returns **zero matches**. `senex/checkpoint.py` writes a plain JSON file with an atomic temp+rename pattern; the resume protection is hash-equality of the named fields, not an HMAC. There is no key, no `hmac.compare_digest`, no `hmac.new` anywhere in the codebase.

This is a SOC 2 CC1.4 / CC2.2 issue (accuracy of representations to users) and trust-undermining for any reader who diff-checks the README against the source. It also affects the EU AI Act / advisory-decision posture in F-7.1 — senex's documented "integrity" claim is more than the implementation supports.

Two acceptable fixes:
1. Implement HMAC. Generate a per-machine key under `~/.senex/keys/`, sign the resume hash bundle, verify on resume; document key management.
2. Rewrite the README claim as: "`checkpoint.json` carries a hash bundle; resume is refused on hash drift unless `--allow-mixed-resume` is set." Drop the word HMAC.

Option 2 is cheap and honest. Option 1 is meaningful integrity but requires key-management documentation.

**Files:** `README.md:273-276`, `senex/checkpoint.py`.

---

### 4. PCI DSS

#### F-4.1 (ADVISE) — SecretRedactor does not match cardholder data, IBAN, or SSN

`senex/secret_redactor.py:15-33` defines five patterns: PEM blocks, JWT, AWS access key, GitHub PAT, OpenAI/Anthropic LLM key, and a generic `KEY=value` env-style fallback. **It does NOT match:**

- Primary Account Numbers (PAN) — no Luhn-validated 13-19 digit pattern.
- IBAN (e.g., `GB82 WEST 1234 5698 7654 32`).
- US SSN (`\d{3}-\d{2}-\d{4}`).
- Email addresses.
- E.164 phone numbers.

If senex audits a payment-gateway codebase, hardcoded test PANs (`4111 1111 1111 1111`, the canonical test Visa) in fixtures will appear verbatim in `<file>.md` and `combined.md`. PCI DSS Requirement 3.4 (render PAN unreadable wherever stored) considers an audit report a storage location for PAN. Since the audit dir is on the user's machine and unencrypted, and senex writes the PAN through, **the user has now created a PCI-scope artifact they may not have anticipated**.

The mitigations are user-side (don't run senex against a PCI-scope codebase without scoping their audit dir into their CDE), but senex should at minimum:

1. Document that SecretRedactor is **secret-shaped-string** redaction, not PII redaction.
2. Optionally extend SecretRedactor with a configurable PII pack (off by default; enable with `[output].redact_pii = true`).

A Luhn pattern is small enough to cite verbatim:

```python
("pan_luhn", r"\b(?:\d[ -]?){12,18}\d\b")  # candidate; caller must Luhn-validate
```

But naive PAN regex without Luhn check produces high false-positive rates on any numeric-heavy code (versions, telemetry IDs, etc.). The conservative ship-now stance is **document the gap** (severity ADVISE), and treat PII redaction as a v1.1 feature.

**Files:** `senex/secret_redactor.py:15-33`, `README.md:264-268`.

---

### 5. FIPS 140-x / Cryptography

#### F-5.1 (INFORM) — senex makes no cryptographic claims; document it

senex uses `hashlib.sha256` for fingerprints and IDs (`senex/auditor.py:670`, `senex/cross_cutting.py:41`, `senex/findings_partial.py:45`, `senex/lens.py:190`, `senex/lmstudio_client.py:500`, `senex/lmstudio_lifecycle.py:115`, `senex/walker.py:186`, `senex/tools/pack_hash.py:46`). These are content-addressing hashes, not security primitives. There is no signing, no encryption, no key derivation, no MAC.

**Recommendation:** Add a one-line statement to README Security: "senex performs no cryptographic operations beyond `hashlib.sha256` content fingerprints. The local LLM is not a security oracle. senex is not a FIPS-validated module and makes no FIPS claims." This pre-empts adopters in FedRAMP / FISMA environments asking the wrong question.

**Files:** `README.md` (Security section).

---

### 6. EU AI Act

#### F-6.1 (ADVISE) — Add an "advisory output" disclaimer (Art. 50(1) transparency)

senex uses an LLM. Under the AI Act, code-quality/security analysis is **not** a high-risk Annex III use case (those are biometrics, critical infrastructure, employment decisions, etc.). senex therefore lands in the "minimal risk" tier, where the obligations are mostly transparency and human-oversight messaging.

The risk that *can* push senex toward "automated decision-making" is downstream usage: if a CI/CD pipeline blocks merges on `priority:"high"` findings, the senex output becomes a decision input. Mitigation is documentation, not architecture.

The system prompt at `senex/prompts/system_senior_dev.md` already orients toward "leads, not verdicts" (line 159: "treat findings as leads, not verdicts — verify each one") for the *model*. The README does not yet carry the equivalent disclaimer for the *user*.

**Recommendation:** README "Limitations" section should state explicitly:

> senex output is advisory. Findings are produced by a small open-weights LLM and may be wrong (false positives, missed defects, hallucinated line numbers). Senex is intended as a triage/triangulation aid for human review. Do not gate CI/CD on senex findings without a human-in-the-loop review step. senex makes no warranty of correctness, security, or completeness.

**Files:** `README.md`.

---

### 7. Open-Source Compliance / PyPI

#### F-7.1 (BLOCK) — No `LICENSE` file at repo root

`README.md:292`: "License: TBD (see LICENSE at repo root once added)." No `LICENSE`, `LICENSE.txt`, `LICENSE.md`, or `COPYING` file exists at `E:\senex\` root. PyPI accepts unlicensed uploads but the package is then legally unusable by adopters (default copyright = all rights reserved; users cannot legally execute, distribute, or even depend on it without express permission). The optional-deps in `pyproject.toml:26` (pytest, mypy, etc.) are MIT/BSD; the project itself is undeclared.

**Recommendation:** Adopt a permissive license (Apache-2.0 or MIT are typical for tooling). Add `license = "Apache-2.0"` (PEP 639) to `pyproject.toml` and drop `LICENSE` at repo root. If you choose Apache-2.0, also add a `NOTICE` file noting third-party deps.

**Files:** repo root (missing); `pyproject.toml:5-13`; `README.md:292`.

#### F-7.2 (BLOCK) — No `SECURITY.md`

No vulnerability-disclosure policy file exists. PyPI / GitHub adopt `SECURITY.md` as the canonical surface. SOC 2 CC7.4 expects a documented incident-reporting channel for any tool an organization adopts; without `SECURITY.md`, a SOC 2-audited team cannot cleanly inventory senex.

**Recommendation:** Add `SECURITY.md` with: supported versions, reporting channel (private email or GitHub Security Advisories), expected response time, scope (in: senex code; out: LM Studio, GitNexus, the audited code itself), CVSS bracketing.

**Files:** repo root (missing).

#### F-7.3 (ADVISE) — README missing "Privacy" / "Limitations" sections

A grep of `README.md` for `Privacy|Limitations|VULNERABILITY|CVE|disclosure` returns zero matches. Standard PyPI publishing checklist for an LLM-using tool expects all three of:

- A "Privacy" section (data flows, retention, third-party connections — even if all defaults are "none").
- A "Limitations" section (local-only by default, model-quality dependent, advisory output, no FIPS claims).
- A pointer to `SECURITY.md`.

The existing README "Security" section (lines 258-277) covers some of the technical posture but conflates security and privacy, and does not address the EU AI Act / advisory disclosure or limitation framing.

**Recommendation:** Restructure README footer as: Security (existing, fixed per F-3.3 and F-5.1) → Privacy (new, per F-1.1, F-1.3) → Limitations (new, per F-2.1, F-6.1) → License & contribution (existing) → SECURITY.md pointer.

**Files:** `README.md:258-294`.

---

### 8. Audit Logging / Tamper Evidence

#### F-8.1 (ADVISE) — Append-only is not the same as tamper-evident

`events.jsonl` is genuinely append-only: opened in `"ab"`, written one event per line, `fsync`'d (`senex/subscribers/disk_writer.py:50-73`). `findings.partial.jsonl` is similar streaming append. `combined.md`, `findings.json`, and `claude-handoff.md` are written via atomic replace through `senex/atomic_io.write_text_atomic`. **None of them are signed, hashed-and-anchored, or otherwise tamper-evident** against a user with write access to the audit dir.

For SOC 2 CC7.2 ("system events are logged") this is sufficient — append-only on a controlled host is the bar most auditors apply. For PCI DSS 10.5.2 ("protect audit trail files from unauthorized modifications") it is not — PCI explicitly expects file-integrity-monitoring or external SIEM ingest, neither of which senex provides or can provide as a local tool.

**Recommendation:** Two-part fix.

1. Document the gap explicitly in the SOC 2 / Security section: "Audit artifacts are append-only NDJSON or atomically-replaced. They are not cryptographically signed; integrity protection is the responsibility of the host filesystem and the user's backup/SIEM. Adopters under PCI DSS 10.5.2 should ship `events.jsonl` to an external append-only store."
2. As a v1.1 feature, consider emitting a `manifest.json` at run completion containing the SHA-256 of every artifact, and (optional) HMAC-signing it with a per-host key. This would close F-3.3 and F-8.1 simultaneously.

**Files:** `senex/subscribers/disk_writer.py`, `senex/atomic_io.py` (referenced but not read), `README.md`.

---

### 9. Export Controls

#### F-9.1 (INFORM) — Add a one-sentence non-controlled statement

senex is a defensive code-review tool. Under EAR §734.3 / Wassenaar Arrangement category 4.A.5 / 4.D.4 ("intrusion software"), the controlled definition requires the software to "extract data… from a computer or network-capable device" and to "modify the standard execution path… to allow the execution of externally provided instructions." senex does neither — it reads source files the user already has and produces Markdown reports. The LLM is not a network probe. The tool calls (`grep`, `read_file`, `search_code`, `gitnexus_*`) are all local-filesystem readers.

**Recommendation:** Add a single sentence to README Security: "senex is a defensive code-review tool. It is not 'intrusion software' under EAR §734.3 / Wassenaar Cat. 4.A.5; it does not extract data from remote systems and does not modify execution paths."

**Files:** `README.md` (no current statement).

---

### 10. Author/Email PII Handling (Cross-cutting)

#### F-10.1 (ADVISE) — Reports may quote `__author__` / `__email__` lines verbatim

The renderer emits `recommendations[].code_snippet` inside fenced code blocks (`senex/renderer.py:215-221`) and the LLM is free to quote source lines into `overall_assessment`, `findings[].issue`, and `findings[].why`. The system prompt at `senex/prompts/system_senior_dev.md:149` says "Do not repeat large source spans verbatim" — but a one-line `__author__ = "Jane Doe <jane@example.com>"` is small enough to be quoted as evidence of a "hardcoded credential" false positive (which a small model will sometimes flag).

The SecretRedactor will not catch this: emails are not in the pattern set (`senex/secret_redactor.py:15-33`).

**Recommendation:** Two mitigations, either is sufficient for ADVISE:

1. Add an email regex to `_PATTERNS` in `secret_redactor.py`: `("email", r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")`. Trade-off: false positives in legitimate documentation/test code, but the redaction marker is non-destructive.
2. Document: "senex outputs may contain author/email metadata copied from your source. Treat the audit dir as containing the same PII as the audited repo."

**Files:** `senex/secret_redactor.py:15-33`, `senex/prompts/system_senior_dev.md:149`.

---

### 11. Network Posture (Defense in Depth)

#### F-11.1 (INFORM) — "Loopback-only" is a config flag, not a bind

`senex/config.py:180` declares `allow_non_loopback: bool = False`, and `README.md:262-263` advertises "loopback-only by default." The enforcement is a string check on `base_url` somewhere upstream of `httpx.AsyncClient(base_url=...)` (`senex/lmstudio_client.py:266`). This is correct *policy* but it is policy in code, not policy in OS. A user who edits config to point `base_url` at a remote LM Studio and sets `allow_non_loopback = true` is fully on the hook.

This is fine for an open-source tool. Just document it accurately:

**Recommendation:** Update README Security to: "senex's default `[lmstudio].base_url` is `http://localhost:.../v1`. Setting a non-loopback URL requires explicit `allow_non_loopback = true`. There is no host-firewall enforcement; the loopback default is policy, not OS-level isolation."

**Files:** `senex/config.py:180`, `README.md:262-263`.

---

## Remediation Roadmap

**Pre-PyPI publish (BLOCK — must fix):**
1. F-7.1 — Add LICENSE (Apache-2.0 recommended) + `license` in `pyproject.toml`.
2. F-7.2 — Add `SECURITY.md`.
3. F-3.3 — Either implement HMAC or remove the HMAC claim from `README.md:273-276`.
4. F-1.2 — Either implement `output.retention_days` enforcement or remove the field from schema and example.

**Pre-PyPI publish (ADVISE — strongly recommended):**
5. F-7.3 — Add README "Privacy" + "Limitations" sections.
6. F-6.1 — Add advisory-output disclaimer (EU AI Act minimal risk).
7. F-1.1 — Document GDPR controller/processor posture.
8. F-4.1 — Document SecretRedactor scope (secret-shaped strings, not PII).
9. F-3.1 / F-3.2 — Document append-only logging and config snapshot for SOC 2 adopters.
10. F-8.1 — Document tamper-evidence boundary.
11. F-10.1 — Either add email regex to SecretRedactor or document the gap.

**Post-publish (INFORM — v1.1 polish):**
12. F-2.1 — HIPAA non-applicability statement.
13. F-9.1 — Export-control non-applicability statement.
14. F-5.1 — FIPS / no-crypto-claims statement.
15. F-1.3 — DSAR file-layout documentation.
16. F-11.1 — Network-policy precision.
