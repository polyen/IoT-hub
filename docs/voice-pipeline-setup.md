# Voice Pipeline Setup

## Hailo Whisper (RPi5 + Hailo-8)

### Prerequisites

```bash
# Install HailoRT on RPi5 (see scripts/edge-bootstrap.sh)
sudo apt install hailort
pip install hailo_platform

# Download Hailo Whisper HEF
dvc pull models/versions/whisper_encoder.hef
```

### Install

```bash
uv sync
```

### Run

```bash
python -m hub.edge.voice.hailo_whisper --record audio.wav
```

## CPU Fallback (any machine)

```bash
pip install faster-whisper soundfile
python -m hub.edge.voice.hailo_whisper --record audio.wav --force-cpu
```

## Quick record + transcribe test

```bash
bash scripts/test-stt.sh
```

Requires `arecord` (ALSA). Records 5 s from default mic, prints transcript.

## Architecture

- Encoder runs on Hailo-8 NPU via `hailo_platform.HEF`
- Decoder runs on CPU (ARM Cortex-A76 on RPi5)
- Fallback: `faster-whisper distil-large-v3` fully on CPU with int8 quantization
- Both paths expose the same `STTBackend` protocol — swap transparently
