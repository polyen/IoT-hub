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
RUN uv sync --no-dev --frozen --no-install-project

COPY hub/ hub/
RUN uv sync --no-dev --frozen

RUN useradd --create-home --no-log-init -r appuser
USER appuser

ENV WAKE_WORD_MODEL_PATH="" \
    MOONSHINE_MODEL="UsefulSensors/moonshine-tiny-uk" \
    FORCE_CPU_STT="false" \
    WHISPER_HEF_PATH="" \
    NPU_STRATEGY="whisper_waits" \
    MQTT_HOST="mosquitto" \
    MQTT_PORT="1883" \
    PIPER_MODEL="uk_UA-lada-x_low" \
    PIPER_MODEL_DIR="/home/appuser/.local/share/piper"

CMD ["python", "-m", "hub.edge.voice.pipeline"]
