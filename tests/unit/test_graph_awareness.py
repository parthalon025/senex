"""Tests for senex.graph_awareness — pre-flight per-file graph context.

Implements M2 Task 2.2 verification (spec §5.8 + §SEC-4 subprocess
hardening). Per Conventions §6, mocks live ONLY at the
`asyncio.create_subprocess_exec` boundary.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from senex.events import EventBus, GraphContextUnavailable
from senex.graph_awareness import (
    GitNexusCLIProvider,
    GitNexusUnavailable,
    GraphContext,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gitnexus_outputs"


def _proc_returning(stdout: bytes, stderr: bytes = b"", returncode: int = 0):
    """Build an AsyncMock that simulates `asyncio.create_subprocess_exec`."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


@pytest.mark.asyncio
async def test_gitnexus_subprocess_uses_listform_args_no_shell() -> None:
    """§SEC-4: list-form, shell=False, npx absolute path pinned."""
    payload = (FIXTURES / "query_walker_what_does.json").read_bytes()
    proc = _proc_returning(payload)
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ) as exec_mock:
        provider = GitNexusCLIProvider(
            repo_name="senex", npx_path="C:/abs/npx.cmd", bus=EventBus()
        )
        await provider.fetch("senex/walker.py")
        assert exec_mock.called
        args, kwargs = exec_mock.call_args
        # On Windows, .cmd wrappers go through cmd.exe /c; on other platforms
        # the npx path is the first arg directly.
        import sys
        if sys.platform == "win32":
            assert args[0] == "cmd.exe"
            assert args[1] == "/c"
            assert args[2] == "C:/abs/npx.cmd"
        else:
            assert args[0] == "C:/abs/npx.cmd"
        # No shell-mode invocation under any circumstance.
        assert "shell" not in kwargs or kwargs.get("shell") is False
        # The CLI used: gitnexus query --repo <repo> "<goal>".
        assert "gitnexus" in args
        assert "query" in args
        assert "--repo" in args and "senex" in args


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_relpath",
    [
        "../etc/passwd",
        "/abs/leak.py",
        "C:/Windows/System32",
        "file with spaces.py",
        "weird;rm -rf;.py",
        "unicode-€-eur.py",  # plan parameter name implied non-ASCII; value fixed
        "file\nwith\nnewlines.py",
    ],
)
async def test_relpath_regex_rejects_bad_input(bad_relpath: str) -> None:
    """§SEC-4: relpath regex runs BEFORE subprocess construction."""
    provider = GitNexusCLIProvider(
        repo_name="senex", npx_path="/abs/npx", bus=EventBus()
    )
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec"
    ) as exec_mock:
        ctx = await provider.fetch(bad_relpath)
        assert exec_mock.called is False
        assert ctx.available is False
        assert ctx.raw_text_block == "[graph context unavailable]"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "good_relpath",
    [
        "senex/walker.py",
        "senex\\walker.py",  # Windows separator
        "src/sub-mod/file.go",
        "path/to/File_v2.tsx",
        "a.py",
        "a/b.c.d.py",
    ],
)
async def test_relpath_regex_accepts_valid_input(good_relpath: str) -> None:
    payload = (FIXTURES / "query_walker_what_does.json").read_bytes()
    proc = _proc_returning(payload)
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ) as exec_mock:
        provider = GitNexusCLIProvider(
            repo_name="senex", npx_path="/abs/npx", bus=EventBus()
        )
        await provider.fetch(good_relpath)
        assert exec_mock.called


@pytest.mark.asyncio
async def test_subprocess_failure_returns_sentinel_and_emits_event() -> None:
    proc = _proc_returning(b"", b"index missing\n", returncode=1)
    bus = EventBus()
    seen: list[GraphContextUnavailable] = []

    async def cb(ev):  # type: ignore[no-untyped-def]
        seen.append(ev)

    bus.subscribe_local("test", GraphContextUnavailable, cb)
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        provider = GitNexusCLIProvider(
            repo_name="senex", npx_path="/abs/npx", bus=bus
        )
        ctx = await provider.fetch("senex/walker.py")
    assert ctx.available is False
    assert ctx.raw_text_block == "[graph context unavailable]"
    assert len(seen) == 1
    assert seen[0].path == "senex/walker.py"
    assert "index missing" in (seen[0].reason or "")


@pytest.mark.asyncio
async def test_subprocess_timeout_returns_sentinel() -> None:
    async def hang(*a, **kw):  # type: ignore[no-untyped-def]
        await asyncio.sleep(60)

    proc = AsyncMock()
    proc.communicate = hang
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.returncode = -1
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        provider = GitNexusCLIProvider(
            repo_name="senex",
            npx_path="/abs/npx",
            bus=EventBus(),
            timeout_seconds=0.05,
        )
        ctx = await provider.fetch("senex/walker.py")
    assert ctx.available is False
    assert proc.kill.called  # timeout path kills the process


@pytest.mark.asyncio
async def test_subprocess_returns_garbage_returns_sentinel() -> None:
    proc = _proc_returning(b"not json {{{")
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        provider = GitNexusCLIProvider(
            repo_name="senex", npx_path="/abs/npx", bus=EventBus()
        )
        ctx = await provider.fetch("senex/walker.py")
    assert ctx.available is False


@pytest.mark.asyncio
async def test_fetch_parses_fixture_into_graph_context() -> None:
    payload = (FIXTURES / "query_walker_what_does.json").read_bytes()
    proc = _proc_returning(payload)
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        provider = GitNexusCLIProvider(
            repo_name="senex", npx_path="/abs/npx", bus=EventBus()
        )
        ctx = await provider.fetch("senex/walker.py")
    assert ctx.available is True
    assert isinstance(ctx.public_symbols, list)
    assert "[graph context unavailable]" not in ctx.raw_text_block
    assert len(ctx.raw_text_block) > 0


@pytest.mark.asyncio
async def test_prefetch_all_populates_cache_and_avoids_resubprocess() -> None:
    payload = (FIXTURES / "query_walker_what_does.json").read_bytes()
    proc = _proc_returning(payload)
    exec_mock = AsyncMock(return_value=proc)
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec",
        new=exec_mock,
    ):
        provider = GitNexusCLIProvider(
            repo_name="senex", npx_path="/abs/npx", bus=EventBus()
        )
        await provider.prefetch_all(
            ["senex/walker.py", "senex/graph_awareness.py"]
        )
        calls_after_prefetch = exec_mock.call_count
        await provider.fetch("senex/walker.py")
        await provider.fetch("senex/graph_awareness.py")
        assert exec_mock.call_count == calls_after_prefetch


@pytest.mark.asyncio
async def test_preflight_resolves_npx_absolute_path() -> None:
    with patch(
        "senex.graph_awareness.shutil.which",
        return_value="C:/Program Files/nodejs/npx.cmd",
    ):
        provider = await GitNexusCLIProvider.preflight(
            repo_name="senex", bus=EventBus()
        )
    assert provider._npx_path == "C:/Program Files/nodejs/npx.cmd"


@pytest.mark.asyncio
async def test_preflight_raises_when_npx_missing() -> None:
    with patch("senex.graph_awareness.shutil.which", return_value=None), patch(
        "senex.graph_awareness.Path.is_file", return_value=False
    ), pytest.raises(GitNexusUnavailable):
        await GitNexusCLIProvider.preflight(
            repo_name="senex", bus=EventBus()
        )


def test_graph_context_dataclass_shape() -> None:
    ctx = GraphContext(
        cluster=None,
        cluster_summary=None,
        public_symbols=[],
        callers_d1_count={},
        top_processes=[],
        available=False,
        raw_text_block="[graph context unavailable]",
    )
    assert ctx.available is False
    assert ctx.raw_text_block == "[graph context unavailable]"


def test_graph_awareness_module_has_nonempty_docstring() -> None:
    from senex import graph_awareness as ga

    assert ga.__doc__ and ga.__doc__.strip() != ""


@pytest.mark.asyncio
async def test_fetch_returns_cache_hit_without_resubprocess() -> None:
    """Second call with same relpath reads from cache (no new spawn)."""
    payload = (FIXTURES / "query_walker_what_does.json").read_bytes()
    proc = _proc_returning(payload)
    exec_mock = AsyncMock(return_value=proc)
    with patch(
        "senex.graph_awareness.asyncio.create_subprocess_exec",
        new=exec_mock,
    ):
        provider = GitNexusCLIProvider(
            repo_name="senex", npx_path="/abs/npx", bus=EventBus()
        )
        await provider.fetch("senex/walker.py")
        first = exec_mock.call_count
        await provider.fetch("senex/walker.py")
        assert exec_mock.call_count == first
