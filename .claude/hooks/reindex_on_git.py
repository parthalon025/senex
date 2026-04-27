#!/usr/bin/env python3
"""PostToolUse hook: re-index GitNexus after git mutating operations in senex repo.

Reads Claude Code's PostToolUse JSON payload from stdin. If the tool is Bash and
the command contains a mutating git operation, fires `npx gitnexus analyze
--embeddings` in the repo root, async (subprocess.Popen returns immediately).

The --embeddings flag preserves any previously generated embeddings; without it,
gitnexus would silently delete them on each re-index.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

GIT_MUTATIONS = re.compile(
    r"\bgit\s+(?:commit|merge|rebase|cherry-pick|reset|pull|am|revert)\b"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    command = tool_input.get("command", "") or ""

    if not GIT_MUTATIONS.search(command):
        return 0

    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / ".gitnexus").exists() and not (repo_root / ".git").exists():
        return 0

    try:
        subprocess.Popen(
            ["npx", "gitnexus", "analyze", "--embeddings"],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except FileNotFoundError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
