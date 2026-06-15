FROM python:3.11-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

COPY hub/ hub/
RUN uv sync --no-dev --frozen

# Use the uv-managed venv as default python (otherwise CMD picks system python
# and ModuleNotFoundError fires for aiomqtt).
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --no-log-init -r appuser
USER appuser

ENV MQTT_HOST="mosquitto" \
    MQTT_PORT="1883" \
    Z2M_BASE_TOPIC="zigbee2mqtt"

CMD ["python", "-m", "hub.edge.zigbee.bridge"]
