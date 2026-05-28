"""Host-side audio preprocessing for the Hailo Whisper encoder.

Adapted from hailocs/hailo-whisper (MIT) common/audio_utils_numpy.py — torch
dependency removed in favour of scipy.signal. The mel-filterbank is loaded
lazily from the cached mel_filters.npz produced by Hailo (same file format
as OpenAI Whisper's bundled filters).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import signal  # type: ignore[import-untyped]

SAMPLE_RATE = 16000
N_FFT = 400
HOP_LENGTH = 160
N_MELS = 80


@lru_cache(maxsize=4)
def _mel_filters(path: str) -> np.ndarray:
    with np.load(Path(path), allow_pickle=False) as f:
        return np.asarray(f[f"mel_{N_MELS}"])


def _log_mel(audio: np.ndarray, mel_filters_path: str) -> np.ndarray:
    """log-Mel spectrogram matching the OpenAI Whisper definition.

    Matches torch.stft(..., center=True) — reflection-pad N_FFT//2 on both
    sides before STFT so 10 s @ 16 kHz produces exactly 1000 mel frames
    (hailocs/hailo-whisper's bare scipy.stft produces 997 — too few for the
    Hailo encoder HEF which is compiled for the torch-aligned frame count).
    """
    pad = N_FFT // 2
    padded = np.pad(audio, (pad, pad), mode="reflect")
    window = signal.windows.hann(N_FFT, sym=False)
    _f, _t, stft = signal.stft(
        padded,
        fs=SAMPLE_RATE,
        window=window,
        nperseg=N_FFT,
        noverlap=N_FFT - HOP_LENGTH,
        boundary=None,
        padded=False,
    )
    magnitudes = np.abs(stft[..., :-1]) ** 2
    mel_spec = _mel_filters(mel_filters_path) @ magnitudes
    log_spec = np.log10(np.maximum(mel_spec, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return np.asarray((log_spec + 4.0) / 4.0, dtype=np.float32)


def _pad_or_trim(audio: np.ndarray, samples: int) -> np.ndarray:
    if audio.shape[-1] > samples:
        return audio[..., :samples]
    if audio.shape[-1] < samples:
        return np.pad(audio, (0, samples - audio.shape[-1]))
    return audio


def _peak_normalise(audio: np.ndarray, target: float = 0.9) -> np.ndarray:
    """Whisper expects properly-leveled audio; quiet input → decoder hallucinates.

    Reference applies this in hailo-apps speech_recognition (improve_audio).
    """
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1e-6:
        return np.asarray(audio * (target / peak), dtype=np.float32)
    return audio


def audio_to_mel(
    audio_f32: np.ndarray,
    chunk_seconds: int,
    mel_filters_path: str,
) -> np.ndarray:
    """Turn one int16-normalised waveform into a single encoder-ready mel tensor.

    Returns shape (1, 1, n_frames, n_mels) — NHWC layout the Hailo HEF expects.
    Audio is peak-normalised and padded/trimmed to chunk_seconds * 16 kHz samples.
    """
    audio_f32 = _peak_normalise(audio_f32)
    audio_f32 = _pad_or_trim(audio_f32, chunk_seconds * SAMPLE_RATE)
    mel = _log_mel(audio_f32, mel_filters_path)  # (n_mels, n_frames)
    mel = mel[np.newaxis, :, :, np.newaxis]  # (1, n_mels, n_frames, 1)
    return np.ascontiguousarray(np.transpose(mel, (0, 3, 2, 1)))  # NHWC
