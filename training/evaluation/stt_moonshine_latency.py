"""Latency + WER benchmark for Moonshine-base-uk (Ukrainian) on the STT corpus.

Ukrainian Moonshine ships **only as safetensors** (no ONNX), so this loads it
through ``transformers`` (``MoonshineForConditionalGeneration`` /
``AutoModelForSpeechSeq2Seq``) on CPU. The model is tiny (~61 M params), so the
PyTorch path is still fast — the point of this script is to confirm the latency
on the *real RPi 5 CPU* before committing to Moonshine as the working STT engine
(thesis §4.3.4 / NFR-2 ≤ 5 s budget).

Run on the RPi 5, ideally twice — once idle and once with ``iot-hub-cv`` active —
to capture the realistic contended latency, same as the LLM measurement in
§4.3.2.

Generation is tuned the way short-command Moonshine needs: ``max_new_tokens``
scaled to clip duration (~6.5 tok/s) + ``no_repeat_ngram_size`` to stop the
runaway repetition the bare ``max_length`` config allows.

Honesty contract: missing deps / corpus → ``{"measured": false, ...}`` with a
note; never a fabricated number.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
from pathlib import Path
from typing import Any

from training.evaluation.wer import corpus_error_rates, utterance_error_rates

logger = logging.getLogger(__name__)

MODEL_ID = "UsefulSensors/moonshine-base-uk"
TARGET_SR = 16000
E2E_BUDGET_S = 5.0


def _not_measured(note: str) -> dict[str, Any]:
    return {"measured": False, "pass": None, "note": note}


def _load_manifest(manifest_path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in manifest_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        entries.append({"audio": str(obj["audio"]), "text": str(obj["text"])})
    return entries


def _resample(audio: Any, src_sr: int) -> Any:
    if src_sr == TARGET_SR:
        return audio
    try:
        import soxr  # type: ignore[import-untyped]

        return soxr.resample(audio, src_sr, TARGET_SR)
    except ImportError:
        from scipy.signal import resample as _scipy_resample  # type: ignore[import-untyped]

        n = round(len(audio) * TARGET_SR / src_sr)
        return _scipy_resample(audio, n)


def run(manifest_path: Path, *, warmup: int = 3, onnx_dir: str | None = None) -> dict[str, Any]:
    if not manifest_path.exists():
        return _not_measured(f"corpus manifest not found at {manifest_path}")
    entries = _load_manifest(manifest_path)
    if not entries:
        return _not_measured(f"corpus manifest {manifest_path} is empty")

    try:
        import soundfile as sf
        import torch
        from transformers import AutoProcessor
    except ImportError as exc:
        return _not_measured(
            f"missing dependency ({exc.name}); install: "
            "pip install torch transformers soundfile soxr"
        )

    # Two runtimes: optimised onnxruntime (--onnx-dir, the production target) or
    # the plain PyTorch reference. Same generate() API for both.
    if onnx_dir:
        try:
            from optimum.onnxruntime import ORTModelForSpeechSeq2Seq
        except ImportError:
            return _not_measured("--onnx-dir set but optimum is missing; pip install optimum")
        logger.info("Loading ONNX model from %s via onnxruntime …", onnx_dir)
        processor = AutoProcessor.from_pretrained(onnx_dir)  # type: ignore[no-untyped-call]
        model = ORTModelForSpeechSeq2Seq.from_pretrained(onnx_dir)
        runtime = "onnxruntime"
        model_label = onnx_dir
    else:
        from transformers import AutoModelForSpeechSeq2Seq

        logger.info("Loading %s via transformers …", MODEL_ID)
        processor = AutoProcessor.from_pretrained(MODEL_ID)  # type: ignore[no-untyped-call]
        model = AutoModelForSpeechSeq2Seq.from_pretrained(MODEL_ID).eval()
        runtime = "transformers/pytorch-cpu"
        model_label = MODEL_ID

    base_dir = manifest_path.parent
    pairs: list[tuple[str, str]] = []
    latencies: list[float] = []
    per_utterance: list[dict[str, Any]] = []

    for i, entry in enumerate(entries):
        audio_path = (base_dir / entry["audio"]).resolve()
        if not audio_path.exists():
            logger.warning("missing audio, skipping: %s", audio_path)
            continue
        audio, src_sr = sf.read(str(audio_path), dtype="float32")
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        audio = _resample(audio, int(src_sr))
        duration_s = len(audio) / TARGET_SR

        inputs = processor(audio, sampling_rate=TARGET_SR, return_tensors="pt")
        max_new_tokens = int(duration_s * 6.5) + 4

        t0 = time.perf_counter()
        with torch.no_grad():
            ids = model.generate(**inputs, max_new_tokens=max_new_tokens, no_repeat_ngram_size=3)
        latency = time.perf_counter() - t0
        hypothesis = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()

        # Discard warm-up clips from latency stats (first runs include lazy init).
        if i >= warmup:
            latencies.append(latency)

        rates = utterance_error_rates(entry["text"], hypothesis)
        pairs.append((entry["text"], hypothesis))
        per_utterance.append(
            {
                "audio": entry["audio"],
                "reference": entry["text"],
                "hypothesis": hypothesis,
                "wer": round(rates.wer, 4),
                "latency_s": round(latency, 3),
            }
        )

    if not latencies:
        return _not_measured(
            "no clips measured (after warm-up) — corpus too small or audio missing"
        )

    agg = corpus_error_rates(pairs)
    latencies.sort()
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[p95_idx]

    return {
        "measured": True,
        "model": model_label,
        "runtime": runtime,
        "n_measured": len(latencies),
        "warmup_skipped": warmup,
        "wer": round(agg.wer, 4),
        "cer": round(agg.cer, 4),
        "latency_p50_s": round(p50, 3),
        "latency_p95_s": round(p95, 3),
        "latency_mean_s": round(statistics.mean(latencies), 3),
        "budget_s": E2E_BUDGET_S,
        "fits_budget": p95 < E2E_BUDGET_S,
        "per_utterance": per_utterance,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Moonshine-base-uk latency + WER on the STT corpus"
    )
    parser.add_argument("--manifest", default="training/evaluation/stt_corpus/manifest.jsonl")
    parser.add_argument("--warmup", type=int, default=3, help="Clips to skip from latency stats")
    parser.add_argument(
        "--onnx-dir",
        default=None,
        help="Run the optimised onnxruntime path from this exported ONNX dir "
        "(omit to use the plain PyTorch reference)",
    )
    parser.add_argument("--output", default="materials/evaluation_results")
    args = parser.parse_args()

    result = run(Path(args.manifest), warmup=args.warmup, onnx_dir=args.onnx_dir)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stt_moonshine.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))

    logger.info("Results written to %s", out_dir / "stt_moonshine.json")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "per_utterance"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
