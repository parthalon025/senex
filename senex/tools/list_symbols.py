"""senex.tools.list_symbols - file symbol extractor (AST + line-scan).

Implements spec section 5.11.1 (list_symbols in v1 tool set), section SEC-1
(path-safety), and section 11.1 threat model row list_symbols traversal.

Extraction strategy:

- Python / .pyi: stdlib ``ast`` module with ``end_lineno`` (Python 3.8+).
  Nested function bodies are skipped; only top-level functions, module-level
  functions, class-level methods, and class definitions are returned.
- All other supported languages: line-scan against per-language declaration
  regex patterns. End-line detection uses brace counting for braced languages
  and ``end``-keyword counting for Ruby. Approximate — exact for Python only.
- Unknown extensions: best-effort via a generic fallback regex; no end-line
  detection.

Capped at 100 symbols; ``truncated=True`` when more were found. All I/O on
``asyncio.to_thread`` (conventions §3).
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .context import ToolContext
from .exceptions import (
    PathOutsideRepo,
    SymlinkRefused,
    ToolDispatchFailed,
)
from .registry import ToolRegistry
from .safety import validate_repo_path

_TOOL_NAME: Final[str] = "list_symbols"
_DESCRIPTION: Final[str] = (
    "List all top-level functions, methods, and classes in a file with their "
    "line ranges and signatures. Exact for Python (AST-based); approximate for "
    "other languages. Useful for surveying large files and identifying functions "
    "over 50 lines."
)

_MAX_FILE_SIZE_BYTES: Final[int] = 5_000_000  # 5MB cap
_MAX_SYMBOLS: Final[int] = 100


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class SymbolEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["function", "async_function", "method", "async_method", "class"]
    start_line: int
    end_line: int | None  # None when language parser cannot determine it
    line_count: int | None  # None when end_line is None
    signature: str  # first line of declaration, stripped of leading whitespace


class ListSymbolsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str
    language: str  # e.g. "python", "typescript", "go"
    symbols: list[SymbolEntry]
    truncated: bool  # True if more than 100 symbols found (cap at 100)


class ListSymbolsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relpath: str = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXT_TO_LANG: Final[dict[str, str]] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".swift": "swift",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
}


def _detect_language(path: Path) -> str:
    return _EXT_TO_LANG.get(path.suffix.lower(), "unknown")


# ---------------------------------------------------------------------------
# Python extraction (AST-based)
# ---------------------------------------------------------------------------


def _format_python_sig(
    node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool
) -> str:
    """Build a compact single-line signature for a Python function/method."""
    prefix = "async def " if is_async else "def "
    args_parts: list[str] = []
    a = node.args

    # positional-only args (Python 3.8+)
    for arg in a.posonlyargs:
        part = arg.arg
        if arg.annotation is not None:
            part += f": {ast.unparse(arg.annotation)}"
        args_parts.append(part)
    if a.posonlyargs:
        args_parts.append("/")

    # regular args
    n_regular = len(a.args)
    n_defaults = len(a.defaults)
    for i, arg in enumerate(a.args):
        part = arg.arg
        if arg.annotation is not None:
            part += f": {ast.unparse(arg.annotation)}"
        default_idx = i - (n_regular - n_defaults)
        if default_idx >= 0:
            part += f"={ast.unparse(a.defaults[default_idx])}"
        args_parts.append(part)

    # *args
    if a.vararg is not None:
        part = f"*{a.vararg.arg}"
        if a.vararg.annotation is not None:
            part += f": {ast.unparse(a.vararg.annotation)}"
        args_parts.append(part)
    elif a.kwonlyargs:
        args_parts.append("*")

    # keyword-only args
    for i, arg in enumerate(a.kwonlyargs):
        part = arg.arg
        if arg.annotation is not None:
            part += f": {ast.unparse(arg.annotation)}"
        if a.kw_defaults[i] is not None:
            part += f"={ast.unparse(a.kw_defaults[i])}"  # type: ignore[arg-type]
        args_parts.append(part)

    # **kwargs
    if a.kwarg is not None:
        part = f"**{a.kwarg.arg}"
        if a.kwarg.annotation is not None:
            part += f": {ast.unparse(a.kwarg.annotation)}"
        args_parts.append(part)

    sig = f"{prefix}{node.name}({', '.join(args_parts)})"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"

    return sig[:200]


class _SymbolVisitor(ast.NodeVisitor):
    """Collect top-level classes, functions, and class methods."""

    def __init__(self) -> None:
        self._symbols: list[SymbolEntry] = []
        self._class_stack: list[str] = []
        self._in_function: bool = False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # type: ignore[override]
        self._symbols.append(
            SymbolEntry(
                name=node.name,
                kind="class",
                start_line=node.lineno,
                end_line=node.end_lineno,
                line_count=(
                    node.end_lineno - node.lineno + 1
                    if node.end_lineno is not None
                    else None
                ),
                signature=f"class {node.name}",
            )
        )
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # type: ignore[override]
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[override]
        self._visit_func(node)

    def _visit_func(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        if self._in_function:
            return  # skip nested functions
        is_async = isinstance(node, ast.AsyncFunctionDef)
        if self._class_stack:
            kind: Literal["function", "async_function", "method", "async_method", "class"] = (
                "async_method" if is_async else "method"
            )
            name = f"{self._class_stack[-1]}.{node.name}"
        else:
            kind = "async_function" if is_async else "function"
            name = node.name
        sig = _format_python_sig(node, is_async)
        self._symbols.append(
            SymbolEntry(
                name=name,
                kind=kind,
                start_line=node.lineno,
                end_line=node.end_lineno,
                line_count=(
                    node.end_lineno - node.lineno + 1
                    if node.end_lineno is not None
                    else None
                ),
                signature=sig,
            )
        )
        prev = self._in_function
        self._in_function = True
        self.generic_visit(node)
        self._in_function = prev


def _extract_python(source: str) -> list[SymbolEntry]:
    """Parse source with ``ast`` and collect symbols; returns [] on SyntaxError."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    visitor = _SymbolVisitor()
    visitor.visit(tree)
    return visitor._symbols


# ---------------------------------------------------------------------------
# Non-Python line-scan extraction
# ---------------------------------------------------------------------------

_LANG_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "typescript": [
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*[(<]"),
        re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"),
        re.compile(
            r"^\s*(?:public|private|protected|static|override|abstract|async|\s)*"
            r"(?:async\s+)?(\w+)\s*\("
        ),
    ],
    "javascript": [
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*[(<]"),
        re.compile(r"^\s*(?:export\s+)?class\s+(\w+)"),
    ],
    "go": [
        re.compile(r"^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\("),
        re.compile(r"^type\s+(\w+)\s+struct"),
        re.compile(r"^type\s+(\w+)\s+interface"),
    ],
    "rust": [
        re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]"),
        re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|impl)\s+(\w+)"),
    ],
    "java": [
        re.compile(
            r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized"
            r"|native|default|transient|volatile)\s+)*"
            r"(?:(?:<[^>]*>\s+)?\w+\s+)?(\w+)\s*\("
        ),
        re.compile(
            r"^\s*(?:(?:public|private|protected|abstract|final|static)\s+)*"
            r"(?:class|interface|enum|record)\s+(\w+)"
        ),
    ],
    "kotlin": [
        re.compile(
            r"^\s*(?:(?:public|private|protected|internal|override|open|abstract"
            r"|suspend|inline|operator|infix|external|tailrec)\s+)*"
            r"fun\s+(?:<[^>]*>\s+)?(\w+)\s*[(<]"
        ),
        re.compile(
            r"^\s*(?:(?:data|sealed|open|abstract|inner)\s+)?"
            r"(?:class|interface|object|enum class)\s+(\w+)"
        ),
    ],
    "csharp": [
        re.compile(
            r"^\s*(?:(?:public|private|protected|internal|static|virtual|override"
            r"|abstract|async|sealed|partial|new|extern|unsafe|readonly)\s+)*"
            r"(?:(?:<[^>]*>\s+)?[\w?]+\s+)?(\w+)\s*\("
        ),
        re.compile(
            r"^\s*(?:(?:public|private|protected|internal|static|abstract|sealed|partial)\s+)*"
            r"(?:class|interface|struct|enum|record)\s+(\w+)"
        ),
    ],
    "ruby": [
        re.compile(r"^\s*def\s+(?:self\.)?(\w+)"),
        re.compile(r"^\s*class\s+(\w+)"),
        re.compile(r"^\s*module\s+(\w+)"),
    ],
    "swift": [
        re.compile(
            r"^\s*(?:(?:public|private|internal|fileprivate|open|static|class|override"
            r"|mutating|nonmutating|lazy|weak|unowned|required|convenience|final)\s+)*"
            r"func\s+(\w+)\s*[(<]"
        ),
        re.compile(
            r"^\s*(?:(?:public|private|internal|fileprivate|open|final)\s+)*"
            r"(?:class|struct|protocol|enum|actor)\s+(\w+)"
        ),
    ],
    "cpp": [
        re.compile(
            r"^(?:(?:static|virtual|inline|explicit|constexpr|consteval|constinit"
            r"|noexcept|override|final|friend)\s+)*"
            r"(?:[\w:*&<>]+\s+)+(\w+)\s*\("
        ),
        re.compile(r"^\s*(?:class|struct|enum(?:\s+class)?|union)\s+(\w+)"),
    ],
    "php": [
        re.compile(
            r"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*"
            r"function\s+(\w+)\s*\("
        ),
        re.compile(r"^\s*(?:(?:abstract|final)\s+)?class\s+(\w+)"),
    ],
    "shell": [
        re.compile(r"^(?:function\s+)?(\w+)\s*\(\s*\)\s*\{"),
    ],
    "powershell": [
        re.compile(r"^\s*function\s+([\w-]+)\s*(?:\{|\()"),
    ],
}

_GENERIC_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*(?:(?:pub|public|private|protected|static|async|export|override)\s+)*"
    r"(?:function|func|fn|def|fun|sub|proc)\s+(\w+)\s*[(<{]",
    re.IGNORECASE,
)

_BRACED_LANGS: Final[frozenset[str]] = frozenset({
    "typescript", "javascript", "go", "rust", "java", "kotlin",
    "csharp", "swift", "cpp", "php", "shell", "powershell",
})

_CLASS_KW_RE: re.Pattern[str] = re.compile(
    r"\b(?:class|struct|interface|enum|trait|type|module|record|protocol|actor)\b"
)

_ASYNC_RE: re.Pattern[str] = re.compile(r"\basync\b")


def _find_brace_end(lines: list[str], start_idx: int) -> int | None:
    """Return 1-based line number of the closing brace, or None if not found."""
    depth = 0
    found_open = False
    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
        if found_open and depth == 0:
            return i + 1  # 1-based
    return None


_RUBY_OPEN_RE: re.Pattern[str] = re.compile(
    r"^(def|class|module|do|begin|if|unless|case|while|until|for)\b"
)


def _find_ruby_end(lines: list[str], start_idx: int) -> int | None:
    """Return 1-based line number of the matching ``end``, or None."""
    depth = 0
    for i in range(start_idx, len(lines)):
        line = lines[i].strip()
        if _RUBY_OPEN_RE.match(line):
            depth += 1
        if line == "end" or line.startswith("end ") or line.startswith("end#"):
            depth -= 1
            if depth == 0:
                return i + 1  # 1-based
    return None


_NOISE_NAMES: Final[frozenset[str]] = frozenset({
    "if", "for", "while", "switch", "match",
})


def _extract_by_patterns(
    source: str,
    lang: str,
    patterns: list[re.Pattern[str]],
) -> list[SymbolEntry]:
    """Scan lines against ``patterns`` and build symbol entries."""
    lines = source.splitlines()
    symbols: list[SymbolEntry] = []

    for i, line in enumerate(lines):
        for pat in patterns:
            m = pat.match(line)
            if m and m.group(1):
                name = m.group(1)
                if len(name) < 2 or name in _NOISE_NAMES:
                    continue
                start_line = i + 1  # 1-based

                pre = line[: m.start(1)]
                is_async = bool(_ASYNC_RE.search(pre))
                is_class = bool(_CLASS_KW_RE.search(line[: m.start(1) + 10]))

                if is_class:
                    kind: Literal["function", "async_function", "method", "async_method", "class"] = "class"
                elif is_async:
                    kind = "async_function"
                else:
                    kind = "function"

                if lang in _BRACED_LANGS:
                    end_line: int | None = _find_brace_end(lines, i)
                elif lang == "ruby":
                    end_line = _find_ruby_end(lines, i)
                else:
                    end_line = None

                lc = (end_line - start_line + 1) if end_line is not None else None
                symbols.append(
                    SymbolEntry(
                        name=name,
                        kind=kind,
                        start_line=start_line,
                        end_line=end_line,
                        line_count=lc,
                        signature=line.strip()[:200],
                    )
                )
                break  # only the first matching pattern per line

    # Deduplicate by start_line (multiple patterns may match the same line).
    seen: set[int] = set()
    deduped: list[SymbolEntry] = []
    for s in symbols:
        if s.start_line not in seen:
            seen.add(s.start_line)
            deduped.append(s)
    return deduped


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def list_symbols_handler(
    inp: ListSymbolsInput, ctx: ToolContext
) -> ListSymbolsOutput:
    """Extract symbols from ``inp.relpath`` after path-safety validation.

    File I/O and symbol extraction run on a worker thread (conventions §3:
    no sync I/O in async paths). Python extraction uses the stdlib ``ast``
    module; all other languages use a line-scan approach against
    per-language declaration patterns.
    """
    try:
        resolved = validate_repo_path(inp.relpath, ctx.repo_root)
    except (PathOutsideRepo, SymlinkRefused):
        raise

    def _extract_sync() -> ListSymbolsOutput:
        if not resolved.exists():
            raise ToolDispatchFailed(f"file not found: {inp.relpath}")
        if not resolved.is_file():
            raise ToolDispatchFailed(f"not a regular file: {inp.relpath}")

        size = resolved.stat().st_size
        if size > _MAX_FILE_SIZE_BYTES:
            raise ToolDispatchFailed(
                f"file too large ({size} > {_MAX_FILE_SIZE_BYTES} bytes)"
            )

        source = resolved.read_text(encoding="utf-8", errors="replace")
        lang = _detect_language(resolved)

        if lang == "python":
            symbols = _extract_python(source)
        else:
            patterns = _LANG_PATTERNS.get(lang, [_GENERIC_PATTERN])
            symbols = _extract_by_patterns(source, lang, patterns)

        truncated = len(symbols) > _MAX_SYMBOLS
        return ListSymbolsOutput(
            relpath=inp.relpath,
            language=lang,
            symbols=symbols[:_MAX_SYMBOLS],
            truncated=truncated,
        )

    return await asyncio.to_thread(_extract_sync)


def register_list_symbols(registry: ToolRegistry) -> None:
    """Register the ``list_symbols`` tool with the given registry."""
    registry.register(
        _TOOL_NAME,
        ListSymbolsInput,
        list_symbols_handler,  # type: ignore[arg-type]
        description=_DESCRIPTION,
    )


__all__ = [
    "ListSymbolsInput",
    "ListSymbolsOutput",
    "SymbolEntry",
    "list_symbols_handler",
    "register_list_symbols",
]
