from __future__ import annotations

import pytest

from hub.edge.agent.grammars import load_grammar

# ── 1: load_grammar("light") returns non-empty string ─────────────────────────


def test_load_grammar_light_nonempty() -> None:
    grammar = load_grammar("light")
    assert isinstance(grammar, str)
    assert len(grammar) > 0


# ── 2: load_grammar("timer") contains "set_timer" ─────────────────────────────


def test_load_grammar_timer_contains_set_timer() -> None:
    grammar = load_grammar("timer")
    assert "set_timer" in grammar


# ── 3: load_grammar("nonexistent") raises FileNotFoundError ───────────────────


def test_load_grammar_nonexistent_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_grammar("nonexistent")
