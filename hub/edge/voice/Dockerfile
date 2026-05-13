FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    portaudio19-dev libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

COPY hub/ hub/
RUN uv sync --no-dev --frozen

RUN useradd --create-home --no-log-init -r appuser
USER appuser

ENV WAKE_WORD_MODEL_PATH="" \
    FORCE_CPU_STT="false" \
    WHISPER_HEF_PATH="" \
    NPU_STRATEGY="whisper_waits" \
    MQTT_HOST="mosquitto" \
    MQTT_PORT="1883"

CMD ["python", "-m", "hub.edge.voice.pipeline"]
