"""Interactive recorder for the Ukrainian STT WER corpus.

Reads ``commands_uk.txt`` (one reference transcript per line), prompts you to
speak each line, records a 16 kHz mono WAV via ``sounddevice``, and appends a
``manifest.jsonl`` entry ``{"audio": "clip_NNN.wav", "text": "<reference>"}``
that ``stt_wer.py`` consumes directly.

Run **on the RPi 5 with its real microphone** so the corpus captures the actual
home acoustic path (mic + room), not a clean studio signal::

    uv run python -m training.evaluation.stt_corpus.record

Controls per clip: ENTER to start, ENTER again to stop. ``s`` skips a line,
``r`` re-records the previous one, ``q`` quits (progress is saved). Re-running
resumes after the clips already present in the manifest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SAMPLE_RATE = 16000
CORPUS_DIR = Path(__file__).resolve().parent
COMMANDS_FILE = CORPUS_DIR / "commands_uk.txt"
MANIFEST_FILE = CORPUS_DIR / "manifest.jsonl"


def _load_commands() -> list[str]:
    lines: list[str] = []
    for raw in COMMANDS_FILE.read_text().splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


def _already_recorded() -> int:
    """Number of manifest entries already on disk (resume point)."""
    if not MANIFEST_FILE.exists():
        return 0
    return sum(1 for line in MANIFEST_FILE.read_text().splitlines() if line.strip())


def _record_clip(out_path: Path) -> None:
    """Record from the default mic until the user presses ENTER again.

    Records at the input device's native sample rate (many mics only expose
    48 kHz, so forcing 16 kHz can raise "invalid sample rate" mid-session). The
    WER scorer resamples to 16 kHz via ffmpeg, so any rate is fine downstream.
    """
    import threading

    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    rate = int(sd.query_devices(kind="input")["default_samplerate"]) or SAMPLE_RATE
    frames: list[np.ndarray] = []
    stop = threading.Event()

    def _callback(indata: np.ndarray, _frames: int, _time: object, _status: object) -> None:
        frames.append(indata.copy())

    def _wait_for_enter() -> None:
        input()
        stop.set()

    print(f"  ⏺  recording @ {rate} Hz… press ENTER to stop")
    with sd.InputStream(samplerate=rate, channels=1, dtype="int16", callback=_callback):
        threading.Thread(target=_wait_for_enter, daemon=True).start()
        while not stop.is_set():
            sd.sleep(50)

    audio = np.concatenate(frames) if frames else np.zeros((1, 1), dtype="int16")
    sf.write(str(out_path), audio, rate, subtype="PCM_16")
    print(f"  ✔  saved {out_path.name} ({len(audio) / rate:.1f}s)")


def main() -> None:
    try:
        import sounddevice  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError:
        sys.exit(
            "Recording needs sounddevice + soundfile:\n"
            "  uv pip install sounddevice soundfile\n"
            "(PortAudio system lib required: `apt install libportaudio2` on the RPi)"
        )

    commands = _load_commands()
    start = _already_recorded()
    print(
        f"Corpus: {len(commands)} commands · {start} already recorded · resuming at #{start + 1}\n"
    )

    with MANIFEST_FILE.open("a", encoding="utf-8") as manifest:
        idx = start
        while idx < len(commands):
            text = commands[idx]
            print(f"[{idx + 1}/{len(commands)}] «{text}»")
            choice = input("  ENTER=record  s=skip  q=quit > ").strip().lower()
            if choice == "q":
                break
            if choice == "s":
                idx += 1
                continue

            clip_name = f"clip_{idx + 1:03d}.wav"
            _record_clip(CORPUS_DIR / clip_name)
            manifest.write(
                json.dumps({"audio": clip_name, "text": text}, ensure_ascii=False) + "\n"
            )
            manifest.flush()
            idx += 1

    print(f"\nDone. Manifest: {MANIFEST_FILE}")
    print("Run: uv run python -m training.evaluation.stt_wer")


if __name__ == "__main__":
    main()
