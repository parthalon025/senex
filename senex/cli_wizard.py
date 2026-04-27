"""senex.cli_wizard — interactive pre-TUI text wizard for ``senex audit`` (M11).

When ``senex audit`` is invoked without a positional path and without
``--no-wizard``, this module walks the user through repo / model / lens /
flag selection over plain stdin/stdout, then returns a ``RuntimeConfig``
ready for ``SenexApp.start_audit`` or the headless subscriber path.

Per conventions §3 (no sync I/O in async paths — this whole module is
synchronous; LM Studio probing is wrapped via ``asyncio.run``), §4 (named
exceptions, no bare except), §10 (pydantic at the boundary — output is
``RuntimeConfig``), §13 (module docstring + Google-style function docs).

Streams default to ``sys.stdin`` / ``sys.stdout`` but tests inject
``io.StringIO`` so the wizard never touches the real terminal under test.

Public API:
    interactive_audit_setup(config, client, *, stdin=None, stdout=None) -> RuntimeConfig

Exception taxonomy:
    WizardCancelled — user typed ``q`` / ``exit`` / sent SIGINT.
    WizardError    — config or LM Studio precondition failed.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from senex.config import SenexConfig
from senex.lens import Lens
from senex.lmstudio_errors import LMSConnectionLost

if TYPE_CHECKING:  # pragma: no cover
    from senex.lmstudio_client import LMStudioClient
    from senex.tui.runtime import RuntimeConfig


# ---------------------------------------------------------------------------
# Named exceptions (conventions §4)
# ---------------------------------------------------------------------------


class WizardCancelled(Exception):
    """User cancelled the wizard (typed ``q``, ``exit``, or sent SIGINT).

    The CLI catches this and exits 130 (SIGINT) per the senex exit-code
    matrix in spec §8.1.
    """


class WizardError(Exception):
    """A precondition for the wizard failed (LM Studio unreachable, etc.).

    The CLI catches this and exits 3 (external dependency error) per
    spec §8.1.
    """


# ---------------------------------------------------------------------------
# Discovery defaults
# ---------------------------------------------------------------------------

# Directory names that should never be traversed when discovering repos on
# disk. These are typical artifact / cache directories that frequently
# *contain* a `.git` of their own (vendored package, virtualenv, etc.) but
# are never what a user means by "audit this repo".
_DISCOVERY_EXCLUDES: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".cache",
        "AppData",
    }
)


# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------


def _read_line(stdin: TextIO) -> str:
    """Read one line from ``stdin``; raise ``WizardCancelled`` on Ctrl+C / EOF.

    Centralizes Ctrl+C handling so each prompt site doesn't need its own
    try/except. EOF (empty bytes when not at first read) is treated as a
    cancel signal as well — running unattended with closed stdin should
    not loop.
    """
    try:
        line = stdin.readline()
    except KeyboardInterrupt as exc:
        raise WizardCancelled("user interrupted") from exc
    if line == "":
        # EOF reached.
        raise WizardCancelled("stdin closed")
    return line.rstrip("\r\n")


def _print(stdout: TextIO, msg: str = "") -> None:
    """Single ``print`` site so tests can capture every line cleanly."""
    print(msg, file=stdout)


# ---------------------------------------------------------------------------
# Helpers — repo selection
# ---------------------------------------------------------------------------


def _validate_repo_path(raw: str) -> Path:
    """Resolve + validate a user-supplied path. Raises ``ValueError`` on miss.

    Per conventions §5 (path safety): ``Path.resolve()`` then check
    existence + directory-ness. The caller turns this into a re-prompt.
    """
    candidate = Path(raw).expanduser()
    resolved = candidate.resolve()
    if not resolved.exists():
        raise ValueError(f"path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"path is not a directory: {resolved}")
    return resolved


def _select_repo(
    config: SenexConfig,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> tuple[str, Path]:
    """Render repo menu, accept user choice, return ``(name, resolved_path)``.

    Args:
        config: The loaded ``SenexConfig``; ``config.repos`` populates the menu.
        stdin: Input stream (default ``sys.stdin``).
        stdout: Output stream (default ``sys.stdout``).

    Returns:
        Tuple of the selected repo's name (config name or basename for
        custom paths) and its resolved absolute path.

    Raises:
        WizardCancelled: User cancelled (``q`` / ``exit`` / Ctrl+C).
    """
    import sys

    sin: TextIO = stdin if stdin is not None else sys.stdin
    sout: TextIO = stdout if stdout is not None else sys.stdout

    # Working list of (display_name, path) candidates. May grow when the
    # user picks "Discover more on disk...".
    candidates: list[tuple[str, Path]] = [
        (r.name, Path(r.path)) for r in config.repos
    ]

    # Bounded outer loop (conventions §3) — allow at most 8 re-prompts so
    # a malformed-stdin test can't spin forever.
    for _ in range(8):
        _print(sout, "")
        _print(sout, "Select a repository:")
        for i, (name, path) in enumerate(candidates, start=1):
            _print(sout, f"  {i}) {name}  ({path})")
        discover_idx = len(candidates) + 1
        custom_idx = len(candidates) + 2
        _print(sout, f"  {discover_idx}) Discover more on disk...")
        _print(sout, f"  {custom_idx}) Enter custom path")
        _print(sout, "  q) Cancel")
        sout.flush()

        raw = _read_line(sin).strip()
        lowered = raw.lower()
        if lowered in ("q", "quit", "exit"):
            raise WizardCancelled("user cancelled at repo select")

        # Numeric: candidate, discover, or custom.
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(candidates):
                name, path = candidates[idx - 1]
                try:
                    resolved = _validate_repo_path(str(path))
                except ValueError as exc:
                    _print(sout, f"  ! {exc}")
                    continue
                return name, resolved
            if idx == discover_idx:
                _print(sout, "Path to scan (blank = home):")
                sout.flush()
                root_raw = _read_line(sin).strip()
                root = (
                    Path(root_raw).expanduser()
                    if root_raw
                    else Path.home()
                )
                if not root.exists():
                    _print(sout, f"  ! root not found: {root}")
                    continue
                discovered = _discover_repos_on_disk(root)
                if not discovered:
                    _print(sout, "  (no additional repos found)")
                # Append only repos we don't already list (compare resolved paths).
                known = {p.resolve() for _, p in candidates}
                for d in discovered:
                    if d.resolve() not in known:
                        candidates.append((d.name, d))
                        known.add(d.resolve())
                continue
            if idx == custom_idx:
                _print(sout, "Enter repo path:")
                sout.flush()
                path_raw = _read_line(sin).strip()
                if not path_raw:
                    _print(sout, "  ! empty path")
                    continue
                try:
                    resolved = _validate_repo_path(path_raw)
                except ValueError as exc:
                    _print(sout, f"  ! {exc}")
                    continue
                return resolved.name, resolved
            _print(sout, f"  ! choice out of range: {idx}")
            continue

        # Non-numeric, non-cancel: treat as a path candidate.
        if raw:
            try:
                resolved = _validate_repo_path(raw)
            except ValueError as exc:
                _print(sout, f"  ! {exc}")
                continue
            return resolved.name, resolved

        _print(sout, "  ! please enter a number, path, or q")

    raise WizardCancelled("repo selection exhausted retries")


# ---------------------------------------------------------------------------
# Helpers — model selection
# ---------------------------------------------------------------------------


def _select_model(
    client: "LMStudioClient | None",
    default_model: str,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> str:
    """Probe ``GET /v1/models``, render the menu, return the chosen model id.

    Args:
        client: An ``LMStudioClient`` instance whose ``list_loaded_models``
            coroutine will be awaited via ``asyncio.run``. ``None`` is
            tolerated: we fall back to ``default_model`` and skip the menu.
        default_model: Configured default model id; pre-selected on Enter.
        stdin: Input stream (default ``sys.stdin``).
        stdout: Output stream (default ``sys.stdout``).

    Returns:
        The selected model id as a plain string.

    Raises:
        WizardError: LM Studio unreachable or returned no loaded models.
        WizardCancelled: User cancelled.
    """
    import sys

    sin: TextIO = stdin if stdin is not None else sys.stdin
    sout: TextIO = stdout if stdout is not None else sys.stdout

    if client is None:
        _print(sout, f"(skipping model probe; using default: {default_model})")
        return default_model

    try:
        models = asyncio.run(client.list_loaded_models())
    except LMSConnectionLost as exc:
        raise WizardError(
            "LM Studio not running or unreachable at the configured base_url. "
            f"Details: {exc}"
        ) from exc

    if not models:
        raise WizardError(
            "LM Studio is reachable but no models are loaded. "
            "Load a model in LM Studio (or set [lmstudio.lifecycle].auto_load) "
            "and re-run."
        )

    # Bounded loop: allow at most 8 re-prompts.
    for _ in range(8):
        _print(sout, "")
        _print(sout, "Select a model:")
        for i, m in enumerate(models, start=1):
            tag = "  (default)" if m.id == default_model else ""
            _print(sout, f"  {i}) {m.id}{tag}")
        _print(sout, "  q) Cancel")
        sout.flush()

        raw = _read_line(sin).strip()
        lowered = raw.lower()
        if lowered in ("q", "quit", "exit"):
            raise WizardCancelled("user cancelled at model select")

        if raw == "":
            return default_model
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(models):
                return models[idx - 1].id
            _print(sout, f"  ! choice out of range: {idx}")
            continue
        # Allow typing the model id directly.
        for m in models:
            if m.id == raw:
                return m.id
        _print(sout, f"  ! unknown model: {raw}")

    raise WizardCancelled("model selection exhausted retries")


# ---------------------------------------------------------------------------
# Helpers — yes/no
# ---------------------------------------------------------------------------


def _yes_no(
    prompt: str,
    *,
    default: bool,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> bool:
    """Prompt for a y/n answer; empty input returns ``default``.

    Args:
        prompt: User-visible prompt text.
        default: Value returned for blank input. Drives the [Y/n] vs [y/N] suffix.
        stdin: Input stream (default ``sys.stdin``).
        stdout: Output stream (default ``sys.stdout``).

    Returns:
        ``True`` for any answer in {y, yes}; ``False`` for {n, no}; ``default``
        for blank input.

    Raises:
        WizardCancelled: User cancelled.
    """
    import sys

    sin: TextIO = stdin if stdin is not None else sys.stdin
    sout: TextIO = stdout if stdout is not None else sys.stdout

    suffix = "[Y/n]" if default else "[y/N]"
    for _ in range(8):
        _print(sout, f"{prompt} {suffix}")
        sout.flush()
        raw = _read_line(sin).strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        if raw in ("q", "quit", "exit"):
            raise WizardCancelled("user cancelled at yes/no prompt")
        _print(sout, "  ! please answer y or n")
    raise WizardCancelled("yes/no prompt exhausted retries")


# ---------------------------------------------------------------------------
# Helpers — disk discovery
# ---------------------------------------------------------------------------


def _is_repo_root(path: Path) -> bool:
    """Return True when ``path/.git`` looks like a working-tree repo.

    Accepts:
      * ``.git`` directory present at ``path/.git`` — git's standard layout
        for a working tree. (Bare repos place HEAD/objects/refs directly
        inside the named directory, so they have no ``.git`` child and are
        correctly rejected by this check.)
      * ``.git`` *file* whose contents start with ``gitdir:`` — the
        worktree pointer file emitted by ``git worktree add``.
    """
    git = path / ".git"
    if git.is_file():
        try:
            head = git.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return head.lstrip().startswith("gitdir:")
    return git.is_dir()


def _discover_repos_on_disk(root: Path, max_depth: int = 6) -> list[Path]:
    """Walk ``root`` looking for git working trees; return their parent paths.

    Args:
        root: Directory to scan. Must exist; resolved to an absolute path.
        max_depth: Maximum recursion depth from ``root`` (root itself = 0).
            Walking deeper than this is skipped — keeps the wizard
            responsive on large home directories.

    Returns:
        A list of repo root paths (the directory that contains ``.git``),
        deduplicated and resolved. Order is depth-first / discovery order.

    Notes:
        * Excludes ``_DISCOVERY_EXCLUDES`` directories (node_modules, .venv,
          dist, build, __pycache__, .cache, AppData) — these typically
          contain vendored ``.git`` directories the user never means.
        * Tolerates ``PermissionError`` and ``OSError`` from ``os.walk`` —
          a single inaccessible subtree must not abort the scan.
    """
    root_abs = root.resolve()
    found: list[Path] = []
    seen: set[Path] = set()

    # Enforce depth at the os.walk level by pruning ``dirs`` once we're at
    # the limit. We compute depth as len(rel_parts).
    try:
        walker = os.walk(str(root_abs), topdown=True, onerror=None)
        for dirpath, dirs, _files in walker:
            try:
                here = Path(dirpath)
                rel = here.relative_to(root_abs)
                depth = 0 if str(rel) in ("", ".") else len(rel.parts)
            except (ValueError, OSError):
                # Malformed path; skip the subtree.
                dirs[:] = []
                continue

            # Prune excluded names BEFORE descending so we never enter them.
            dirs[:] = [d for d in dirs if d not in _DISCOVERY_EXCLUDES]

            # Stop descending when we've hit the depth cap.
            if depth >= max_depth:
                dirs[:] = []

            # Detect a worktree pointer FILE on this level (os.walk lists it
            # in `_files` not `dirs`). The standard `.git` directory case is
            # caught by the `for d in list(dirs)` block below.
            try:
                if (here / ".git").is_file() and _is_repo_root(here):
                    resolved = here.resolve()
                    if resolved not in seen:
                        found.append(here)
                        seen.add(resolved)
            except OSError:
                pass

            # Detect a `.git` directory child.
            if ".git" in dirs:
                try:
                    if _is_repo_root(here):
                        resolved = here.resolve()
                        if resolved not in seen:
                            found.append(here)
                            seen.add(resolved)
                except OSError:
                    pass
                # Once we're inside a repo we don't need to descend into
                # the .git directory itself (and we never want to surface
                # nested submodules as separate top-level audit targets).
                dirs[:] = [d for d in dirs if d != ".git"]
    except (PermissionError, OSError):
        # Top-level walk failed — return whatever we already collected.
        return found

    return found


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def interactive_audit_setup(
    config: SenexConfig,
    client: "LMStudioClient | None",
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> "RuntimeConfig":
    """Walk the user through audit setup; return a populated ``RuntimeConfig``.

    Args:
        config: Loaded senex config (``[[repos]]`` and ``[lmstudio]``
            populate the menus).
        client: LM Studio client used to probe loaded models. ``None`` skips
            the probe and uses ``config.lmstudio.model``.
        stdin: Input stream (default ``sys.stdin``).
        stdout: Output stream (default ``sys.stdout``).

    Returns:
        A ``RuntimeConfig`` ready for ``SenexApp.start_audit`` or the
        headless subscriber path.

    Raises:
        WizardCancelled: User cancelled at any prompt; the CLI exits 130.
        WizardError: LM Studio unreachable / no models loaded; the CLI
            exits 3.
    """
    import senex
    from senex.tui.runtime import RuntimeConfig

    import sys

    sout: TextIO = stdout if stdout is not None else sys.stdout

    try:
        # 1. Welcome banner.
        _print(sout, "")
        _print(sout, f"senex audit wizard (v{senex.__version__})")
        _print(sout, "─" * 40)

        # 2. Repo selection.
        repo_name, repo_path = _select_repo(config, stdin=stdin, stdout=sout)
        _print(sout, f"  -> repo: {repo_name} ({repo_path})")

        # 3. Model selection.
        chosen_model = _select_model(
            client,
            default_model=config.lmstudio.model,
            stdin=stdin,
            stdout=sout,
        )
        _print(sout, f"  -> model: {chosen_model}")

        # 4. Lens — fixed at "correctness" for v1; no prompt.
        lens_name = "correctness"
        try:
            lens = Lens.load(lens_name)
        except FileNotFoundError as exc:
            raise WizardError(f"lens {lens_name!r} not found: {exc}") from exc

        # 5. Include tests?
        include_tests = _yes_no(
            "Include tests?",
            default=False,
            stdin=stdin,
            stdout=sout,
        )

        # 6. Save thinking traces?
        save_thinking = _yes_no(
            "Save thinking traces?",
            default=True,
            stdin=stdin,
            stdout=sout,
        )

        # 7. Confirm.
        proceed = _yes_no(
            f"Start audit on {repo_name} with {chosen_model}?",
            default=True,
            stdin=stdin,
            stdout=sout,
        )
        if not proceed:
            raise WizardCancelled("user declined at confirm")

    except KeyboardInterrupt as exc:
        raise WizardCancelled("user interrupted") from exc

    # Apply overrides on top of the loaded config (a fresh model_dump +
    # validate keeps pydantic strict-mode honest).
    overrides: dict[str, Any] = {
        "lmstudio": {
            "model": chosen_model,
            "thinking": {"save_traces": save_thinking},
        },
        "lens": {
            "name": lens_name,
            "include_tests": include_tests,
        },
        "walker": {"include_tests": include_tests},
    }
    merged = _deep_merge_dict(config.model_dump(), overrides)
    resolved = SenexConfig.model_validate(merged)

    return RuntimeConfig(
        repo=repo_path,
        config=resolved,
        lens=lens,
        config_path=Path("senex.config.toml"),
        output_root=Path(resolved.output.root),
        resume=False,
        allow_mixed_resume=False,
        cli_overrides={},
    )


def _deep_merge_dict(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge; ``over`` wins. Mirrors ``senex.config._deep_merge``.

    Kept private here to avoid exposing the config module's internal helper.
    """
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


__all__ = [
    "WizardCancelled",
    "WizardError",
    "interactive_audit_setup",
]
