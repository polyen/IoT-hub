FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    portaudio19-dev libsndfile1 \
    ffmpeg \
    espeak-ng espeak-ng-data \
    wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install piper-tts binary for ARM64 / amd64
RUN ARCH=$(dpkg --print-architecture) && \
    case "$ARCH" in \
      arm64) PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz" ;; \
      amd64) PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz" ;; \
      *) echo "Unsupported arch $ARCH — skipping piper install" && exit 0 ;; \
    esac && \
    wget -qO /tmp/piper.tar.gz "$PIPER_URL" && \
    tar -xzf /tmp/piper.tar.gz -C /usr/local/bin --strip-components=1 && \
    rm /tmp/piper.tar.gz

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra voice --no-install-project

COPY hub/ hub/
RUN uv sync --frozen --no-dev --extra voice

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --no-log-init -r appuser

# Create HF cache dir owned by appuser so the named volume inherits correct
# permissions on first mount (Docker copies image dir perms into a new volume).
RUN mkdir -p /app/.hf_cache && chown appuser:appuser /app/.hf_cache

# Pre-bake silero-vad ONNX into the image — the model is bundled in the wheel
# so no network access is needed at container startup and no torch is required.
RUN pip download --no-deps "silero-vad==6.2.1" -d /tmp/sv-dl && \
    python3 -c "import zipfile,pathlib; whl=next(pathlib.Path('/tmp/sv-dl').glob('*.whl')); dest=pathlib.Path('/app/.silero_vad/silero_vad.onnx'); dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(zipfile.ZipFile(whl).read('silero_vad/data/silero_vad.onnx'))" && \
    chown -R appuser:appuser /app/.silero_vad && \
    rm -rf /tmp/sv-dl

USER appuser

ENV WAKE_WORD_MODEL_PATH="" \
    MOONSHINE_MODEL="" \
    FASTER_WHISPER_MODEL="base" \
    FORCE_CPU_STT="false" \
    WHISPER_HEF_PATH="" \
    NPU_STRATEGY="whisper_waits" \
    MQTT_HOST="mosquitto" \
    MQTT_PORT="1883" \
    PIPER_MODEL="uk_UA-lada-x_low" \
    PIPER_MODEL_DIR="/home/appuser/.local/share/piper" \
    SILERO_VAD_ONNX_PATH="/app/.silero_vad/silero_vad.onnx" \
    HF_HOME="/app/.hf_cache"

CMD ["python", "-m", "hub.edge.voice.pipeline"]
