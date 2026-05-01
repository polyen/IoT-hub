# hub/edge/Dockerfile — stub (populated in T1.x)
FROM python:3.11-slim-bookworm

WORKDIR /app

# Non-root user
RUN useradd --create-home --shell /bin/bash appuser

COPY pyproject.toml ./
COPY hub/ hub/

RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache ".[dev]"

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

# Placeholder entrypoint — replaced in T1.x with real edge agent
CMD ["python", "-c", "import http.server; http.server.HTTPServer(('', 8000), http.server.BaseHTTPRequestHandler).serve_forever()"]
