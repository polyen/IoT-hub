"""Word- and character-error-rate computation with Ukrainian-aware normalisation.

Self-contained (no ``jiwer`` dependency): a standard Levenshtein edit distance
over token / character streams, plus a normalisation step tuned for short
Ukrainian smart-home commands (lowercase, apostrophe unification, punctuation
strip, whitespace collapse).

WER and CER are the standard STT quality metrics; we expose both because for
short commands a single wrong character (e.g. ``світло`` → ``світла``) inflates
WER disproportionately, so CER gives a complementary view.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ukrainian apostrophe variants → a single canonical apostrophe. Whisper-family
# models emit several Unicode code points for the same sound (U+2019, U+02BC,
# backtick); collapsing them avoids counting a stylistic choice as an error.
_APOSTROPHES = "’ʼ`´"
_CANONICAL_APOSTROPHE = "'"

# Punctuation we strip before scoring — STT punctuation is not part of the
# command semantics and is inconsistently emitted across backends.
_PUNCT = '.,!?;:…"«»()[]{}—–-'


def normalize(text: str) -> str:
    """Lowercase, unify apostrophes, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    for ch in _APOSTROPHES:
        text = text.replace(ch, _CANONICAL_APOSTROPHE)
    # Replace punctuation with spaces (not nothing) so ``увімкни,світло`` splits.
    cleaned = "".join(" " if ch in _PUNCT else ch for ch in text)
    return " ".join(cleaned.split())


def _edit_distance(ref: list[str], hyp: list[str]) -> int:
    """Levenshtein distance between two token sequences (sub/ins/del = 1)."""
    n, m = len(ref), len(hyp)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,  # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution / match
            )
        prev = curr
    return prev[m]


@dataclass
class ErrorRates:
    """WER/CER for one or many utterances plus the underlying counts."""

    wer: float
    cer: float
    ref_words: int
    word_errors: int
    ref_chars: int
    char_errors: int


def utterance_error_rates(reference: str, hypothesis: str) -> ErrorRates:
    """Compute WER and CER for a single (reference, hypothesis) pair."""
    ref_norm = normalize(reference)
    hyp_norm = normalize(hypothesis)

    ref_words = ref_norm.split()
    hyp_words = hyp_norm.split()
    word_errors = _edit_distance(ref_words, hyp_words)

    ref_chars = list(ref_norm.replace(" ", ""))
    hyp_chars = list(hyp_norm.replace(" ", ""))
    char_errors = _edit_distance(ref_chars, hyp_chars)

    wer = word_errors / len(ref_words) if ref_words else (1.0 if hyp_words else 0.0)
    cer = char_errors / len(ref_chars) if ref_chars else (1.0 if hyp_chars else 0.0)

    return ErrorRates(
        wer=wer,
        cer=cer,
        ref_words=len(ref_words),
        word_errors=word_errors,
        ref_chars=len(ref_chars),
        char_errors=char_errors,
    )


def corpus_error_rates(pairs: list[tuple[str, str]]) -> ErrorRates:
    """Aggregate WER/CER over a corpus.

    Aggregation is over total errors / total reference length (the standard
    micro-average), *not* a mean of per-utterance rates — short utterances would
    otherwise dominate.
    """
    total = ErrorRates(0.0, 0.0, 0, 0, 0, 0)
    for reference, hypothesis in pairs:
        r = utterance_error_rates(reference, hypothesis)
        total.ref_words += r.ref_words
        total.word_errors += r.word_errors
        total.ref_chars += r.ref_chars
        total.char_errors += r.char_errors

    total.wer = total.word_errors / total.ref_words if total.ref_words else 0.0
    total.cer = total.char_errors / total.ref_chars if total.ref_chars else 0.0
    return total
