"""End-to-end voice command latency: mic-audio → resolved intent.

Times the **critical path** the user actually waits on (thesis NFR-2, ≤5 s):

  1. **STT** — ``get_backend().transcribe(audio)`` (the production engine).
  2. **Intent** — ``IntentClassifier.classify(text)`` (production ONNX SetFit).

Wake-word / VAD are excluded on purpose: they run *before* the user finishes
speaking, so they are not part of the response-time budget. The LLM is likewise
excluded — by design it is off the critical path (§4.2.3).

Runs over the labelled STT corpus (real recorded clips) and reports per-stage
and total p50/p95 against the 5 s budget. Run it **on the RPi 5** — latency is
hardware-bound, so a laptop run is not representative of the deployed system.

Honesty contract: missing corpus, STT backend, or intent model →
``{"measured": false, ...}`` with a note; never fabricated timings.
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

from training.evaluation.stt_wer import load_manifest

logger = logging.getLogger(__name__)

# NFR-2 interactive budget for a voice turn (seconds).
E2E_BUDGET_S = 5.0
DEFAULT_INTENT_MODEL = Path("models/intent_classifier")


def _not_measured(note: str) -> dict[str, Any]:
    return {"measured": False, "pass": None, "note": note}


def _percentiles(values: list[float]) -> dict[str, float]:
    s = sorted(values)
    p95_idx = max(0, int(len(s) * 0.95) - 1)
    return {
        "p50": round(s[len(s) // 2], 3),
        "p95": round(s[p95_idx], 3),
        "mean": round(statistics.mean(values), 3),
    }


async def _run(
    entries: list[dict[str, str]],
    base_dir: Path,
    intent_model_dir: Path,
    *,
    force_cpu: bool,
    language: str,
) -> dict[str, Any]:
    from hub.edge.agent.intent_classifier import IntentClassifier
    from hub.edge.voice.hailo_whisper import get_backend

    backend = get_backend(force_cpu=force_cpu, language=language)
    classifier = IntentClassifier(intent_model_dir)
    classifier.load()
    if not classifier.is_loaded:
        return _not_measured(f"intent classifier failed to load from {intent_model_dir}")

    stt_lat: list[float] = []
    intent_lat: list[float] = []
    total_lat: list[float] = []
    per_utterance: list[dict[str, Any]] = []

    for entry in entries:
        audio_path = (base_dir / entry["audio"]).resolve()
        if not audio_path.exists():
            continue
        audio_bytes = audio_path.read_bytes()

        t0 = time.perf_counter()
        text = await backend.transcribe(audio_bytes)
        t1 = time.perf_counter()
        label, conf = classifier.classify(text)
        t2 = time.perf_counter()

        stt_s, intent_s, total_s = t1 - t0, t2 - t1, t2 - t0
        stt_lat.append(stt_s)
        intent_lat.append(intent_s)
        total_lat.append(total_s)
        per_utterance.append(
            {
                "audio": entry["audio"],
                "transcript": text,
                "intent": label,
                "confidence": round(conf, 3),
                "stt_s": round(stt_s, 3),
                "intent_s": round(intent_s, 3),
                "total_s": round(total_s, 3),
            }
        )

    if not total_lat:
        return _not_measured("no audio files found on disk for the manifest")

    total_stats = _percentiles(total_lat)
    return {
        "measured": True,
        "backend": type(backend).__name__,
        "n_utterances": len(total_lat),
        "stages": {
            "stt": _percentiles(stt_lat),
            "intent": _percentiles(intent_lat),
            "total": total_stats,
        },
        "budget_s": E2E_BUDGET_S,
        "pass": total_stats["p95"] <= E2E_BUDGET_S,
        "per_utterance": per_utterance,
    }


def run(
    manifest_path: Path,
    intent_model_dir: Path,
    *,
    force_cpu: bool = False,
    language: str = "uk",
) -> dict[str, Any]:
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
        return _not_measured("no STT backend installed (faster-whisper or hailo_platform)")

    return asyncio.run(
        _run(
            entries,
            manifest_path.parent,
            intent_model_dir,
            force_cpu=force_cpu,
            language=language,
        )
    )


def _to_markdown(result: dict[str, Any]) -> str:
    lines = ["# End-to-End Voice Latency", ""]
    if not result.get("measured"):
        return "\n".join(
            lines + ["**Status:** ⚠️ not measured", "", f"_{result.get('note', '')}_", ""]
        )
    st = result["stages"]
    passed = "✓" if result.get("pass") else "✗"
    lines += [
        f"**Backend:** `{result['backend']}` · {result['n_utterances']} utterances · "
        f"budget {result['budget_s']} s",
        "",
        "| Stage | p50 (s) | p95 (s) | mean (s) |",
        "|-------|---------|---------|----------|",
        f"| STT | {st['stt']['p50']} | {st['stt']['p95']} | {st['stt']['mean']} |",
        f"| Intent | {st['intent']['p50']} | {st['intent']['p95']} | {st['intent']['mean']} |",
        f"| **Total** | **{st['total']['p50']}** | **{st['total']['p95']}** | "
        f"**{st['total']['mean']}** |",
        "",
        f"NFR-2 (total p95 ≤ {result['budget_s']} s): {passed}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="End-to-end voice command latency (STT → intent)")
    parser.add_argument("--manifest", default="training/evaluation/stt_corpus/manifest.jsonl")
    parser.add_argument("--intent-model", default=str(DEFAULT_INTENT_MODEL))
    parser.add_argument("--output", default="materials/evaluation_results")
    parser.add_argument("--force-cpu", action="store_true")
    parser.add_argument("--language", default="uk")
    args = parser.parse_args()

    result = run(
        Path(args.manifest),
        Path(args.intent_model),
        force_cpu=args.force_cpu,
        language=args.language,
    )

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "voice_e2e_latency.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    (out_dir / "voice_e2e_latency.md").write_text(_to_markdown(result))

    logger.info("Results written to %s", out_dir / "voice_e2e_latency.json")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "per_utterance"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
