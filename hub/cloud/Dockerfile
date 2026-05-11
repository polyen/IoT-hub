FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project
COPY hub/ hub/
RUN uv sync --no-dev --frozen
RUN useradd --create-home --no-log-init -r appuser
USER appuser
ENV TELEGRAM_TOKEN="" TELEGRAM_WEBHOOK_URL="" EDGE_API_URL="http://backend:8000" \
    MQTT_HOST="mosquitto" MQTT_PORT="8883"
CMD ["python", "-m", "hub.cloud.telegram_bot"]
