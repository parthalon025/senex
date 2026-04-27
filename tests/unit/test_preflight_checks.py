"""Per-check unit tests for ``senex.phases.preflight`` (M8 Task 8.2).

Each spec §8.1 row has its own small ``check_*`` function. We test:

* PASS path returns ``CheckStatus.PASS``.
* FAIL path returns ``CheckStatus.FAIL`` with the exact documented exit code.
* WARN path returns ``CheckStatus.WARN`` (no exit_code).
* ``check_addendum_safety`` SEC-1 rejection branches: symlink, traversal,
  absolute path outside repo, oversized.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from senex.config import LmStudioCfg, SenexConfig
from senex.phases.preflight import (
    CheckResult,
    CheckStatus,
    check_addendum_safety,
    check_config_parses,
    check_gitnexus_index,
    check_language_anchors,
    check_lifecycle_backend,
    check_lms_reachable,
    check_model_loaded_or_loadable,
    check_output_dir_writable,
    check_repo_path,
    check_runlock_dir_writable,
    check_sampling_ranges,
    check_schema_with_thinking,
    check_streaming,
)


# ---------------------------------------------------------------------------
# Sync checks — pure / filesystem only.
# ---------------------------------------------------------------------------


def test_check_config_parses_pass(tmp_path: Path) -> None:
    cfg = tmp_path / "senex.config.toml"
    cfg.write_text(
        '[lmstudio]\nmodel = "google/gemma-4-26b-a4b"\n', encoding="utf-8"
    )
    result = check_config_parses(cfg)
    assert result.status is CheckStatus.PASS


def test_check_config_parses_fail_unknown_key(tmp_path: Path) -> None:
    cfg = tmp_path / "senex.config.toml"
    cfg.write_text("[bogus_section]\nfoo = 1\n", encoding="utf-8")
    result = check_config_parses(cfg)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


def test_check_config_parses_fail_missing_file(tmp_path: Path) -> None:
    result = check_config_parses(tmp_path / "missing.toml")
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


def test_check_repo_path_pass(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    result = check_repo_path(tmp_path)
    assert result.status is CheckStatus.PASS


def test_check_repo_path_fail_not_dir(tmp_path: Path) -> None:
    f = tmp_path / "not_a_dir"
    f.write_text("x")
    result = check_repo_path(f)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


def test_check_repo_path_fail_no_git(tmp_path: Path) -> None:
    result = check_repo_path(tmp_path)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


def test_check_repo_path_fail_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §8.1: repo path MUST be absolute."""
    monkeypatch.chdir(tmp_path)
    result = check_repo_path(Path("relative/path"))
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


def test_check_output_dir_writable_pass(tmp_path: Path) -> None:
    out = tmp_path / "audits"
    result = check_output_dir_writable(out, min_bytes=1)
    assert result.status is CheckStatus.PASS


def test_check_output_dir_writable_fail_unwritable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate write rejection by raising on mkdir."""
    out = tmp_path / "audits"

    def boom(*args: object, **kwargs: object) -> None:
        raise PermissionError("no write")

    monkeypatch.setattr(Path, "mkdir", boom)
    result = check_output_dir_writable(out, min_bytes=1)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


def test_check_gitnexus_index_warn_when_missing(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    result = check_gitnexus_index(tmp_path)
    # No .gitnexus dir -> warn
    assert result.status is CheckStatus.WARN


def test_check_gitnexus_index_pass_when_present(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    gn = tmp_path / ".gitnexus"
    gn.mkdir()
    (gn / "meta.json").write_text("{}", encoding="utf-8")
    result = check_gitnexus_index(tmp_path)
    assert result.status is CheckStatus.PASS


# ---- check_addendum_safety (SEC-1) -----------------------------------------


def _make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    return root


def test_check_addendum_safety_pass_inside_repo(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    addendum = repo / "addendum.md"
    addendum.write_text("rules\n", encoding="utf-8")
    result = check_addendum_safety(addendum, repo)
    assert result.status is CheckStatus.PASS


def test_check_addendum_safety_pass_when_none(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    result = check_addendum_safety(None, repo)
    assert result.status is CheckStatus.PASS


def test_check_addendum_safety_fail_traversal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    # path with traversal that lands outside repo
    addendum = (repo / ".." / "outside.md")
    result = check_addendum_safety(addendum, repo)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


def test_check_addendum_safety_fail_absolute_outside_repo(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    result = check_addendum_safety(outside, repo)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


@pytest.mark.skipif(os.name == "nt", reason="symlink creation typically requires admin on Windows")
def test_check_addendum_safety_fail_symlink(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    target = tmp_path / "secret.md"
    target.write_text("secret\n", encoding="utf-8")
    addendum = repo / "addendum.md"
    addendum.symlink_to(target)
    result = check_addendum_safety(addendum, repo)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


def test_check_addendum_safety_fail_oversized(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    big = repo / "big.md"
    big.write_bytes(b"x" * (64 * 1024 + 1))
    result = check_addendum_safety(big, repo)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


# ---- check_language_anchors -----------------------------------------------


def test_check_language_anchors_pass(tmp_path: Path) -> None:
    anchors = tmp_path / "prompts"
    anchors.mkdir()
    (anchors / "lang_python.md").write_text("py\n", encoding="utf-8")
    result = check_language_anchors([".py"], anchors)
    assert result.status is CheckStatus.PASS


def test_check_language_anchors_warn_missing(tmp_path: Path) -> None:
    anchors = tmp_path / "prompts"
    anchors.mkdir()
    result = check_language_anchors([".py", ".rs"], anchors)
    # Spec §8.1: missing language anchor is a WARN, not a fail.
    assert result.status is CheckStatus.WARN


# ---- check_sampling_ranges -------------------------------------------------


def test_check_sampling_ranges_pass() -> None:
    cfg = SenexConfig()
    result = check_sampling_ranges(cfg)
    assert result.status is CheckStatus.PASS


def test_check_sampling_ranges_fail_temperature() -> None:
    # Construct a SenexConfig with an out-of-range temperature; pydantic
    # validates at construction time, so we patch the assembled value via
    # model_construct (skipping validation) to simulate a corrupt assembly.
    cfg = SenexConfig.model_construct()
    cfg.lmstudio = LmStudioCfg.model_construct()
    cfg.lmstudio.sampling = cfg.lmstudio.sampling.model_copy(update={"temperature": 5.0})
    result = check_sampling_ranges(cfg)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 2


# ---- check_lifecycle_backend ----------------------------------------------


def test_check_lifecycle_backend_pass_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SenexConfig()
    # Simulate `lms` CLI present
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/lms")
    result = check_lifecycle_backend(cfg)
    assert result.status is CheckStatus.PASS


def test_check_lifecycle_backend_warn_when_auto_load_off(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SenexConfig()
    cfg.lmstudio.lifecycle = cfg.lmstudio.lifecycle.model_copy(
        update={"auto_load": False, "auto_unload": False}
    )
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    # SDK import will also fail; force ImportError via sys.modules
    import sys

    monkeypatch.setitem(sys.modules, "lmstudio", None)
    result = check_lifecycle_backend(cfg)
    assert result.status is CheckStatus.WARN


def test_check_lifecycle_backend_fail_when_auto_load_on(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SenexConfig()
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    import sys

    monkeypatch.setitem(sys.modules, "lmstudio", None)
    result = check_lifecycle_backend(cfg)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 3


# ---- check_runlock_dir_writable -------------------------------------------


def test_check_runlock_dir_writable_pass(tmp_path: Path) -> None:
    cfg = SenexConfig()
    cfg.lmstudio.lifecycle = cfg.lmstudio.lifecycle.model_copy(
        update={"runlock_dir": str(tmp_path / "locks")}
    )
    result = check_runlock_dir_writable(cfg)
    assert result.status is CheckStatus.PASS


def test_check_runlock_dir_writable_pass_when_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = SenexConfig()
    cfg.lmstudio.lifecycle = cfg.lmstudio.lifecycle.model_copy(update={"runlock_dir": ""})
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = check_runlock_dir_writable(cfg)
    assert result.status is CheckStatus.PASS


# ---------------------------------------------------------------------------
# Async checks — exercised with a tiny in-memory fake.
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal stand-in for ``LMStudioClient`` for preflight tests."""

    def __init__(
        self,
        *,
        models: list[object] | None = None,
        raise_list: bool = False,
        non_loopback: bool = False,
        capabilities: object | None = None,
    ) -> None:
        from senex.lmstudio_client import LoadedModelInfo, ProbedCapabilities

        self._models = models if models is not None else [
            LoadedModelInfo(id="google/gemma-4-26b-a4b")
        ]
        self._raise_list = raise_list
        self._caps = capabilities or ProbedCapabilities(
            supports_tools=True,
            supports_schema_with_tools=True,
            supports_streaming=True,
            supports_reasoning_effort=True,
        )
        self._config = LmStudioCfg(
            base_url=(
                "http://192.168.1.1:1234/v1" if non_loopback else "http://localhost:1234/v1"
            ),
            allow_non_loopback=False,
        )

    async def list_loaded_models(self) -> list[object]:
        if self._raise_list:
            from senex.lmstudio_errors import LMSConnectionLost

            raise LMSConnectionLost("simulated")
        return self._models

    async def probe_capabilities(self, model_id: str) -> object:
        return self._caps


@pytest.mark.asyncio
async def test_check_lms_reachable_pass() -> None:
    client = _FakeClient()
    result = await check_lms_reachable(client)
    assert result.status is CheckStatus.PASS


@pytest.mark.asyncio
async def test_check_lms_reachable_fail_unreachable() -> None:
    client = _FakeClient(raise_list=True)
    result = await check_lms_reachable(client)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 3


@pytest.mark.asyncio
async def test_check_lms_reachable_fail_non_loopback() -> None:
    client = _FakeClient(non_loopback=True)
    result = await check_lms_reachable(client)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 3


@pytest.mark.asyncio
async def test_check_model_loaded_pass() -> None:
    cfg = SenexConfig()
    client = _FakeClient()
    result = await check_model_loaded_or_loadable(client, cfg)
    assert result.status is CheckStatus.PASS


@pytest.mark.asyncio
async def test_check_model_loaded_fail_when_not_loaded_and_no_auto_load() -> None:
    cfg = SenexConfig()
    cfg.lmstudio.lifecycle = cfg.lmstudio.lifecycle.model_copy(update={"auto_load": False})
    client = _FakeClient(models=[])
    result = await check_model_loaded_or_loadable(client, cfg)
    assert result.status is CheckStatus.FAIL
    assert result.exit_code == 3


@pytest.mark.asyncio
async def test_check_schema_with_thinking_pass() -> None:
    cfg = SenexConfig()
    client = _FakeClient()
    result = await check_schema_with_thinking(client, cfg)
    assert result.status is CheckStatus.PASS


@pytest.mark.asyncio
async def test_check_schema_with_thinking_warn_when_unsupported() -> None:
    from senex.lmstudio_client import ProbedCapabilities

    caps = ProbedCapabilities(
        supports_tools=True,
        supports_schema_with_tools=False,
        supports_streaming=True,
        supports_reasoning_effort=True,
    )
    cfg = SenexConfig()
    client = _FakeClient(capabilities=caps)
    result = await check_schema_with_thinking(client, cfg)
    assert result.status is CheckStatus.WARN


@pytest.mark.asyncio
async def test_check_streaming_pass() -> None:
    cfg = SenexConfig()
    client = _FakeClient()
    result = await check_streaming(client, cfg)
    assert result.status is CheckStatus.PASS


@pytest.mark.asyncio
async def test_check_streaming_warn_when_unsupported() -> None:
    from senex.lmstudio_client import ProbedCapabilities

    caps = ProbedCapabilities(
        supports_tools=True,
        supports_schema_with_tools=True,
        supports_streaming=False,
        supports_reasoning_effort=True,
    )
    cfg = SenexConfig()
    client = _FakeClient(capabilities=caps)
    result = await check_streaming(client, cfg)
    assert result.status is CheckStatus.WARN


# ---------------------------------------------------------------------------
# CheckResult dataclass smoke
# ---------------------------------------------------------------------------


def test_check_result_pass_message_default() -> None:
    r = CheckResult(status=CheckStatus.PASS)
    assert r.exit_code is None


def test_check_result_fail_requires_exit_code() -> None:
    r = CheckResult(status=CheckStatus.FAIL, message="boom", exit_code=2)
    assert r.exit_code == 2
