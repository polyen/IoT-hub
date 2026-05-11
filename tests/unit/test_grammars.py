from __future__ import annotations

import pytest

from hub.edge.agent.grammars import GRAMMARS_DIR, load_grammar

ALL_GRAMMARS = ["light", "timer", "relay", "push", "ask_user"]


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


# ── 4-8: load_grammar(name) returns string content for each grammar ──────────


def test_load_grammar_light_is_string() -> None:
    grammar = load_grammar("light")
    assert isinstance(grammar, str)
    assert grammar.strip() != ""


def test_load_grammar_timer_is_string() -> None:
    grammar = load_grammar("timer")
    assert isinstance(grammar, str)
    assert grammar.strip() != ""


def test_load_grammar_relay_is_string() -> None:
    grammar = load_grammar("relay")
    assert isinstance(grammar, str)
    assert grammar.strip() != ""


def test_load_grammar_push_is_string() -> None:
    grammar = load_grammar("push")
    assert isinstance(grammar, str)
    assert grammar.strip() != ""


def test_load_grammar_ask_user_is_string() -> None:
    grammar = load_grammar("ask_user")
    assert isinstance(grammar, str)
    assert grammar.strip() != ""


# ── 9: load_grammar raises FileNotFoundError for empty name ──────────────────


def test_load_grammar_empty_name_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_grammar("")


# ── 10-14: each grammar contains expected JSON keys ──────────────────────────


def test_light_grammar_contains_expected_keys() -> None:
    grammar = load_grammar("light")
    assert "topic" in grammar
    assert "payload" in grammar
    assert "state" in grammar
    assert "brightness" in grammar


def test_timer_grammar_contains_expected_keys() -> None:
    grammar = load_grammar("timer")
    assert "tool" in grammar
    assert "duration_sec" in grammar
    assert "label" in grammar


def test_relay_grammar_contains_expected_keys() -> None:
    grammar = load_grammar("relay")
    assert "topic" in grammar
    assert "payload" in grammar
    assert "cmd" in grammar
    assert "relay_on" in grammar
    assert "relay_off" in grammar
    assert "relay_toggle" in grammar


def test_push_grammar_contains_expected_keys() -> None:
    grammar = load_grammar("push")
    assert "title" in grammar
    assert "message" in grammar
    assert "priority" in grammar
    assert "default" in grammar
    assert "high" in grammar
    assert "urgent" in grammar


def test_ask_user_grammar_contains_expected_keys() -> None:
    grammar = load_grammar("ask_user")
    assert "question" in grammar


# ── 15-19: each grammar starts with `root` rule ──────────────────────────────


def test_light_grammar_starts_with_root() -> None:
    grammar = load_grammar("light")
    assert grammar.lstrip().startswith("root")


def test_timer_grammar_starts_with_root() -> None:
    grammar = load_grammar("timer")
    assert grammar.lstrip().startswith("root")


def test_relay_grammar_starts_with_root() -> None:
    grammar = load_grammar("relay")
    assert grammar.lstrip().startswith("root")


def test_push_grammar_starts_with_root() -> None:
    grammar = load_grammar("push")
    assert grammar.lstrip().startswith("root")


def test_ask_user_grammar_starts_with_root() -> None:
    grammar = load_grammar("ask_user")
    assert grammar.lstrip().startswith("root")


# ── 20: load_grammar is idempotent (calling twice returns same content) ──────


def test_load_grammar_idempotent() -> None:
    first = load_grammar("light")
    second = load_grammar("light")
    assert first == second


# ── 21: load_grammar idempotent for all grammars ─────────────────────────────


def test_load_grammar_idempotent_all() -> None:
    for name in ALL_GRAMMARS:
        first = load_grammar(name)
        second = load_grammar(name)
        assert first == second, f"{name} grammar not idempotent"


# ── 22: each grammar defines a `string` non-terminal ─────────────────────────


def test_all_grammars_define_string_rule() -> None:
    for name in ALL_GRAMMARS:
        grammar = load_grammar(name)
        assert "string ::=" in grammar, f"{name} missing string rule"


# ── 23: each grammar defines a `ws` (whitespace) non-terminal ────────────────


def test_all_grammars_define_ws_rule() -> None:
    for name in ALL_GRAMMARS:
        grammar = load_grammar(name)
        assert "ws" in grammar, f"{name} missing ws rule"


# ── 24: GRAMMARS_DIR points to a valid directory ─────────────────────────────


def test_grammars_dir_exists() -> None:
    assert GRAMMARS_DIR.exists()
    assert GRAMMARS_DIR.is_dir()


# ── 25: every grammar file exists on disk ────────────────────────────────────


def test_all_grammar_files_exist() -> None:
    for name in ALL_GRAMMARS:
        path = GRAMMARS_DIR / f"{name}.gbnf"
        assert path.exists(), f"{path} missing"


# ── 26: load_grammar with path-traversal style name fails ────────────────────


def test_load_grammar_path_traversal_fails() -> None:
    with pytest.raises(FileNotFoundError):
        load_grammar("../../../etc/passwd")


# ── 27: light grammar enumerates valid states ────────────────────────────────


def test_light_grammar_state_enum() -> None:
    grammar = load_grammar("light")
    # GBNF embeds enum strings via escaped quotes, e.g. \"on\"
    assert "on" in grammar
    assert "off" in grammar
    assert "toggle" in grammar


# ── 28: timer grammar references duration non-terminal ───────────────────────


def test_timer_grammar_duration_rule() -> None:
    grammar = load_grammar("timer")
    assert "duration ::=" in grammar


# ── 29: push grammar enumerates valid priorities ─────────────────────────────


def test_push_grammar_priority_enum() -> None:
    grammar = load_grammar("push")
    assert "priority ::=" in grammar


# ── 30: load_grammar returns text-mode content (no bytes) ────────────────────


def test_load_grammar_returns_str_not_bytes() -> None:
    grammar = load_grammar("light")
    assert isinstance(grammar, str)
    assert not isinstance(grammar, bytes)
