"""Text-to-speech synthesis — piper-tts primary, espeak-ng fallback.

Returns raw 16 kHz int16 mono PCM bytes ready for audio_io play functions.
Ukrainian model is downloaded on first call and cached in ~/.local/share/piper.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Piper model for Ukrainian.  Override with PIPER_MODEL env var.
# Voice list: https://rhasspy.github.io/piper-samples/
_DEFAULT_MODEL = "uk_UA-lada-x_low"
_PIPER_MODEL_DIR = Path(os.environ.get("PIPER_MODEL_DIR", Path.home() / ".local/share/piper"))

# Output sample rate that piper will emit (must match audio_io SAMPLE_RATE=16000)
_SAMPLE_RATE = 16_000


def _model_files(model_name: str) -> tuple[Path, Path]:
    """Return (onnx_path, config_path) for a piper model name."""
    base = _PIPER_MODEL_DIR / model_name
    return base.with_suffix(".onnx"), base.with_suffix(".onnx.json")


def _download_model(model_name: str) -> bool:
    """Download piper model files if missing. Returns True on success."""
    onnx, cfg = _model_files(model_name)
    if onnx.exists() and cfg.exists():
        return True

    _PIPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    base_url = (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        f"uk/uk_UA/lada/x_low/{model_name}"
    )
    try:
        import urllib.request

        for suffix, path in [(".onnx", onnx), (".onnx.json", cfg)]:
            url = base_url + suffix
            logger.info("Downloading piper model: %s", url)
            urllib.request.urlretrieve(url, path)  # noqa: S310
        return True
    except Exception as exc:
        logger.warning("Failed to download piper model %s: %s", model_name, exc)
        for p in (onnx, cfg):
            p.unlink(missing_ok=True)
        return False


def _piper_available() -> bool:
    return shutil.which("piper") is not None or shutil.which("piper-tts") is not None


def _piper_bin() -> str:
    return shutil.which("piper") or shutil.which("piper-tts") or "piper"


def _espeak_available() -> bool:
    return shutil.which("espeak-ng") is not None or shutil.which("espeak") is not None


def _espeak_bin() -> str:
    return shutil.which("espeak-ng") or shutil.which("espeak") or "espeak-ng"


async def synthesize(text: str, model_name: str | None = None) -> bytes:
    """Return raw 16 kHz int16 mono PCM for *text* using piper or espeak-ng fallback."""
    model_name = model_name or os.environ.get("PIPER_MODEL", _DEFAULT_MODEL)

    if _piper_available():
        pcm = await _synth_piper(text, model_name)
        if pcm:
            return pcm
        logger.warning("piper synthesis failed — falling back to espeak-ng")

    if _espeak_available():
        return await _synth_espeak(text)

    raise RuntimeError("No TTS engine available (install piper or espeak-ng)")


async def _synth_piper(text: str, model_name: str) -> bytes | None:
    onnx, cfg = _model_files(model_name)
    if not (onnx.exists() and cfg.exists()):
        if not _download_model(model_name):
            return None

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name

    try:
        cmd = [
            _piper_bin(),
            "--model",
            str(onnx),
            "--config",
            str(cfg),
            "--output_file",
            wav_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            proc.communicate(text.encode()),
            timeout=30,
        )
        if proc.returncode != 0:
            logger.warning("piper error: %s", stderr.decode())
            return None

        return await _wav_to_pcm16k(wav_path)
    except Exception as exc:
        logger.warning("piper synthesis error: %s", exc)
        return None
    finally:
        Path(wav_path).unlink(missing_ok=True)


async def _synth_espeak(text: str) -> bytes:
    """Synthesize with espeak-ng → raw 16kHz int16 PCM via ffmpeg resampling."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name

    try:
        cmd = [
            _espeak_bin(),
            "-v",
            "uk",
            "-s",
            "150",  # words per minute
            "-w",
            wav_path,
            text,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode != 0:
            raise RuntimeError(f"espeak-ng failed: {stderr.decode()}")

        return await _wav_to_pcm16k(wav_path)
    finally:
        Path(wav_path).unlink(missing_ok=True)


async def _wav_to_pcm16k(wav_path: str) -> bytes:
    """Convert WAV file to 16 kHz int16 mono raw PCM via ffmpeg."""
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-i",
        wav_path,
        "-ar",
        str(_SAMPLE_RATE),
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    return stdout
