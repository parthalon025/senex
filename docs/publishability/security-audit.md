# senex Security Audit (read-only)

Auditor: security-auditor | Date: 2026-04-29
Verdict: Strong overall posture. 1 CRITICAL + 3 HIGH must be fixed pre-publish.

## Findings table

| ID | Severity | Title | File:line | Remediation |
|----|----------|-------|-----------|-------------|
| SSRF-1 | **CRITICAL** | Loopback URL parser bypassable via userinfo at-segment | senex/phases/preflight.py:319-327 | Replace string-split with urlparse(...).hostname; resolve and reject non-loopback A/AAAA |
| SSRF-2 | High | CLAUDE_CONTEXT_URL has no loopback validation | senex/tools/search_code.py:56 | Apply same loopback check |
| DEP-1 | High | 8 dependencies completely unpinned | pyproject.toml:10-23 | Add `>=X.Y,<X+1`; ship requirements-lock.txt |
| DEP-2 | High | requirements.txt missing pathspec | requirements.txt vs pyproject.toml:22 | Regenerate or remove |
| SSRF-3 | Medium | Authorization header sent with potentially-leaked api_key on non-loopback | lmstudio_client.py:274 | Skip auth header on confirmed loopback |
| SSRF-4 | Medium | _wait_for_health polls unvalidated base_url | lmstudio_lifecycle.py:454-466 | Fold under SSRF-1 |
| SEC-1 | Medium | events.jsonl not run through SecretRedactor | subscribers/disk_writer.py:54-73 | Redact event string fields at write time |
| SEC-2 | Medium | findings.json aggregator does not re-redact | findings_aggregator.py:122-126 | Apply redactor in _dump_finding |
| DEP-3 | Medium | ulid-py deprecated/unmaintained | pyproject.toml:21 | Migrate to python-ulid |
| DEP-4 | Medium | detect-secrets unpinned (historical CVEs) | pyproject.toml:16 | Pin >=1.5,<2 |
| SUB-1 | Low | _docker_argv splits action on whitespace | lmstudio_lifecycle.py:419 | Change to action: list[str] |
| SUB-2 | Low | wsl_distro / compose_file / env_file unvalidated | lmstudio_lifecycle.py:415-422 | Add safety regexes |
| SSRF-5 | Low | 600s read timeout + 10s connect can stall worker if non-loopback | lmstudio_client.py:266-275 | Document |
| PATH-1 | Low | Renderer write_file_atomic does not re-validate relpath under audit_dir | renderer.py:408 | Add resolve-and-check defense-in-depth |
| PATH-2 | Low | repo_path not symlink-rejected | walker.py:107 | Document trust assumption |
| TOCTOU-1 | Low | Walker symlink check has microsecond TOCTOU window | walker.py:155-166 | Document |
| RES-1 | Low | repo_root.rglob(glob) not upper-bounded | tools/grep.py:108 | Cap candidate list at 100k |
| RES-2 | Low | Combined HTTP retry backoff up to ~65s | lmstudio_client.py:579-600 | Document |
| SEC-4 | Low | Thinking flag uses substring match 'gemma' in model | lmstudio_client.py:331 | Match against allow-list |
| DEP-5 | Low | Dev-extras have no upper bounds | pyproject.toml:26 | Add upper bounds |
| DEP-6 | Low | SGLang container uses :latest tag | infra/sglang/docker-compose.yml:3 | Pin to digest or tag |

## What senex got right (preserve in regression tests)

1. Subprocess discipline - every create_subprocess_exec is list-form; no shell=True anywhere
2. Pydantic extra='forbid' uniform across config / events / tool I/O; Levenshtein typo-suggester
3. Path-safety primitive (validate_repo_path) walks ancestors for symlink rejection BEFORE resolve()
4. Atomic writes everywhere (tmp -> fsync -> os.replace); per-line flush+fsync for streaming NDJSON
5. Regex hardening: length cap + ReDoS-shape rejection + per-call timeout
6. SecretRedactor at every disk-bound boundary
7. Resource caps everywhere - token budget, max files, max tool calls, max compactions, regex timeouts
8. No unsafe deserialization / dynamic-code primitives anywhere in senex/. Pure stdlib tomllib + json
9. Lifecycle ownership invariants belt-and-suspenders (ResumedRunCannotOwnLoad defense)
10. Cross-platform PID liveness for runlocks (POSIX os.kill + Windows OpenProcess)

## Remediation roadmap

Before PyPI publication (BLOCKING):
1. SSRF-1 (Critical) - replace string-split URL parsing with urlparse, resolve hostname, reject non-loopback IPs
2. SSRF-2 (High) - apply loopback validator to CLAUDE_CONTEXT_URL
3. DEP-1 (High) - pin every dependency
4. DEP-2 (High) - sync requirements.txt with pyproject.toml (or delete requirements.txt)
5. DEP-3 (Medium) - migrate off ulid-py

First patch release:
6. SEC-1, SEC-2 - redact events.jsonl + findings aggregator
7. SSRF-3, SSRF-4 - auth header / health probe loopback
8. DEP-4 - pin detect-secrets
