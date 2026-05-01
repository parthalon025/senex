# senex Ollama Backend + Rename Design

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Add Ollama as a first-class inference backend with full lifecycle management; rename `lmstudio_*` modules to backend-agnostic names; remove LM Studio SDK/CLI backends.

---

## Goals

1. Run `senex audit` against `gemma4:e4b` via Ollama with full feature parity: structured output, tool calling, thinking mode, lifecycle management.
2. Rename all `lmstudio_*` file/class/config names to backend-agnostic equivalents.
3. Remove `LMStudioSDKBackend` and `LMSCLIBackend` — supported backends become **Ollama** and **SGLang** only.
4. Zero behavior regression for existing SGLang users.

---

## Non-Goals

- Ollama cloud / remote Ollama instances
- Multi-backend parallel benchmarking (separate feature)
- Restoring LM Studio backends later (tracked in CHANGELOG as intentional removal)

---

## Rename Map

| Old name | New name | Notes |
|---|---|---|
| `senex/lmstudio_client.py` | `senex/inference_client.py` | File rename |
| `senex/lmstudio_lifecycle.py` | `senex/inference_lifecycle.py` | File rename |
| `senex/lmstudio_errors.py` | `senex/inference_errors.py` | File rename |
| `LmStudioCfg` | `InferenceCfg` | Config class |
| `LMStudioSDKBackend` | *(deleted)* | Removed |
| `LMSCLIBackend` | *(deleted)* | Removed |
| `HTTPBackend` | `SGLangBackend` | Renamed for clarity |
| Config key `[lmstudio]` | `[inference]` | TOML key; old key aliased with deprecation warning for one version |
| `LifecycleBackendUnavailable` | unchanged | Keep |

All import sites updated via `gitnexus_rename` dry-run → apply. Tests updated in same pass.

---

## Config Changes

### New `OllamaCfg` section

Added nested under `InferenceCfg` alongside existing `SglangCfg`:

```toml
[inference.ollama]
# Lifecycle
manage_process    = true                       # start/stop 'ollama serve'
pull_on_start     = true                       # run 'ollama pull <model>' if not cached
startup_timeout_s = 60

# Connection
base_url          = "http://localhost:11434"
model             = "gemma4:e4b"

# Capabilities
thinking          = true                       # inject <|think|> into system prompt
strict_json_schema = false                     # set true if Ollama adds native json_schema support later
```

`InferenceCfg.backend` field added (default `"auto"`): `"ollama"` | `"sglang"` | `"auto"`.
Auto-selection order: Ollama (if `inference.ollama.base_url` reachable) → SGLang.

---

## Architecture

```
senex audit
    └── phases/file_audit.py
            └── inference_client.py  ← unchanged interface
                    ├── _build_request()     ← NEW: backend-aware schema translation
                    ├── _stream_chat()       ← unchanged
                    └── inference_lifecycle.py
                            ├── OllamaBackend    ← NEW
                            └── SGLangBackend    ← renamed from HTTPBackend
```

---

## `OllamaBackend` (new class in `inference_lifecycle.py`)

Implements the existing `LifecycleBackend` protocol:

```python
class OllamaBackend:
    async def is_loaded(self, model_id: str) -> bool
    async def load(self, model_id: str, timeout: int) -> ModelInfo
    async def unload(self, model_id: str) -> None
    async def list_loaded(self) -> list[ModelInfo]
```

### `probe()` (class method — used by LifecycleBackendFactory auto-detection)

`GET <base_url>/` → returns `True` if HTTP 200, `False` otherwise. No timeout beyond connect (2s).

### `load()` sequence

1. Health check: `GET /` → raises `LifecycleBackendUnavailable` if unreachable
2. If `manage_process=True` and health check fails: spawn `ollama serve` as subprocess; poll `/` every 2s until 200 or `startup_timeout_s` exceeded
3. If `pull_on_start=True`: `POST /api/pull {name: model_id, stream: true}` — stream NDJSON progress → emit `ModelPullProgress` events on bus; skip if model already in `GET /api/tags` response
4. Verify loaded: `GET /api/ps` → check `models[].name` contains model_id
5. If not loaded: warm up via `POST /api/chat {model, messages: [{role:"user", content:"hi"}], keep_alive: "10m"}` → discard response
6. Extract fingerprint from `POST /api/show {name: model_id}` → `details.digest`

### `unload()` sequence

`POST /api/chat {model, messages: [{role:"user", content:""}], keep_alive: 0}` — forces Ollama to evict from VRAM immediately.

### `is_loaded()`

`GET /api/ps` → `models[].name` contains model_id.

### Fingerprinting

`POST /api/show {name: model_id}` → `details.digest` (stable per model version). Cached for 60s, same policy as SGLang.

---

## `inference_client.py` changes

### Schema translation (critical — validated via GitHub #10001)

Ollama's `/v1/chat/completions` endpoint **ignores** `response_format: {type: "json_schema", json_schema: {...}}`. It uses a different `format` parameter instead.

`InferenceClient` receives a `backend_type: Literal["ollama", "sglang"]` string from `LifecycleBackendFactory.select()` — stored on the client instance at construction time so `_build_request()` can branch without additional I/O.

New `_build_request()` method detects backend type and translates:

```python
def _build_request(self, messages, schema, tools, backend_type):
    body = {"model": self.cfg.model, "messages": messages}
    
    if backend_type == "ollama":
        # Ollama native: format parameter with raw schema
        if schema:
            body["format"] = schema.model_json_schema()
        # Tools pass through unchanged (OpenAI format supported)
        if tools:
            body["tools"] = tools
    else:
        # SGLang: standard OpenAI response_format
        if schema:
            if self.cfg.strict_json_schema:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "audit_response", "schema": schema.model_json_schema(), "strict": True}}
            else:
                body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"] = tools
    return body
```

### Thinking activation

SGLang uses `extra_body={"chat_template_kwargs": {"enable_thinking": True}}`.
Ollama uses `<|think|>` injected at the start of the system prompt.

```python
def _inject_thinking(self, messages, backend_type, thinking_enabled):
    if not thinking_enabled:
        return messages
    if backend_type == "ollama":
        # Prepend <|think|> to system message content
        msgs = list(messages)
        if msgs and msgs[0]["role"] == "system":
            msgs[0] = {**msgs[0], "content": "<|think|>\n" + msgs[0]["content"]}
        return msgs
    else:
        # SGLang: handled via extra_body, no message mutation
        return messages
```

Existing inline `<think>...</think>` streaming extraction already handles gemma4 output — no changes to the streaming phase detector.

### Schema negotiation fallback

Current fallback: if `json_schema` returns HTTP 400, retry with `json_object`. For Ollama: if `format: {schema}` returns HTTP 400, retry with `format: "json"` + post-hoc Pydantic validation (same as existing `json_object` path). The `strict_json_schema` config flag is unused for Ollama (always native format mode).

---

## `senex doctor` additions

Two new checks when `inference.ollama` config is present:

| Check ID | Command | Pass criteria |
|---|---|---|
| `ollama_reachable` | `GET /` | HTTP 200 |
| `ollama_model_available` | `POST /api/show {name: model}` | `status=200`, no error |

Existing `lmstudio_reachable` check renamed `inference_reachable`, routed to the active backend.

---

## `LifecycleBackendFactory` changes

Simplified to two candidates (LM Studio backends removed):

```python
@staticmethod
async def select(cfg: InferenceCfg) -> LifecycleBackend:
    if cfg.backend == "ollama" or (cfg.backend == "auto" and await OllamaBackend.probe(cfg.ollama.base_url)):
        return OllamaBackend(cfg.ollama)
    return SGLangBackend(cfg)
```

`LMStudioSDKBackend` and `LMSCLIBackend` classes deleted. Their removal noted in CHANGELOG as intentional.

---

## File Changes Summary

| File | Change |
|---|---|
| `senex/lmstudio_client.py` → `senex/inference_client.py` | Rename + `_build_request()` + `_inject_thinking()` |
| `senex/lmstudio_lifecycle.py` → `senex/inference_lifecycle.py` | Rename + add `OllamaBackend` + rename `HTTPBackend` → `SGLangBackend` + delete LMS backends |
| `senex/lmstudio_errors.py` → `senex/inference_errors.py` | Rename only |
| `senex/config.py` | Add `OllamaCfg`, rename `LmStudioCfg` → `InferenceCfg`, add `[inference]` key alias |
| `senex/cli.py` | Update imports |
| `senex/phases/*.py` | Update imports |
| `senex/subscribers/*.py` | Update imports |
| `tests/` | Update imports; add `tests/unit/test_ollama_backend.py` |
| `senex.config.toml.example` | Add `[inference.ollama]` section |

---

## Test Plan

| Test file | What it covers |
|---|---|
| `tests/unit/test_ollama_backend.py` | `OllamaBackend.load/unload/is_loaded/list_loaded` against httpx mock |
| `tests/unit/test_inference_client_schema_translation.py` | `_build_request()` produces correct `format` vs `response_format` per backend |
| `tests/unit/test_inference_client_thinking_injection.py` | `_inject_thinking()` mutates system prompt for Ollama, uses extra_body for SGLang |
| `tests/unit/test_ollama_doctor_checks.py` | `ollama_reachable`, `ollama_model_available` pass/fail cases |
| Existing recorded/ tests | Must still pass — SGLang path unchanged |

---

## Migration Notes

- Config files using `[lmstudio]` will get a deprecation warning on load; they continue to work for one version. Rename to `[inference]` to silence.
- `lmstudio_reachable` doctor check renamed to `inference_reachable` in `doctor --json` output.
- Anyone using `LMStudioSDKBackend` or `LMSCLIBackend` directly (not via factory) gets an `ImportError` — breaking change, noted in CHANGELOG.

---

## Sources

- [Ollama Structured Outputs docs](https://docs.ollama.com/capabilities/structured-outputs)
- [Ollama json_schema compatibility gap — GitHub #10001](https://github.com/ollama/ollama/issues/10001)
- [gemma4:e4b on Ollama](https://ollama.com/library/gemma4:e4b)
- [Ollama API reference](https://docs.ollama.com/api/introduction)
