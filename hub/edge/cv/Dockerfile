FROM python:3.13-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxext6 libxrender-dev libgl1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

COPY hub/ hub/
RUN uv sync --no-dev --frozen

# Use the uv-managed venv as default python — otherwise CMD picks system
# python and ModuleNotFoundError fires for aiomqtt / fastapi / etc.
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --no-log-init -r appuser
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/metrics')" || exit 1

ENV RTSP_URL="" \
    HEF_PATH="/app/models/current_yolo.hef" \
    POSE_HEF_PATH="/app/models/current_pose.hef" \
    MQTT_HOST="mosquitto" \
    MQTT_PORT="1883" \
    ROOM="living_room" \
    TARGET_FPS="15"

CMD ["python", "-m", "hub.edge.cv.pipeline"]
