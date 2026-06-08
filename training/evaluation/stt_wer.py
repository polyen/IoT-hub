"""STT accuracy benchmark: WER / CER + per-utterance latency on a real corpus.

Unlike ``stt_latency.py`` (which only times a synthetic sine wave), this runner
transcribes a **labelled** Ukrainian command corpus through the *production* STT
backend (``hub.edge.voice.hailo_whisper.get_backend``) and scores it against
reference transcripts. The selected engine therefore mirrors what the live
system uses — controlled by ``STT_BACKEND`` / ``FASTER_WHISPER_MODEL`` exactly
as in production.

Honesty contract: if the corpus, audio files, or an STT backend are missing,
the result is ``{"measured": false, "pass": null, ...}`` with an explanatory
note — **never** a fabricated WER. Only a real transcription run sets
``measured: true``.

Corpus manifest (JSONL, one object per line)::

    {"audio": "clip_001.wav", "text": "увімкни світло у вітальні"}

``audio`` is resolved relative to the manifest's directory. See
``training/evaluation/stt_corpus/README.md`` for how to record one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
from pathlib import Path
from typing import Any

from training.evaluation.wer import corpus_error_rates, utterance_error_rates

logger = logging.getLogger(__name__)

# WER target for short Ukrainian commands on faster-whisper-base (uk). Drawn
# from §4.3.4 of the thesis (the ~8-10% working-engine figure being validated).
DEFAULT_WER_TARGET = 0.12


def _not_measured(note: str) -> dict[str, Any]:
    return {
        "measured": False,
        "pass": None,
        "wer": None,
        "cer": None,
        "n_utterances": 0,
        "note": note,
    }


def load_manifest(manifest_path: Path) -> list[dict[str, str]]:
    """Read a JSONL manifest → list of ``{"audio", "text"}`` entries."""
    entries: list[dict[str, str]] = []
    for line_no, line in enumerate(manifest_path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{manifest_path}:{line_no}: invalid JSON ({exc})") from exc
        if "audio" not in obj or "text" not in obj:
            raise ValueError(f"{manifest_path}:{line_no}: entry needs 'audio' and 'text' keys")
        entries.append({"audio": str(obj["audio"]), "text": str(obj["text"])})
    return entries


async def _transcribe_corpus(
    entries: list[dict[str, str]],
    base_dir: Path,
    *,
    force_cpu: bool,
    language: str,
) -> dict[str, Any]:
    """Transcribe every clip and score WER/CER + latency. Assumes deps present."""
    from hub.edge.voice.hailo_whisper import get_backend

    backend = get_backend(force_cpu=force_cpu, language=language)
    backend_name = type(backend).__name__

    pairs: list[tuple[str, str]] = []
    per_utterance: list[dict[str, Any]] = []
    latencies: list[float] = []

    for entry in entries:
        audio_path = (base_dir / entry["audio"]).resolve()
        if not audio_path.exists():
            logger.warning("Missing audio, skipping: %s", audio_path)
            continue
        audio_bytes = audio_path.read_bytes()

        t0 = time.perf_counter()
        hypothesis = await backend.transcribe(audio_bytes)
        latency = time.perf_counter() - t0
        latencies.append(latency)

        reference = entry["text"]
        rates = utterance_error_rates(reference, hypothesis)
        pairs.append((reference, hypothesis))
        per_utterance.append(
            {
                "audio": entry["audio"],
                "reference": reference,
                "hypothesis": hypothesis,
                "wer": round(rates.wer, 4),
                "cer": round(rates.cer, 4),
                "latency_s": round(latency, 3),
            }
        )

    if not pairs:
        return _not_measured("manifest had entries but no audio files were found on disk")

    agg = corpus_error_rates(pairs)
    latencies_sorted = sorted(latencies)
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)

    return {
        "measured": True,
        "backend": backend_name,
        "language": language,
        "wer": round(agg.wer, 4),
        "cer": round(agg.cer, 4),
        "n_utterances": len(pairs),
        "ref_words": agg.ref_words,
        "word_errors": agg.word_errors,
        "latency_mean_s": round(statistics.mean(latencies), 3),
        "latency_p50_s": round(latencies_sorted[len(latencies_sorted) // 2], 3),
        "latency_p95_s": round(latencies_sorted[p95_idx], 3),
        "target_wer": DEFAULT_WER_TARGET,
        "pass": agg.wer <= DEFAULT_WER_TARGET,
        "per_utterance": per_utterance,
    }


def run(
    manifest_path: Path,
    *,
    force_cpu: bool = False,
    language: str = "uk",
) -> dict[str, Any]:
    """Top-level entry: validate inputs, then transcribe + score."""
    if not manifest_path.exists():
        return _not_measured(f"corpus manifest not found at {manifest_path}")

    entries = load_manifest(manifest_path)
    if not entries:
        return _not_measured(f"corpus manifest {manifest_path} is empty")

    try:
        from hub.edge.voice.hailo_whisper import FASTER_WHISPER_AVAILABLE, HAILO_AVAILABLE
    except ImportError:
        return _not_measured("hub.edge.voice unavailable in this environment")

    if not (FASTER_WHISPER_AVAILABLE or HAILO_AVAILABLE):
        return _not_measured(
            "no STT backend installed (need faster-whisper for CPU or hailo_platform for NPU)"
        )

    return asyncio.run(
        _transcribe_corpus(
            entries,
            manifest_path.parent,
            force_cpu=force_cpu,
            language=language,
        )
    )


def _to_markdown(result: dict[str, Any]) -> str:
    lines = ["# STT WER Benchmark", ""]
    if not result.get("measured"):
        lines += [
            "**Status:** ⚠️ not measured",
            "",
            f"_{result.get('note', 'no note')}_",
            "",
            "Record a corpus (see `training/evaluation/stt_corpus/README.md`) and re-run.",
        ]
        return "\n".join(lines) + "\n"

    passed = "✓" if result.get("pass") else "✗"
    lines += [
        f"**Backend:** `{result['backend']}` · language `{result['language']}` · "
        f"{result['n_utterances']} utterances",
        "",
        "| Metric | Value | Target | Pass |",
        "|--------|-------|--------|------|",
        f"| WER | {result['wer']:.4f} | ≤{result['target_wer']} | {passed} |",
        f"| CER | {result['cer']:.4f} | — | — |",
        f"| Latency p50 | {result['latency_p50_s']} s | — | — |",
        f"| Latency p95 | {result['latency_p95_s']} s | — | — |",
        f"| Latency mean | {result['latency_mean_s']} s | — | — |",
        "",
        f"Word errors: {result['word_errors']} / {result['ref_words']} reference words.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="STT WER/CER benchmark on a labelled UA corpus")
    parser.add_argument(
        "--manifest",
        default="training/evaluation/stt_corpus/manifest.jsonl",
        help="JSONL corpus manifest ({audio, text} per line)",
    )
    parser.add_argument("--output", default="materials/evaluation_results")
    parser.add_argument("--force-cpu", action="store_true", help="Force the CPU STT backend")
    parser.add_argument("--language", default="uk")
    args = parser.parse_args()

    result = run(
        Path(args.manifest),
        force_cpu=args.force_cpu,
        language=args.language,
    )

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stt_wer.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (out_dir / "stt_wer.md").write_text(_to_markdown(result))

    logger.info("Results written to %s", out_dir / "stt_wer.json")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "per_utterance"}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
