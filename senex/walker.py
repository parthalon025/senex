"""Walker -- repo enumeration with safety guards.

Implements spec §5.9 (Walker) + §SEC-3 (symlink escape) + §ARCH-14
(case-collision handling on case-insensitive filesystems). Returns a
deterministic, gitignore-aware, size-capped list of files for downstream
audit.

Conventions cross-refs:
- §1 Code style: pathlib over os.path; ``from __future__ import annotations``.
- §3 Async/concurrency: bounded iteration (`_MAX_FILES_HARD_CAP`).
- §4 Error handling: named exceptions only.
- §5 Security: §SEC-3 symlink-escape guard; resolve THEN is_relative_to.
- §6 Testing: pure data return; no async I/O; auditor (M8) publishes
  events from ``WalkResult.skipped``.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import pathspec

from .config import WalkerCfg
from .events import EventBus

# Hard cap on discovered files; refuses to silently truncate (Conventions §3).
_MAX_FILES_HARD_CAP: int = 50_000


class WalkerError(Exception):
    """Base for walker-level errors that abort discovery."""


class RepoPathInvalid(WalkerError):
    """repo_path does not exist, is not a directory, or has no .git/."""


class PathOutsideRepo(WalkerError):
    """A candidate path resolved outside repo_root_resolved.

    Used for hard-fail config addendum paths; the walker itself emits
    skipped entries instead of raising.
    """


class WalkerLimitExceeded(WalkerError):
    """Exceeded `_MAX_FILES_HARD_CAP` (default 50000); refuses to truncate."""


@dataclass(frozen=True)
class WalkResult:
    """Result of `Walker.discover()`.

    Attributes:
        kept: absolute paths, sorted by relpath ascending (forward-slash
            relpath as sort key).
        skipped: list of (path, reason); reason in
            {"gitignore", "extension", "exclude", "too_large_bytes",
             "symlink_escape", "symlink_broken", "case_collision",
             "tests_excluded", "binary"}.
        relpath_to_report_path: forward-slash relpath -> forward-slash
            report-relpath. Differs only when a case-collision suffix is
            applied (§ARCH-14).
    """

    kept: list[Path]
    skipped: list[tuple[Path, str]]
    relpath_to_report_path: dict[str, str]


class Walker:
    """Discover files for audit (spec §5.9).

    The walker is intentionally synchronous: it returns pure data and does
    NOT publish to the (async) event bus directly. Callers (auditor M8)
    iterate `WalkResult.skipped` and emit `SymlinkSkipped` / `FileSkipped`
    events at their leisure.
    """

    def __init__(self, bus: EventBus) -> None:
        # Bus retained for future sync-emit hooks (M8 may inject one). Not
        # used in v1; passing it in keeps the constructor stable across the
        # M2/M8 boundary.
        self._bus = bus

    def discover(self, repo_path: Path, config: WalkerCfg) -> WalkResult:
        """Enumerate files under `repo_path` per `config`.

        Args:
            repo_path: absolute path to the audited repo (must contain `.git/`).
            config: walker configuration (extensions, excludes, size cap, etc.).

        Returns:
            A `WalkResult` with kept files (sorted) and skip reasons.

        Raises:
            RepoPathInvalid: repo_path missing / not a directory / no `.git/`.
            WalkerLimitExceeded: > `_MAX_FILES_HARD_CAP` files discovered.
        """
        # 1. Validate repo
        if not repo_path.exists() or not repo_path.is_dir():
            raise RepoPathInvalid(
                f"repo_path {repo_path} does not exist or is not a directory"
            )
        if not (repo_path / ".git").exists():
            raise RepoPathInvalid(f"repo_path {repo_path} has no .git/ subdir")
        repo_root_resolved = repo_path.resolve(strict=True)

        # 2. Load .gitignore (if present) via pathspec (Conventions §14: do
        #    not re-implement what the ecosystem covers).
        gi = repo_path / ".gitignore"
        spec = pathspec.PathSpec.from_lines(
            "gitwildmatch",
            gi.read_text(encoding="utf-8").splitlines() if gi.exists() else [],
        )

        # 3. Build excludes set: default_excludes + tests/ when include_tests=False
        excludes: set[str] = set(config.default_excludes)
        if not config.include_tests:
            excludes.add("tests")

        kept: list[Path] = []
        skipped: list[tuple[Path, str]] = []
        report_paths: dict[str, str] = {}
        seen_lower: dict[str, str] = {}  # lower-relpath -> first relpath
        file_count = 0
        ext_set = {e.lower() for e in config.extensions}

        # 4. Walk; followlinks=False is mandatory (§SEC-3).
        for dirpath, dirnames, filenames in os.walk(
            repo_path, followlinks=False, topdown=True
        ):
            # Prune excluded dirs in-place so os.walk does not descend.
            # Exception: if a dir is also matched by .gitignore, descend
            # so every file inside is recorded with a "gitignore" reason
            # (otherwise the dir vanishes silently from the report).
            kept_dirs: list[str] = []
            for d in sorted(dirnames):
                if d not in excludes:
                    kept_dirs.append(d)
                    continue
                # Check if gitignore matches this dir; if so, descend.
                dir_rel = (
                    (Path(dirpath) / d).relative_to(repo_path).as_posix() + "/"
                )
                if spec.match_file(dir_rel):
                    kept_dirs.append(d)
            dirnames[:] = kept_dirs
            for fname in sorted(filenames):
                file_count += 1
                if file_count > _MAX_FILES_HARD_CAP:
                    raise WalkerLimitExceeded(
                        f"discovered > {_MAX_FILES_HARD_CAP} files; "
                        f"raise WalkerCfg.max_files or narrow excludes"
                    )
                candidate = Path(dirpath) / fname
                rel = candidate.relative_to(repo_path).as_posix()

                # 4a. Symlink escape guard (§SEC-3) -- RESOLVE FIRST, then is_relative_to.
                #     Reverse order is exploitable: a sym whose literal path
                #     is under the repo passes naive is_relative_to but its
                #     resolved target is elsewhere.
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    skipped.append((candidate, "symlink_broken"))
                    continue
                if not _is_relative_to(resolved, repo_root_resolved):
                    skipped.append((candidate, "symlink_escape"))
                    continue

                # 4b. .gitignore (relative path)
                if spec.match_file(rel):
                    skipped.append((candidate, "gitignore"))
                    continue

                # 4c. Extension filter
                if candidate.suffix.lower() not in ext_set:
                    skipped.append((candidate, "extension"))
                    continue

                # 4d. Size cap (BYTES, not lines; §ARCH-14, spec §5.9)
                if resolved.stat().st_size > config.max_size_bytes:
                    skipped.append((candidate, "too_large_bytes"))
                    continue

                # 4e. Case-collision detection (§ARCH-14)
                rel_lower = rel.lower()
                if rel_lower in seen_lower and seen_lower[rel_lower] != rel:
                    short = hashlib.sha256(
                        str(resolved).encode("utf-8")
                    ).hexdigest()[:8]
                    stem = Path(rel).with_suffix("").as_posix()
                    suffix = candidate.suffix
                    report_rel = f"{stem}~{short}{suffix}"
                else:
                    seen_lower.setdefault(rel_lower, rel)
                    report_rel = rel

                kept.append(candidate)
                report_paths[rel] = report_rel

        # 5. Deterministic sort (relpath ascending; stable for equal relpaths).
        kept.sort(key=lambda p: p.relative_to(repo_path).as_posix())
        skipped.sort(
            key=lambda pair: (
                pair[0].relative_to(repo_path).as_posix()
                if _under(pair[0], repo_path)
                else str(pair[0])
            )
        )

        return WalkResult(
            kept=kept, skipped=skipped, relpath_to_report_path=report_paths
        )


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Pure helper: True iff `child` is a descendant of `parent`.

    Re-implemented (rather than delegating to `Path.is_relative_to`) so the
    semantics are pinned across platforms and the security-critical call
    site is unambiguous.
    """
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _under(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False
