"""A clean module that does one thing."""
from pathlib import Path


def safe_read(p: Path) -> str:
    return p.read_text(encoding="utf-8")
