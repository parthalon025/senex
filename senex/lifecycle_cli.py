"""senex.lifecycle_cli - lifecycle subcommand handlers + argparse subparser.

Implements spec section 10 CLI shapes:
- ``senex lifecycle status [--json]`` -- list loaded models + runlock holders.
- ``senex lifecycle clear-locks [--force]`` -- prune stale-PID entries.

M4 Task 4.4 declares these handlers; M10 wires the top-level argparse layer
that dispatches to them.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from senex.inference_lifecycle import (
    LifecycleBackendFactory,
    LifecycleBackendUnavailable,
)
from senex.runlock import RunLock, _is_pid_alive


def _load_lifecycle_kwargs(config_path: str | None) -> dict[str, Any]:
    """Read ``senex.config.toml`` and produce kwargs for ``LifecycleBackendFactory.select``.

    Returns a kwargs dict that wires the HTTP backend (SGLang / vLLM /
    OpenAI-compat servers) when ``base_url`` is set in the config; falls
    back to the auto-discovery path (SDK / CLI) on any error so missing
    or malformed config never breaks ``senex lifecycle status``.
    """
    try:
        from senex.config import load_config

        cfg_path = Path(config_path) if config_path else Path("senex.config.toml")
        if not cfg_path.exists():
            return {}
        cfg = load_config(cfg_path)
        base_url = getattr(cfg.inference, "base_url", "") or ""
        api_key = getattr(cfg.inference, "api_key", "lm-studio") or "lm-studio"
        sglang_cfg = getattr(cfg.inference, "sglang", None)
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if sglang_cfg is not None:
            kwargs["sglang_cfg"] = sglang_cfg
        return kwargs
    except Exception:  # noqa: BLE001 — never let config trouble break status.
        return {}


def _default_runlock_root() -> Path:
    return Path.home() / ".senex" / "locks"


def _enumerate_lockfiles(root: Path) -> list[Path]:
    """Return all ``*.lock`` files under ``root`` (non-recursive)."""
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*.lock") if p.is_file())


def _holder_with_pid_alive(holder: dict[str, Any]) -> dict[str, Any]:
    """Augment a runlock holder dict with a ``pid_alive`` boolean."""
    out = dict(holder)
    out["pid_alive"] = _is_pid_alive(int(holder.get("pid", 0)))
    return out


async def cli_lifecycle_status(
    *, as_json: bool, config_path: str | None = None
) -> int:
    """Print the lifecycle status report. Returns the process exit code.

    Output schema (JSON, byte-pinned per M4 plan Task 4.4.1):

        {
          "version": 1,
          "loaded_models": [{"model_id", "quant", "fingerprint", "backend"}],
          "runlock_holders": [{"fingerprint", "run_id", "pid", "started_at",
                                "loaded_by_us", "pid_alive"}]
        }

    When ``config_path`` is provided (or ``./senex.config.toml`` exists),
    the configured ``[lmstudio].base_url`` is used so the HTTP backend
    (SGLang / vLLM / OpenAI-compat servers) is preferred. Otherwise the
    factory falls back to the lmstudio SDK or the ``lms`` CLI.
    """
    loaded_models: list[dict[str, str]] = []
    try:
        select_kwargs = _load_lifecycle_kwargs(config_path)
        backend = await LifecycleBackendFactory.select(**select_kwargs)
        infos = await backend.list_loaded()
        for info in infos:
            loaded_models.append({
                "model_id": info.model_id,
                "quant": info.quant,
                "fingerprint": info.fingerprint,
                "backend": info.backend,
            })
    except LifecycleBackendUnavailable:
        # Status report is still useful for inspecting runlocks even if the
        # backend is unreachable; we just emit empty loaded_models.
        pass

    root = _default_runlock_root()
    runlock_holders: list[dict[str, Any]] = []
    for lock in _enumerate_lockfiles(root):
        # Recover fingerprint from the lockfile JSON itself, not from the path
        # (the path-based name is sanitized).
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fp = data.get("model_fingerprint", "")
        for h in data.get("holders", []):
            row = {
                "fingerprint": fp,
                "run_id": h.get("run_id", ""),
                "pid": int(h.get("pid", 0)),
                "started_at": h.get("started_at", ""),
                "loaded_by_us": bool(h.get("loaded_by_us", False)),
            }
            row["pid_alive"] = _is_pid_alive(int(row["pid"]))
            runlock_holders.append(row)

    if as_json:
        payload = {
            "version": 1,
            "loaded_models": loaded_models,
            "runlock_holders": runlock_holders,
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    # Human-readable table.
    lines: list[str] = []
    lines.append("LOADED MODELS")
    lines.append(f"{'MODEL_ID':40s} {'QUANT':10s} {'BACKEND':6s} FINGERPRINT")
    for m in loaded_models:
        lines.append(
            f"{m['model_id']:40s} {m['quant']:10s} "
            f"{m['backend']:6s} {m['fingerprint']}"
        )
    if not loaded_models:
        lines.append("  (none)")
    lines.append("")
    lines.append("RUNLOCK HOLDERS")
    lines.append(
        f"{'RUN_ID':24s} {'PID':>7s} {'ALIVE':>5s} {'OWNED':>5s} "
        f"{'STARTED_AT':24s} FINGERPRINT"
    )
    for h in runlock_holders:
        lines.append(
            f"{h['run_id']:24s} {h['pid']:>7d} "
            f"{'yes' if h['pid_alive'] else 'no':>5s} "
            f"{'yes' if h['loaded_by_us'] else 'no':>5s} "
            f"{h['started_at']:24s} {h['fingerprint']}"
        )
    if not runlock_holders:
        lines.append("  (none)")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


async def cli_lifecycle_clear_locks(*, force: bool) -> int:
    """Prune dead-PID holders from every lockfile under the runlock root.

    Default (force=False): only stale (dead-PID) entries removed; live entries
    preserved. If any lockfile has live holders AND no dead holders to prune,
    the command exits non-zero with a guidance message on stderr.

    With force=True: live holders are also removed; ``warnings.warn`` (UserWarning)
    is emitted for each live PID removed (re-raised by RunLock.clear).
    """
    root = _default_runlock_root()
    locks = _enumerate_lockfiles(root)
    if not locks:
        sys.stderr.write("no lockfiles found\n")
        return 0

    total_removed = 0
    refused = 0
    for lock in locks:
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fp = data.get("model_fingerprint", "")
        if not fp:
            continue
        holders_before = list(data.get("holders", []))
        live_holders = [
            h for h in holders_before
            if _is_pid_alive(int(h.get("pid", 0)))
        ]
        dead_holders = [
            h for h in holders_before
            if not _is_pid_alive(int(h.get("pid", 0)))
        ]
        if not force and live_holders and not dead_holders:
            refused += 1
            continue
        removed = RunLock.clear(fp, force=force, root=root)
        total_removed += removed

    if refused:
        sys.stderr.write(
            f"refusing to remove live holders; use --force "
            f"({refused} lockfile(s) with live holders left untouched)\n"
        )
        return 2

    sys.stderr.write(
        f"pruned {total_removed} stale holder entr"
        f"{'y' if total_removed == 1 else 'ies'}\n"
    )
    return 0


def register_lifecycle_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``senex lifecycle ...`` subparser tree.

    M10 wires the top-level argparse and dispatches via:

        if args.cmd == "lifecycle":
            if args.lifecycle_cmd == "status":
                exit(asyncio.run(cli_lifecycle_status(as_json=args.as_json)))
            elif args.lifecycle_cmd == "clear-locks":
                exit(asyncio.run(cli_lifecycle_clear_locks(force=args.force)))
    """
    lp = subparsers.add_parser(
        "lifecycle",
        help="Inspect/manage inference-server model lifecycle state",
    )
    lp_sub = lp.add_subparsers(dest="lifecycle_cmd", required=True)
    status = lp_sub.add_parser("status")
    status.add_argument("--json", action="store_true", dest="as_json")
    clear = lp_sub.add_parser("clear-locks")
    clear.add_argument("--force", action="store_true")
