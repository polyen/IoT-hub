"""Latency / RTF / TTFB benchmark for the voice TTS stage (piper → espeak-ng).

Times the **production synthesis path** the user hears at the end of a voice
turn: ``hub.edge.voice.tts.synthesize(text)`` — piper-tts (UA model
``uk_UA-lada-x_low``) rendering to WAV, then ffmpeg resampling to the 16 kHz
int16 PCM the playback code expects. espeak-ng is the documented fallback.

TTS sits **off** the NFR-2 5 s critical path (it runs after the LLM, which is
itself off-path — §4.2.3), so the interesting question is not a hard budget but
whether synthesis keeps up with playback: ``RTF = synth_time / audio_duration``.
``RTF < 1`` means the hub can render faster than real time, i.e. it never
starves the speaker. We report that as the pass criterion.

Three metrics per phrase:
  * **latency_s** — wall-clock of the full ``synthesize()`` call (incl. ffmpeg).
  * **rtf**       — ``latency_s / audio_duration_s``.
  * **ttfb_s**    — time-to-first-audio from piper's raw streaming mode
                    (``--output_raw``). For the single-sentence commands the hub
                    actually speaks, TTFB ≈ latency (piper emits per sentence);
                    it only diverges on multi-sentence notifications. espeak has
                    no streaming path, so ttfb is ``null`` there.

Hardware-bound — **run on the RPi 5**, ideally twice (idle and with
``iot-hub-cv`` active) to capture contended synthesis latency, same protocol as
the STT and LLM measurements (§4.3).

Honesty contract: no TTS engine / no model / empty corpus →
``{"measured": false, ...}`` with a note; never a fabricated number.
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

logger = logging.getLogger(__name__)

# 16 kHz int16 mono — the format synthesize() returns (audio_io.SAMPLE_RATE).
_SAMPLE_RATE = 16_000
_BYTES_PER_SAMPLE = 2

# RTF below this means synthesis keeps ahead of playback (real-time capable).
REALTIME_RTF = 1.0


def _not_measured(note: str) -> dict[str, Any]:
    return {"measured": False, "pass": None, "note": note}


def _load_phrases(path: Path) -> list[str]:
    phrases: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        phrases.append(line)
    return phrases


def _percentiles(values: list[float]) -> dict[str, float]:
    s = sorted(values)
    p95_idx = max(0, int(len(s) * 0.95) - 1)
    return {
        "p50": round(s[len(s) // 2], 3),
        "p95": round(s[p95_idx], 3),
        "mean": round(statistics.mean(values), 3),
    }


async def _piper_ttfb(text: str, model_name: str) -> float | None:
    """Time-to-first-audio using piper's raw streaming mode, or None on failure.

    Reuses the production model resolution from ``tts.py`` so the model file is
    the exact one the hub speaks with. Returns the wall-clock from feeding text
    to the first PCM byte appearing on stdout.
    """
    from hub.edge.voice.tts import _model_files, _piper_bin

    onnx, cfg = _model_files(model_name)
    if not (onnx.exists() and cfg.exists()):
        return None
    cmd = [_piper_bin(), "--model", str(onnx), "--config", str(cfg), "--output_raw"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdin is not None and proc.stdout is not None
        t0 = time.perf_counter()
        proc.stdin.write(text.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        first = await asyncio.wait_for(proc.stdout.read(4096), timeout=30)
        ttfb = time.perf_counter() - t0
        if not first:
            return None
        # Drain the rest so piper exits cleanly and the pipe is not left full.
        await proc.stdout.read()
        await proc.wait()
        return ttfb
    except Exception as exc:  # streaming is best-effort — never break the run
        logger.warning("piper TTFB probe failed: %s", exc)
        return None


async def _resolve_engine() -> tuple[str, str | None] | None:
    """Return (engine, model_name) for the path synthesize() will actually take.

    Triggers the one-time piper model download (same code as production) so the
    first warm-up clip is not penalised by a network fetch inside the timed loop.
    """
    from hub.edge.voice import tts

    if tts._piper_available():
        model_name = tts._DEFAULT_MODEL
        import os

        model_name = os.environ.get("PIPER_MODEL", model_name)
        onnx, cfg = tts._model_files(model_name)
        if (onnx.exists() and cfg.exists()) or tts._download_model(model_name):
            return "piper", model_name
        logger.warning("piper present but model %s unavailable — will use espeak", model_name)
    if tts._espeak_available():
        return "espeak", None
    return None


async def _run(phrases: list[str], *, warmup: int) -> dict[str, Any]:
    try:
        from hub.edge.voice.tts import synthesize
    except ImportError as exc:
        return _not_measured(f"hub.edge.voice.tts unavailable ({exc})")

    engine = await _resolve_engine()
    if engine is None:
        return _not_measured("no TTS engine available (install piper or espeak-ng)")
    engine_name, model_name = engine

    latencies: list[float] = []
    rtfs: list[float] = []
    ttfbs: list[float] = []
    per_utterance: list[dict[str, Any]] = []

    for i, text in enumerate(phrases):
        t0 = time.perf_counter()
        pcm = await synthesize(text, model_name=model_name)
        latency = time.perf_counter() - t0

        duration_s = len(pcm) / (_SAMPLE_RATE * _BYTES_PER_SAMPLE)
        rtf = latency / duration_s if duration_s > 0 else float("nan")

        ttfb = (
            await _piper_ttfb(text, model_name)
            if engine_name == "piper" and model_name is not None
            else None
        )

        warm = i < warmup
        if not warm and duration_s > 0:
            latencies.append(latency)
            rtfs.append(rtf)
            if ttfb is not None:
                ttfbs.append(ttfb)

        per_utterance.append(
            {
                "text": text,
                "n_chars": len(text),
                "n_words": len(text.split()),
                "audio_duration_s": round(duration_s, 3),
                "latency_s": round(latency, 3),
                "rtf": round(rtf, 3),
                "ttfb_s": round(ttfb, 3) if ttfb is not None else None,
                "warmup": warm,
            }
        )

    if not latencies:
        return _not_measured(
            "no phrases measured after warm-up — corpus too small or synthesis produced no audio"
        )

    lat = _percentiles(latencies)
    rtf_stats = _percentiles(rtfs)
    result: dict[str, Any] = {
        "measured": True,
        "engine": engine_name,
        "model": model_name or "espeak-ng (uk)",
        "sample_rate_hz": _SAMPLE_RATE,
        "n_measured": len(latencies),
        "warmup_skipped": warmup,
        "latency_s": lat,
        "rtf": rtf_stats,
        "realtime_rtf": REALTIME_RTF,
        # Real-time capable iff worst-case (p95) synthesis still outruns playback.
        "pass": rtf_stats["p95"] < REALTIME_RTF,
        "ttfb_s": _percentiles(ttfbs) if ttfbs else None,
        "per_utterance": per_utterance,
    }
    return result


def run(phrases_path: Path, *, warmup: int = 2) -> dict[str, Any]:
    if not phrases_path.exists():
        return _not_measured(f"phrases corpus not found at {phrases_path}")
    phrases = _load_phrases(phrases_path)
    if not phrases:
        return _not_measured(f"phrases corpus {phrases_path} is empty")
    return asyncio.run(_run(phrases, warmup=warmup))


def _to_markdown(result: dict[str, Any]) -> str:
    lines = ["# TTS Synthesis Latency", ""]
    if not result.get("measured"):
        return "\n".join(
            lines + ["**Status:** ⚠️ not measured", "", f"_{result.get('note', '')}_", ""]
        )
    passed = "✓" if result.get("pass") else "✗"
    lat, rtf = result["latency_s"], result["rtf"]
    lines += [
        f"**Engine:** `{result['engine']}` (`{result['model']}`) · "
        f"{result['n_measured']} phrases · {result['sample_rate_hz']} Hz",
        "",
        "| Metric | p50 | p95 | mean |",
        "|--------|-----|-----|------|",
        f"| Latency (s) | {lat['p50']} | {lat['p95']} | {lat['mean']} |",
        f"| RTF | {rtf['p50']} | {rtf['p95']} | {rtf['mean']} |",
    ]
    ttfb = result.get("ttfb_s")
    if ttfb:
        lines.append(f"| TTFB (s) | {ttfb['p50']} | {ttfb['p95']} | {ttfb['mean']} |")
    else:
        lines.append("| TTFB (s) | _n/a (espeak / no streaming)_ | | |")
    lines += [
        "",
        f"Real-time capable (RTF p95 < {result['realtime_rtf']}): {passed}",
        "",
        "_TTS is off the NFR-2 critical path; RTF < 1 is a throughput sanity check, not a budget._",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="TTS synthesis latency / RTF / TTFB benchmark")
    parser.add_argument("--phrases", default="training/evaluation/tts_corpus/phrases.txt")
    parser.add_argument("--warmup", type=int, default=2, help="Phrases to skip from stats")
    parser.add_argument("--output", default="materials/evaluation_results")
    args = parser.parse_args()

    result = run(Path(args.phrases), warmup=args.warmup)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tts_latency.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (out_dir / "tts_latency.md").write_text(_to_markdown(result))

    logger.info("Results written to %s", out_dir / "tts_latency.json")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "per_utterance"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
