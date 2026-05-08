"""GBNF grammar files for llama-cpp constrained generation."""

from __future__ import annotations

from pathlib import Path

GRAMMARS_DIR = Path(__file__).parent


def load_grammar(name: str) -> str:
    """Load grammar by name (without .gbnf extension)."""
    path = GRAMMARS_DIR / f"{name}.gbnf"
    if not path.exists():
        raise FileNotFoundError(f"Grammar not found: {path}")
    return path.read_text()
