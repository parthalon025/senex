"""Per-language anchor selector + per-file user prompt builder.

Implements spec section 5.3 (anchors) and section 5.1 trust-boundary
template (per-file user prompt). The user prompt template is in
``per_file_user.md`` (Task 2.4) and is loaded once at module import.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent

EXT_TO_ANCHOR: dict[str, str] = {
    ".py": "lang_python.md",
    ".ts": "lang_typescript.md",
    ".tsx": "lang_typescript.md",
    ".rs": "lang_rust.md",
    ".go": "lang_go.md",
    ".cs": "lang_csharp.md",
}


class PromptError(Exception):
    """Base for prompt-loading errors."""


class PromptTemplateUnsubstituted(PromptError):
    """``build_user_prompt()`` finished with leftover ``{...}`` tokens.

    Indicates a missing variable or a template typo.
    """


@lru_cache(maxsize=16)
def _read_prompt(filename: str) -> str:
    p = _PROMPTS_DIR / filename
    return p.read_text(encoding="utf-8")


def select_anchor(file_path: str | Path) -> str | None:
    """Return the anchor markdown for ``file_path``'s extension, or None.

    Args:
        file_path: a file path (absolute, relative, or just basename); only
            the suffix is consulted.

    Returns:
        The anchor body (UTF-8 string) or None when the extension is not in
        ``EXT_TO_ANCHOR``.
    """
    suffix = Path(file_path).suffix.lower()
    filename = EXT_TO_ANCHOR.get(suffix)
    if filename is None:
        return None
    return _read_prompt(filename)


_UNSUBBED_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


_TEMPLATE_KEYS: tuple[str, ...] = (
    "file_relpath",
    "language",
    "graph_context",
    "numbered_source",
)


def build_user_prompt(
    *,
    file_relpath: str,
    language: str,
    graph_context: str,
    numbered_source: str,
) -> str:
    """Compose the per-file user prompt from ``per_file_user.md``.

    Args:
        file_relpath: forward-slash relpath under repo root.
        language: language anchor key (e.g., ``"python"``).
        graph_context: rendered awareness block (or sentinel).
        numbered_source: file source with line-number prefixes.

    Returns:
        The substituted prompt body.

    Raises:
        PromptTemplateUnsubstituted: a template token (e.g. ``{file_relpath}``)
            was missing from the template — indicates a template authoring bug.
            Does NOT fire on ``{word}`` patterns inside substituted values
            (graph context, source code) since those are external data.
    """
    template = _read_prompt("per_file_user.md")
    values = (file_relpath, language, graph_context, numbered_source)
    missing = [
        "{" + k + "}"
        for k in _TEMPLATE_KEYS
        if "{" + k + "}" not in template
    ]
    if missing:
        raise PromptTemplateUnsubstituted(
            f"unsubstituted template tokens: {missing}"
        )
    out = template
    for key, val in zip(_TEMPLATE_KEYS, values):
        out = out.replace("{" + key + "}", val)
    return out
