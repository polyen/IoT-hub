"""Lazy download + cache for Hailo Whisper HEFs and decoder tokenization assets.

Source: official Hailo S3 (referenced from hailo-ai/hailo-apps resources_config.yaml).
All artifacts are stable as of 2025-08-20 and re-downloadable by URL.

We bind to Hailo-8 + ``tiny`` (10 s multilingual window) and ``base`` (5 s
multilingual window). H8L / H10H targets are listed for reference but unused.
"""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_S3 = "https://hailo-csdata.s3.eu-west-2.amazonaws.com/resources"

# Variant → (encoder URL, decoder URL, encoder window seconds)
_HEF_URLS: dict[str, tuple[str, str, int]] = {
    "tiny": (
        f"{_S3}/whisper/h8/tiny-whisper-encoder-10s_15dB.hef",
        f"{_S3}/whisper/h8/tiny-whisper-decoder-fixed-sequence-matmul-split.hef",
        10,
    ),
    "base": (
        f"{_S3}/whisper/h8/base-whisper-encoder-5s.hef",
        f"{_S3}/whisper/h8/base-whisper-decoder-fixed-sequence-matmul-split.hef",
        5,
    ),
}

# Decoder embedding assets are kept on host (operator removed during HEF compile).
_NPY_URLS: dict[str, tuple[str, str]] = {
    "tiny": (
        f"{_S3}/npy%20files/whisper/decoder_assets/tiny/decoder_tokenization/token_embedding_weight_tiny.npy",
        f"{_S3}/npy%20files/whisper/decoder_assets/tiny/decoder_tokenization/onnx_add_input_tiny.npy",
    ),
    "base": (
        f"{_S3}/npy%20files/whisper/decoder_assets/base/decoder_tokenization/token_embedding_weight_base.npy",
        f"{_S3}/npy%20files/whisper/decoder_assets/base/decoder_tokenization/onnx_add_input_base.npy",
    ),
}

# Mel filterbank (n_mels=80) for the host-side log-mel preprocessing — bundled
# in hailocs/hailo-whisper (MIT) and hailo-ai/hailo-apps. 4 KB, same file in both.
_MEL_FILTERS_URL = (
    "https://raw.githubusercontent.com/hailocs/hailo-whisper/main/common/assets/mel_filters.npz"
)


@dataclass(frozen=True)
class WhisperAssets:
    variant: str
    encoder_hef: Path
    decoder_hef: Path
    token_embedding_npy: Path
    onnx_add_input_npy: Path
    mel_filters_npz: Path
    chunk_seconds: int


def _download(url: str, dest: Path) -> None:
    """Atomic download: stream to .part then rename. Skip if already present."""
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    logger.info("Downloading %s → %s", url, dest)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as out:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
        tmp.rename(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def ensure_assets(variant: str, cache_dir: Path) -> WhisperAssets:
    """Return paths for all Hailo Whisper assets; download anything missing.

    Idempotent: subsequent calls just return the resolved paths.
    """
    if variant not in _HEF_URLS:
        raise ValueError(f"Unsupported Whisper variant {variant!r} (expected tiny|base)")

    enc_url, dec_url, secs = _HEF_URLS[variant]
    tok_url, add_url = _NPY_URLS[variant]
    cache_dir = cache_dir.expanduser().resolve()

    enc = cache_dir / "hef" / Path(enc_url).name
    dec = cache_dir / "hef" / Path(dec_url).name
    tok = cache_dir / "npy" / f"token_embedding_weight_{variant}.npy"
    add = cache_dir / "npy" / f"onnx_add_input_{variant}.npy"
    mel = cache_dir / "mel" / "mel_filters.npz"

    _download(enc_url, enc)
    _download(dec_url, dec)
    _download(tok_url, tok)
    _download(add_url, add)
    _download(_MEL_FILTERS_URL, mel)

    return WhisperAssets(
        variant=variant,
        encoder_hef=enc,
        decoder_hef=dec,
        token_embedding_npy=tok,
        onnx_add_input_npy=add,
        mel_filters_npz=mel,
        chunk_seconds=secs,
    )
