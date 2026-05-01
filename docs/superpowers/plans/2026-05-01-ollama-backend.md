# Ollama Backend + Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Ollama as a first-class inference backend with full lifecycle management; rename `lmstudio_*` modules to backend-agnostic names; remove LM Studio SDK/CLI backends.

**Architecture:** Three source files are renamed (lmstudio_client → inference_client, lmstudio_lifecycle → inference_lifecycle, lmstudio_errors → inference_errors); config gains OllamaCfg and a backend selector; InferenceClient gains backend-aware schema translation and thinking injection; a new OllamaBackend class handles lifecycle; LifecycleBackendFactory is simplified to two candidates.

**Tech Stack:** Python 3.11+, httpx, pydantic v2, asyncio, TOML config, Ollama REST API (localhost:11434)

---

## File Map

| File | Action |
|---|---|
| `senex/lmstudio_errors.py` → `senex/inference_errors.py` | Rename only (git mv) |
| `senex/lmstudio_client.py` → `senex/inference_client.py` | Rename + new methods |
| `senex/lmstudio_lifecycle.py` → `senex/inference_lifecycle.py` | Rename + OllamaBackend + SGLangBackend + delete LMS backends |
| `senex/config.py` | Add OllamaCfg, rename LmStudioCfg→InferenceCfg, alias [lmstudio] key |
| `senex/cli.py`, `senex/cli_audit.py`, `senex/cli_doctor.py`, etc. | Import site updates |
| `tests/unit/test_ollama_backend.py` | New test file |
| `tests/unit/test_inference_client_ollama.py` | New test file |
| `tests/unit/test_ollama_doctor_checks.py` | New test file |
| `senex.config.toml.example` | Add `[inference.ollama]` section |
| `CHANGELOG.md` | Document breaking changes |

---

## Task 1: Rename source files (git mv)

**Files:**
- Rename: `senex/lmstudio_errors.py` → `senex/inference_errors.py`
- Rename: `senex/lmstudio_client.py` → `senex/inference_client.py`
- Rename: `senex/lmstudio_lifecycle.py` → `senex/inference_lifecycle.py`

- [ ] **Step 1: Rename the three files**

```bash
git mv senex/lmstudio_errors.py senex/inference_errors.py
git mv senex/lmstudio_client.py senex/inference_client.py
git mv senex/lmstudio_lifecycle.py senex/inference_lifecycle.py
```

- [ ] **Step 2: Verify git tracks the renames**

```bash
git status
```

Expected: three `renamed:` entries in the staged area.

- [ ] **Step 3: Update internal self-references inside inference_lifecycle.py**

In `senex/inference_lifecycle.py`, find any `from senex.lmstudio_errors import` or `from senex.lmstudio_client import` and update them to use the new module names. (The file may also import from itself indirectly via type annotations.)

- [ ] **Step 4: Update internal self-references inside inference_client.py**

In `senex/inference_client.py`, update:
```python
# OLD
from senex.lmstudio_errors import ...
# NEW
from senex.inference_errors import ...
```

And any `from senex.lmstudio_lifecycle import ...` → `from senex.inference_lifecycle import ...`

- [ ] **Step 5: Run the full test suite to establish a rename-only baseline**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: same pass/fail count as before (tests will fail on import errors if any import site was missed — fix those before moving on).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: git mv lmstudio_* → inference_* (rename only, no logic changes)"
```

---

## Task 2: Update all import sites

**Files:**
- Modify: `senex/llm_client.py`
- Modify: `senex/auditor.py`
- Modify: `senex/cli_audit.py`
- Modify: `senex/cli_wizard.py`
- Modify: `senex/cli_doctor.py`
- Modify: `senex/lifecycle_cli.py`
- Modify: `senex/compaction.py`
- Modify: `senex/cross_cutting.py`
- Modify: `senex/phases/preflight.py`
- Modify: `senex/phases/file_audit.py`
- Modify: `senex/tui/launcher.py`
- Modify: `senex/tools/loop.py`
- Test: run `pytest tests/ -x -q`

- [ ] **Step 1: Find all remaining import sites**

```bash
grep -rn "lmstudio_client\|lmstudio_lifecycle\|lmstudio_errors" senex/ --include="*.py"
```

Every line returned is an import site that needs updating.

- [ ] **Step 2: Replace all occurrences across the codebase**

For each file found in Step 1, replace:
- `from senex.lmstudio_client import` → `from senex.inference_client import`
- `from senex.lmstudio_lifecycle import` → `from senex.inference_lifecycle import`
- `from senex.lmstudio_errors import` → `from senex.inference_errors import`
- `import senex.lmstudio_client` → `import senex.inference_client`
- `import senex.lmstudio_lifecycle` → `import senex.inference_lifecycle`

- [ ] **Step 3: Update test import sites**

```bash
grep -rn "lmstudio_client\|lmstudio_lifecycle\|lmstudio_errors" tests/ --include="*.py"
```

Apply the same substitutions to every test file found.

- [ ] **Step 4: Verify no `lmstudio_` module references remain**

```bash
grep -rn "lmstudio_client\|lmstudio_lifecycle\|lmstudio_errors" senex/ tests/ --include="*.py"
```

Expected: zero output.

- [ ] **Step 5: Run the full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: same pass count as Task 1 baseline.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: update all import sites lmstudio_* → inference_*"
```

---

## Task 3: Rename classes (LmStudioCfg → InferenceCfg, LMStudioClient → InferenceClient, HTTPBackend → SGLangBackend)

**Files:**
- Modify: `senex/config.py`
- Modify: `senex/inference_client.py`
- Modify: `senex/inference_lifecycle.py`
- Modify: all sites that reference `LmStudioCfg`, `LMStudioClient`, `HTTPBackend`
- Test: `tests/unit/test_config*.py`, `tests/unit/test_inference_client*.py`

- [ ] **Step 1: Write a failing test for the InferenceCfg alias in config.py**

Add to `tests/unit/test_config.py` (or create it if absent):

```python
def test_inference_cfg_is_accessible():
    from senex.config import InferenceCfg
    cfg = InferenceCfg()
    assert cfg.base_url == "http://localhost:30000/v1"

def test_lmstudio_cfg_alias_still_works():
    # LmStudioCfg must remain importable for one version (deprecation alias)
    from senex.config import LmStudioCfg
    assert LmStudioCfg is not None
```

Run: `pytest tests/unit/test_config.py::test_inference_cfg_is_accessible -v`
Expected: FAIL with `ImportError: cannot import name 'InferenceCfg'`

- [ ] **Step 2: Rename LmStudioCfg to InferenceCfg in config.py**

In `senex/config.py`:
1. Change `class LmStudioCfg(_StrictModel):` → `class InferenceCfg(_StrictModel):`
2. Change `SenexConfig.lmstudio: LmStudioCfg` → `SenexConfig.inference: InferenceCfg`  
   (keep `lmstudio` as a deprecated alias — see Task 4 for the key alias logic)
3. Change `RepoCfg.lmstudio: dict[str, Any] | None` → `RepoCfg.inference: dict[str, Any] | None`
4. Add deprecation alias at module bottom:
   ```python
   # Deprecated alias — remove after one release cycle
   LmStudioCfg = InferenceCfg
   ```
5. Update `resolve_config()` — change `repo.lmstudio` → `repo.inference` and `{"lmstudio": ...}` → `{"inference": ...}`

- [ ] **Step 3: Run the failing test — it should now pass**

```bash
pytest tests/unit/test_config.py::test_inference_cfg_is_accessible tests/unit/test_config.py::test_lmstudio_cfg_alias_still_works -v
```

Expected: both PASS.

- [ ] **Step 4: Rename LMStudioClient to InferenceClient in inference_client.py**

In `senex/inference_client.py`:
1. Change `class LMStudioClient:` → `class InferenceClient:`
2. Add alias at module bottom: `LMStudioClient = InferenceClient`
3. Update any `__init__` or factory references that refer to `LMStudioClient` by name.

- [ ] **Step 5: Rename HTTPBackend to SGLangBackend in inference_lifecycle.py**

In `senex/inference_lifecycle.py`:
1. Change `class HTTPBackend:` → `class SGLangBackend:`
2. Change `backend_name = "http"` → `backend_name = "sglang"` (if present)
3. Add alias: `HTTPBackend = SGLangBackend`
4. In `LifecycleBackendFactory.select()`, change `return HTTPBackend(cfg)` → `return SGLangBackend(cfg)`

- [ ] **Step 6: Update all class-name reference sites**

```bash
grep -rn "LMStudioClient\|LmStudioCfg\|HTTPBackend" senex/ tests/ --include="*.py"
```

For each site that is NOT the alias definition itself, update to use the new name. Leave the alias lines as-is.

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: same pass count as Task 2.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: rename LmStudioCfg→InferenceCfg, LMStudioClient→InferenceClient, HTTPBackend→SGLangBackend"
```

---

## Task 4: Config key alias ([lmstudio] → [inference]) + OllamaCfg

**Files:**
- Modify: `senex/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_config.py`:

```python
import warnings
from pathlib import Path
import tomllib, tempfile, textwrap

def _write_toml(content: str) -> Path:
    p = Path(tempfile.mktemp(suffix=".toml"))
    p.write_text(textwrap.dedent(content))
    return p

def test_inference_toml_key_loads():
    p = _write_toml("""
        [inference]
        base_url = "http://localhost:30000/v1"
    """)
    from senex.config import load_config
    cfg = load_config(p)
    assert cfg.inference.base_url == "http://localhost:30000/v1"

def test_lmstudio_toml_key_emits_deprecation_warning():
    p = _write_toml("""
        [lmstudio]
        base_url = "http://localhost:1234/v1"
    """)
    from senex.config import load_config
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cfg = load_config(p)
    assert cfg.inference.base_url == "http://localhost:1234/v1"
    assert any("lmstudio" in str(x.message).lower() for x in w)

def test_ollama_cfg_defaults():
    from senex.config import OllamaCfg
    cfg = OllamaCfg()
    assert cfg.base_url == "http://localhost:11434"
    assert cfg.model == "gemma4:e4b"
    assert cfg.manage_process is True
    assert cfg.pull_on_start is True

def test_inference_cfg_has_backend_and_ollama():
    from senex.config import InferenceCfg
    cfg = InferenceCfg()
    assert cfg.backend == "auto"
    assert cfg.ollama.base_url == "http://localhost:11434"
```

Run: `pytest tests/unit/test_config.py -k "inference_toml or lmstudio_toml or ollama_cfg or inference_cfg_has" -v`
Expected: all four FAIL.

- [ ] **Step 2: Add OllamaCfg class to config.py**

Add this class in `senex/config.py` just before `InferenceCfg`:

```python
class OllamaCfg(_StrictModel):
    manage_process: bool = True
    pull_on_start: bool = True
    startup_timeout_s: int = Field(default=60, gt=0)
    base_url: str = "http://localhost:11434"
    model: str = "gemma4:e4b"
    thinking: bool = True
    strict_json_schema: bool = False
```

- [ ] **Step 3: Add backend field and ollama sub-config to InferenceCfg**

In `senex/config.py`, add to `InferenceCfg` (formerly `LmStudioCfg`):

```python
backend: Literal["ollama", "sglang", "auto"] = "auto"
ollama: OllamaCfg = Field(default_factory=OllamaCfg)
```

Place `backend` and `ollama` after the existing fields (before `sampling`).

- [ ] **Step 4: Update load_config() to translate [lmstudio] → [inference] with a warning**

In `senex/config.py`, update `load_config()`:

```python
def load_config(path: Path) -> SenexConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    if "lmstudio" in data and "inference" not in data:
        import warnings
        warnings.warn(
            "Config key [lmstudio] is deprecated; rename to [inference] to silence this warning.",
            DeprecationWarning,
            stacklevel=2,
        )
        data["inference"] = data.pop("lmstudio")
    elif "lmstudio" in data and "inference" in data:
        import warnings
        warnings.warn(
            "Both [lmstudio] and [inference] keys present; [lmstudio] is ignored.",
            DeprecationWarning,
            stacklevel=2,
        )
        data.pop("lmstudio")
    try:
        return SenexConfig.model_validate(data)
    except ValidationError as exc:
        # ... existing error handling unchanged ...
```

Also rename `SenexConfig.lmstudio` field to `SenexConfig.inference`:
```python
class SenexConfig(_StrictModel):
    output: OutputCfg = Field(default_factory=OutputCfg)
    lens: LensCfg = Field(default_factory=LensCfg)
    walker: WalkerCfg = Field(default_factory=WalkerCfg)
    crosscut: CrosscutCfg = Field(default_factory=CrosscutCfg)
    inference: InferenceCfg = Field(default_factory=InferenceCfg)
    ui: UICfg = Field(default_factory=UICfg)
    repos: list[RepoCfg] = Field(default_factory=list)
```

- [ ] **Step 5: Update all senex/ code that accesses cfg.lmstudio → cfg.inference**

```bash
grep -rn "\.lmstudio\b\|cfg\.lmstudio\|config\.lmstudio\|base\.lmstudio" senex/ --include="*.py"
```

Each occurrence must be changed to `.inference`. Common patterns:
- `cfg.lmstudio.base_url` → `cfg.inference.base_url`
- `config.lmstudio` → `config.inference`
- `{"lmstudio": repo.lmstudio}` in `resolve_config()` → `{"inference": repo.inference}`

- [ ] **Step 6: Run the four new tests**

```bash
pytest tests/unit/test_config.py -k "inference_toml or lmstudio_toml or ollama_cfg or inference_cfg_has" -v
```

Expected: all four PASS.

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: same or better pass count (some tests that previously tested `cfg.lmstudio` may need updating to `cfg.inference`).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(config): add OllamaCfg, InferenceCfg.backend, [lmstudio]->[inference] alias with DeprecationWarning"
```

---

## Task 5: Implement OllamaBackend

**Files:**
- Modify: `senex/inference_lifecycle.py`
- Create: `tests/unit/test_ollama_backend.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_ollama_backend.py`:

```python
"""Tests for OllamaBackend lifecycle operations."""
import pytest
import httpx
import respx

from senex.config import OllamaCfg
from senex.inference_lifecycle import OllamaBackend


@pytest.fixture
def cfg() -> OllamaCfg:
    return OllamaCfg(
        base_url="http://localhost:11434",
        model="gemma4:e4b",
        manage_process=False,
        pull_on_start=False,
    )


@respx.mock
@pytest.mark.asyncio
async def test_probe_returns_true_when_reachable(cfg):
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(200))
    assert await OllamaBackend.probe("http://localhost:11434") is True


@respx.mock
@pytest.mark.asyncio
async def test_probe_returns_false_when_unreachable(cfg):
    respx.get("http://localhost:11434/").mock(side_effect=httpx.ConnectError("refused"))
    assert await OllamaBackend.probe("http://localhost:11434") is False


@respx.mock
@pytest.mark.asyncio
async def test_is_loaded_true_when_model_in_ps(cfg):
    respx.get("http://localhost:11434/api/ps").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "gemma4:e4b"}]}
    ))
    backend = OllamaBackend(cfg)
    assert await backend.is_loaded("gemma4:e4b") is True


@respx.mock
@pytest.mark.asyncio
async def test_is_loaded_false_when_model_absent(cfg):
    respx.get("http://localhost:11434/api/ps").mock(return_value=httpx.Response(
        200, json={"models": []}
    ))
    backend = OllamaBackend(cfg)
    assert await backend.is_loaded("gemma4:e4b") is False


@respx.mock
@pytest.mark.asyncio
async def test_load_raises_when_health_fails_and_no_manage_process(cfg):
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(503))
    from senex.inference_errors import LifecycleBackendUnavailable
    backend = OllamaBackend(cfg)
    with pytest.raises(LifecycleBackendUnavailable):
        await backend.load("gemma4:e4b", timeout=10)


@respx.mock
@pytest.mark.asyncio
async def test_load_succeeds_with_warmup(cfg):
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(200))
    respx.get("http://localhost:11434/api/tags").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "gemma4:e4b"}]}
    ))
    respx.get("http://localhost:11434/api/ps").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "gemma4:e4b"}]}
    ))
    respx.post("http://localhost:11434/api/show").mock(return_value=httpx.Response(
        200, json={"details": {"digest": "sha256:abc123"}}
    ))
    backend = OllamaBackend(cfg)
    info = await backend.load("gemma4:e4b", timeout=10)
    assert info.model_id == "gemma4:e4b"
    assert info.checkpoint_digest == "sha256:abc123"


@respx.mock
@pytest.mark.asyncio
async def test_unload_sends_keep_alive_zero(cfg):
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json={})
    )
    backend = OllamaBackend(cfg)
    await backend.unload("gemma4:e4b")
    assert route.called
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["keep_alive"] == 0


@respx.mock
@pytest.mark.asyncio
async def test_list_loaded_returns_running_models(cfg):
    respx.get("http://localhost:11434/api/ps").mock(return_value=httpx.Response(
        200, json={"models": [{"name": "gemma4:e4b"}, {"name": "llama3:8b"}]}
    ))
    backend = OllamaBackend(cfg)
    models = await backend.list_loaded()
    assert len(models) == 2
    assert models[0].model_id == "gemma4:e4b"
```

Run: `pytest tests/unit/test_ollama_backend.py -v`
Expected: all FAIL with `ImportError: cannot import name 'OllamaBackend'`

- [ ] **Step 2: Implement OllamaBackend in inference_lifecycle.py**

Add the following class to `senex/inference_lifecycle.py` (add necessary imports: `import asyncio`, `import subprocess`, `import json`, `import httpx`):

```python
class OllamaBackend:
    """Lifecycle backend for a locally-running Ollama server."""

    backend_name = "ollama"

    def __init__(self, cfg: "OllamaCfg") -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(base_url=cfg.base_url, timeout=10.0)
        self._digest_cache: dict[str, tuple[float, str]] = {}

    @classmethod
    async def probe(cls, base_url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(base_url + "/")
                return r.status_code == 200
        except Exception:
            return False

    async def is_loaded(self, model_id: str) -> bool:
        r = await self._client.get("/api/ps")
        r.raise_for_status()
        return any(m["name"] == model_id for m in r.json().get("models", []))

    async def load(self, model_id: str, timeout: int) -> "ModelInfo":
        from senex.inference_errors import LifecycleBackendUnavailable
        try:
            health = await self._client.get("/")
            available = health.status_code == 200
        except Exception:
            available = False

        if not available:
            if self._cfg.manage_process:
                await self._start_serve(timeout)
            else:
                raise LifecycleBackendUnavailable(
                    f"Ollama not reachable at {self._cfg.base_url} and manage_process=False"
                )

        if self._cfg.pull_on_start:
            await self._pull_model(model_id)

        if not await self.is_loaded(model_id):
            await self._warmup(model_id)

        digest = await self._get_digest(model_id)
        return ModelInfo(
            model_id=model_id,
            quant="",
            checkpoint_digest=digest,
            fingerprint=digest,
            backend=self.backend_name,
        )

    async def unload(self, model_id: str) -> None:
        await self._client.post("/api/chat", json={
            "model": model_id,
            "messages": [{"role": "user", "content": ""}],
            "keep_alive": 0,
        })

    async def list_loaded(self) -> list["ModelInfo"]:
        r = await self._client.get("/api/ps")
        r.raise_for_status()
        return [
            ModelInfo(model_id=m["name"], quant="", checkpoint_digest="",
                      fingerprint="", backend=self.backend_name)
            for m in r.json().get("models", [])
        ]

    async def _start_serve(self, timeout: int) -> None:
        import subprocess as sp
        sp.Popen(["ollama", "serve"], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        for _ in range(timeout // 2):
            await asyncio.sleep(2)
            if await self.probe(self._cfg.base_url):
                return
        from senex.inference_errors import LifecycleBackendUnavailable
        raise LifecycleBackendUnavailable("ollama serve did not become healthy in time")

    async def _warmup(self, model_id: str) -> None:
        await self._client.post("/api/chat", json={
            "model": model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "keep_alive": "10m",
        })

    async def _get_digest(self, model_id: str) -> str:
        import time
        now = time.monotonic()
        if model_id in self._digest_cache:
            ts, digest = self._digest_cache[model_id]
            if now - ts < 60:
                return digest
        r = await self._client.post("/api/show", json={"name": model_id})
        r.raise_for_status()
        digest = r.json().get("details", {}).get("digest", "")
        self._digest_cache[model_id] = (now, digest)
        return digest

    async def _pull_model(self, model_id: str) -> None:
        tags_r = await self._client.get("/api/tags")
        tags_r.raise_for_status()
        existing = [m["name"] for m in tags_r.json().get("models", [])]
        if model_id in existing:
            return
        async with self._client.stream("POST", "/api/pull",
                                        json={"name": model_id, "stream": True}) as r:
            async for line in r.aiter_lines():
                if line:
                    pass  # TODO: emit ModelPullProgress events on bus
```

- [ ] **Step 3: Run the OllamaBackend tests**

```bash
pytest tests/unit/test_ollama_backend.py -v
```

Expected: all PASS.

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(lifecycle): implement OllamaBackend (probe/load/unload/is_loaded/list_loaded)"
```

---

## Task 6: Update LifecycleBackendFactory + delete LMS backends

**Files:**
- Modify: `senex/inference_lifecycle.py`
- Modify: any file importing `LMStudioSDKBackend` or `LMSCLIBackend`

- [ ] **Step 1: Write a failing test**

Add to `tests/unit/test_ollama_backend.py`:

```python
@respx.mock
@pytest.mark.asyncio
async def test_factory_selects_ollama_when_reachable():
    from senex.config import InferenceCfg, OllamaCfg
    from senex.inference_lifecycle import LifecycleBackendFactory, OllamaBackend
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(200))
    cfg = InferenceCfg(backend="auto")
    backend = await LifecycleBackendFactory.select(cfg)
    assert isinstance(backend, OllamaBackend)


@pytest.mark.asyncio
async def test_factory_falls_back_to_sglang_when_ollama_unreachable():
    from senex.config import InferenceCfg
    from senex.inference_lifecycle import LifecycleBackendFactory, SGLangBackend
    # No mock — Ollama won't be reachable in test env
    cfg = InferenceCfg(backend="sglang")
    backend = await LifecycleBackendFactory.select(cfg)
    assert isinstance(backend, SGLangBackend)
```

Run: `pytest tests/unit/test_ollama_backend.py -k "factory" -v`
Expected: FAIL.

- [ ] **Step 2: Delete LMStudioSDKBackend and LMSCLIBackend from inference_lifecycle.py**

In `senex/inference_lifecycle.py`:
1. Delete the entire `LMStudioSDKBackend` class.
2. Delete the entire `LMSCLIBackend` class.
3. Remove any imports that are only used by those classes (e.g., `lmstudio` SDK import).

- [ ] **Step 3: Rewrite LifecycleBackendFactory.select()**

Replace the existing `select()` method with:

```python
@staticmethod
async def select(cfg: "InferenceCfg") -> "LifecycleBackend":
    if cfg.backend == "ollama":
        return OllamaBackend(cfg.ollama)
    if cfg.backend == "auto" and await OllamaBackend.probe(cfg.ollama.base_url):
        return OllamaBackend(cfg.ollama)
    return SGLangBackend(cfg)
```

Update the method signature from its old form (which took `base_url, api_key, sglang_cfg` as separate args) to accept a single `InferenceCfg`.

- [ ] **Step 4: Update all callers of LifecycleBackendFactory.select()**

```bash
grep -rn "LifecycleBackendFactory.select\|BackendFactory.select" senex/ --include="*.py"
```

Each call site must be updated to pass the full `InferenceCfg` object instead of individual args.

- [ ] **Step 5: Run factory tests**

```bash
pytest tests/unit/test_ollama_backend.py -k "factory" -v
```

Expected: both PASS.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: no regressions. (Tests that specifically tested LMStudioSDKBackend / LMSCLIBackend behavior should be removed or marked xfail.)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(lifecycle): simplify factory to Ollama+SGLang; delete LMStudioSDKBackend and LMSCLIBackend"
```

---

## Task 7: Backend-aware schema translation + thinking injection in InferenceClient

**Files:**
- Modify: `senex/inference_client.py`
- Create: `tests/unit/test_inference_client_ollama.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_inference_client_ollama.py`:

```python
"""Tests for backend-aware schema translation and thinking injection."""
import pytest
from pydantic import BaseModel
from unittest.mock import MagicMock


class _Schema(BaseModel):
    answer: str


def _make_client(backend_type: str):
    from senex.config import InferenceCfg
    from senex.inference_client import InferenceClient
    cfg = InferenceCfg()
    bus = MagicMock()
    redactor = MagicMock()
    redactor.redact.side_effect = lambda x: x
    client = InferenceClient(cfg, bus, redactor)
    client._backend_type = backend_type
    return client


def test_build_request_ollama_uses_format_not_response_format():
    client = _make_client("ollama")
    messages = [{"role": "user", "content": "hi"}]
    body = client._apply_schema_to_body({"model": "gemma4:e4b", "messages": messages}, _Schema)
    assert "format" in body
    assert "response_format" not in body
    assert body["format"] == _Schema.model_json_schema()


def test_build_request_sglang_uses_response_format():
    client = _make_client("sglang")
    messages = [{"role": "user", "content": "hi"}]
    body = client._apply_schema_to_body({"model": "x", "messages": messages}, _Schema)
    assert "response_format" in body
    assert "format" not in body


def test_inject_thinking_ollama_prepends_think_token():
    client = _make_client("ollama")
    messages = [
        {"role": "system", "content": "You are an auditor."},
        {"role": "user", "content": "Audit this."},
    ]
    result = client._inject_thinking(messages, backend_type="ollama", thinking_enabled=True)
    assert result[0]["content"].startswith("<|think|>")
    assert "You are an auditor." in result[0]["content"]


def test_inject_thinking_sglang_no_message_mutation():
    client = _make_client("sglang")
    messages = [
        {"role": "system", "content": "You are an auditor."},
    ]
    result = client._inject_thinking(messages, backend_type="sglang", thinking_enabled=True)
    assert result[0]["content"] == "You are an auditor."


def test_inject_thinking_disabled_no_mutation():
    client = _make_client("ollama")
    messages = [{"role": "system", "content": "Sys prompt."}]
    result = client._inject_thinking(messages, backend_type="ollama", thinking_enabled=False)
    assert result[0]["content"] == "Sys prompt."
```

Run: `pytest tests/unit/test_inference_client_ollama.py -v`
Expected: FAIL with `AttributeError` (methods not yet defined).

- [ ] **Step 2: Add _backend_type attribute to InferenceClient.__init__()**

In `senex/inference_client.py`, update `__init__` to accept and store backend type:

```python
def __init__(
    self,
    config: "InferenceCfg",
    bus: "EventBus",
    redactor: "SecretRedactor",
    backend_type: str = "sglang",
) -> None:
    # ... existing init code ...
    self._backend_type = backend_type
```

- [ ] **Step 3: Add _apply_schema_to_body() method**

Add to `InferenceClient`:

```python
def _apply_schema_to_body(self, body: dict, schema: type | None) -> dict:
    if schema is None:
        return body
    if self._backend_type == "ollama":
        body["format"] = schema.model_json_schema()
    else:
        if self._config.strict_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "audit_response",
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
    return body
```

- [ ] **Step 4: Add _inject_thinking() method**

Add to `InferenceClient`:

```python
def _inject_thinking(
    self, messages: list[dict], backend_type: str, thinking_enabled: bool
) -> list[dict]:
    if not thinking_enabled:
        return messages
    if backend_type == "ollama":
        msgs = list(messages)
        if msgs and msgs[0]["role"] == "system":
            msgs[0] = {**msgs[0], "content": "<|think|>\n" + msgs[0]["content"]}
        return msgs
    return messages
```

- [ ] **Step 5: Wire _apply_schema_to_body into _post_validate() and _chat_with_schema_fallback()**

In `_post_validate()` (or wherever `response_format` / `format` is currently set), replace the direct assignment with a call to `_apply_schema_to_body()`. Also wire `_inject_thinking()` into the `chat()` method where `extra_body` thinking is currently injected for SGLang.

For SGLang thinking (which currently sets `extra_body = {"chat_template_kwargs": {"enable_thinking": True}}`), keep that path — `_inject_thinking()` handles only the message mutation for Ollama.

- [ ] **Step 6: Run the new tests**

```bash
pytest tests/unit/test_inference_client_ollama.py -v
```

Expected: all PASS.

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: no regressions.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(client): add _apply_schema_to_body and _inject_thinking for backend-aware Ollama support"
```

---

## Task 8: Update InferenceClient construction site to pass backend_type from factory

**Files:**
- Modify: `senex/auditor.py` (or wherever `LMStudioClient`/`InferenceClient` is instantiated)
- Modify: `senex/phases/file_audit.py` (if it constructs the client)

- [ ] **Step 1: Find all InferenceClient construction sites**

```bash
grep -rn "InferenceClient(\|LMStudioClient(" senex/ --include="*.py"
```

- [ ] **Step 2: Update each construction site to pass backend_type**

At each site, the factory result must flow into the client. Pattern:

```python
# Before (example)
client = InferenceClient(cfg, bus, redactor)

# After — factory selects backend; its name propagates to the client
backend = await LifecycleBackendFactory.select(cfg)
client = InferenceClient(cfg, bus, redactor, backend_type=backend.backend_name)
```

If the backend is already selected upstream (e.g., in `Lifecycle.acquire()`), thread `backend.backend_name` through to the client construction.

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: wire backend_type from LifecycleBackendFactory into InferenceClient construction"
```

---

## Task 9: Ollama doctor checks

**Files:**
- Modify: `senex/cli_doctor.py`
- Create: `tests/unit/test_ollama_doctor_checks.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_ollama_doctor_checks.py`:

```python
import pytest
import httpx
import respx
from unittest.mock import MagicMock

from senex.config import InferenceCfg, OllamaCfg


def _doctor_checks():
    from senex.cli_doctor import build_checks
    cfg = InferenceCfg(backend="ollama")
    return build_checks(cfg)


@respx.mock
def test_ollama_reachable_check_passes():
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(200))
    checks = _doctor_checks()
    reachable = next(c for c in checks if c.check_id == "ollama_reachable")
    result = reachable.run()
    assert result.passed is True


@respx.mock
def test_ollama_reachable_check_fails():
    respx.get("http://localhost:11434/").mock(return_value=httpx.Response(503))
    checks = _doctor_checks()
    reachable = next(c for c in checks if c.check_id == "ollama_reachable")
    result = reachable.run()
    assert result.passed is False


@respx.mock
def test_ollama_model_available_check_passes():
    respx.post("http://localhost:11434/api/show").mock(return_value=httpx.Response(
        200, json={"details": {"digest": "sha256:abc"}}
    ))
    checks = _doctor_checks()
    model_check = next(c for c in checks if c.check_id == "ollama_model_available")
    result = model_check.run()
    assert result.passed is True


@respx.mock
def test_ollama_model_available_check_fails():
    respx.post("http://localhost:11434/api/show").mock(return_value=httpx.Response(404))
    checks = _doctor_checks()
    model_check = next(c for c in checks if c.check_id == "ollama_model_available")
    result = model_check.run()
    assert result.passed is False
```

Run: `pytest tests/unit/test_ollama_doctor_checks.py -v`
Expected: FAIL.

- [ ] **Step 2: Add Ollama checks to cli_doctor.py**

In `senex/cli_doctor.py`, in the function that builds the check list (likely `build_checks()` or similar), add:

```python
if cfg.backend in ("ollama", "auto"):
    checks.append(DoctorCheck(
        check_id="ollama_reachable",
        description="Ollama server reachable",
        run=lambda: _check_http_get(cfg.ollama.base_url + "/"),
    ))
    checks.append(DoctorCheck(
        check_id="ollama_model_available",
        description=f"Ollama model '{cfg.ollama.model}' available",
        run=lambda: _check_ollama_model(cfg.ollama.base_url, cfg.ollama.model),
    ))
```

Add the helper function:

```python
def _check_ollama_model(base_url: str, model: str) -> "DoctorResult":
    import httpx
    try:
        r = httpx.post(f"{base_url}/api/show", json={"name": model}, timeout=5)
        return DoctorResult(passed=r.status_code == 200, detail=f"HTTP {r.status_code}")
    except Exception as e:
        return DoctorResult(passed=False, detail=str(e))
```

Also rename the existing `lmstudio_reachable` check to `inference_reachable`.

- [ ] **Step 3: Run the doctor tests**

```bash
pytest tests/unit/test_ollama_doctor_checks.py -v
```

Expected: all PASS.

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(doctor): add ollama_reachable and ollama_model_available checks"
```

---

## Task 10: Update example config + CHANGELOG

**Files:**
- Modify: `senex.config.toml.example`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add [inference.ollama] section to senex.config.toml.example**

Add after the existing `[lmstudio]`/`[inference]` section in `senex.config.toml.example`:

```toml
[inference]
backend = "auto"   # "ollama" | "sglang" | "auto" (auto tries Ollama first)

[inference.ollama]
manage_process    = true
pull_on_start     = true
startup_timeout_s = 60
base_url          = "http://localhost:11434"
model             = "gemma4:e4b"
thinking          = true
strict_json_schema = false
```

And rename the `[lmstudio]` key in the example to `[inference]` with a comment:
```toml
# Renamed from [lmstudio] in v1.1.0. The old key is supported with a deprecation warning.
[inference]
```

- [ ] **Step 2: Add [inference.sglang] section for SGLang users**

```toml
[inference.sglang]
manage_container     = false
startup_timeout_seconds = 180
via_wsl              = true
wsl_distro           = "Ubuntu"
```

- [ ] **Step 3: Add a v1.1.0 entry to CHANGELOG.md**

```markdown
## [1.1.0] — 2026-05-01

### Added
- **Ollama backend** (`[inference.backend] = "ollama"`): full lifecycle management
  (start/stop `ollama serve`, `ollama pull`, VRAM load/unload), structured output via
  Ollama's native `format` parameter, and thinking via `<|think|>` injection.
  Default model: `gemma4:e4b`.
- `[inference.ollama]` config section with `manage_process`, `pull_on_start`,
  `startup_timeout_s`, `base_url`, `model`, `thinking`, and `strict_json_schema` fields.
- `InferenceCfg.backend` field: `"ollama" | "sglang" | "auto"` (default `"auto"`;
  auto-selects Ollama if reachable, otherwise SGLang).
- `senex doctor` checks: `ollama_reachable`, `ollama_model_available`.
- `output.retention_days` config field (default 0 = retain forever).

### Changed
- Config key `[lmstudio]` renamed to `[inference]`. Old key is accepted with a
  `DeprecationWarning` for one release cycle.
- `LmStudioCfg` → `InferenceCfg` (Python alias retained for one release).
- `LMStudioClient` → `InferenceClient` (alias retained).
- `HTTPBackend` → `SGLangBackend` (alias retained).
- `lmstudio_reachable` doctor check renamed to `inference_reachable`.
- Source files renamed: `lmstudio_client.py` → `inference_client.py`,
  `lmstudio_lifecycle.py` → `inference_lifecycle.py`,
  `lmstudio_errors.py` → `inference_errors.py`.

### Removed
- `LMStudioSDKBackend` — use `OllamaBackend` or `SGLangBackend` instead. **Breaking.**
- `LMSCLIBackend` — use `OllamaBackend` or `SGLangBackend` instead. **Breaking.**
  Direct imports of these classes will raise `ImportError`.
```

- [ ] **Step 4: Run full test suite one final time**

```bash
pytest tests/ -q 2>&1 | tail -20
```

Expected: all passing (or same known-skip/xfail count as before).

- [ ] **Step 5: Commit**

```bash
git add senex.config.toml.example CHANGELOG.md
git commit -m "docs: add [inference.ollama] example config + v1.1.0 CHANGELOG entry"
```

---

## Completion Checklist

After all tasks:
- [ ] `grep -rn "lmstudio_client\|lmstudio_lifecycle\|lmstudio_errors" senex/ tests/` → zero hits
- [ ] `grep -rn "LMStudioSDKBackend\|LMSCLIBackend" senex/ tests/` → zero hits (except alias tombstones)
- [ ] `pytest tests/ -q` → same or better pass rate than v1.0.2 baseline
- [ ] `python -c "from senex.config import load_config; load_config('~/.senex/senex.config.toml')"` → no error
- [ ] `senex doctor` passes `ollama_reachable` when Ollama is running
