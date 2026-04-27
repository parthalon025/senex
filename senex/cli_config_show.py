"""senex.cli_config_show — ``cmd_config_show`` (M10 Task 10.5).

Resolves the merged config (defaults -> file -> per-repo entry) for a given
repo path, then serializes it as TOML (default) or JSON (``--json``). With
``--all``, dumps a list of every ``[[repos]]`` entry's resolved config.

Per spec §11 (security): values are run through SecretRedactor before
serialization so that secret-shaped strings (api_key, *_token, *_secret)
are never echoed to stdout.

Per spec §10: round-trip discipline — re-loading the TOML output through
``load_config`` MUST produce an equal SenexConfig.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from senex.config import (
    SenexConfig,
    UnknownConfigKey,
    load_config,
    resolve_config,
)
from senex.secret_redactor import SecretRedactor


log = logging.getLogger(__name__)


_EXIT_OK = 0
_EXIT_CONFIG = 2


def cmd_config_show(args: argparse.Namespace) -> int:
    """``senex config show`` dispatcher. Returns process exit code."""
    cfg_path = Path(args.config) if args.config else Path("senex.config.toml")
    try:
        config = load_config(cfg_path)
    except FileNotFoundError:
        sys.stderr.write(f"senex config show: config not found: {cfg_path}\n")
        return _EXIT_CONFIG
    except (UnknownConfigKey, ValueError) as exc:
        sys.stderr.write(f"senex config show: config error: {exc}\n")
        return _EXIT_CONFIG

    if not args.all and not args.repo_path:
        sys.stderr.write(
            "senex config show: must supply <repo-path> or --all\n"
        )
        return _EXIT_CONFIG

    redactor = SecretRedactor()
    as_json = bool(getattr(args, "as_json", False))

    if args.all:
        if not config.repos:
            sys.stderr.write("senex config show --all: no [[repos]] in config\n")
            return _EXIT_CONFIG
        entries = [(Path(r.path), r) for r in config.repos]
        outputs: list[dict[str, Any]] = []
        for repo_path, _entry in entries:
            resolved = resolve_config(
                base=config,
                repo_path=repo_path,
                cli_overrides={},
                tui_overrides={},
            )
            outputs.append(
                {"repo": str(repo_path), "config": _redact_dump(resolved, redactor)}
            )
        if as_json:
            sys.stdout.write(json.dumps(outputs, indent=2) + "\n")
        else:
            for out in outputs:
                sys.stdout.write(f"# repo: {out['repo']}\n")
                sys.stdout.write(_to_toml(out["config"]))
                sys.stdout.write("\n")
        sys.stdout.flush()
        return _EXIT_OK

    repo = Path(args.repo_path)
    resolved = resolve_config(
        base=config,
        repo_path=repo,
        cli_overrides={},
        tui_overrides={},
    )
    redacted = _redact_dump(resolved, redactor)
    if as_json:
        sys.stdout.write(json.dumps(redacted, indent=2) + "\n")
    else:
        sys.stdout.write(_to_toml(redacted))
    sys.stdout.flush()
    return _EXIT_OK


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _redact_dump(config: SenexConfig, redactor: SecretRedactor) -> dict[str, Any]:
    """Return ``config.model_dump()`` with secret-key values replaced.

    Two passes:
      1. ``redact_dict`` masks values whose KEY name matches a sensitive glob
         (api_key, *_token, *_secret, password*, *_key).
      2. Walk the result and run every string through ``redactor.redact`` to
         catch secret-shaped VALUES (e.g. an `sk-...` literal slipped into a
         non-sensitive-named field). The combined effect: no secret survives.
    """
    raw = config.model_dump()
    keyed = redactor.redact_dict(raw)
    return _redact_strings_recursive(keyed, redactor)


def _redact_strings_recursive(node: Any, redactor: SecretRedactor) -> Any:
    if isinstance(node, dict):
        return {k: _redact_strings_recursive(v, redactor) for k, v in node.items()}
    if isinstance(node, list):
        return [_redact_strings_recursive(v, redactor) for v in node]
    if isinstance(node, str):
        return redactor.redact(node)
    return node


def _to_toml(data: dict[str, Any]) -> str:
    """Serialize a dict to TOML, stripping ``None`` values (tomli_w can't encode them).

    Matches the auditor's ``_snapshot_config`` shape so re-loading the output
    via ``load_config`` produces an equivalent SenexConfig.
    """
    import tomli_w

    cleaned = _strip_none(data)
    return tomli_w.dumps(cleaned)


def _strip_none(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_none(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_strip_none(item) for item in node]
    return node


__all__ = ["cmd_config_show"]
