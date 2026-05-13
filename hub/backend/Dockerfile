# Stage 1: build virtualenv
FROM python:3.11-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

COPY hub/ hub/
RUN uv sync --no-dev --frozen

# Stage 2: runtime image
FROM python:3.11-slim AS runtime

WORKDIR /app

RUN useradd --create-home --shell /bin/bash appuser && \
    apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY hub/ hub/
RUN chmod +x /app/hub/backend/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:8000/health/live || exit 1

ENTRYPOINT ["/app/hub/backend/entrypoint.sh"]
