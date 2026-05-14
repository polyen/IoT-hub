# MLflow tracking server with psycopg2 baked in so the Postgres backend works
# out of the box. Upstream `ghcr.io/mlflow/mlflow` ships without psycopg2.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "mlflow==2.18.0" \
    "psycopg2-binary>=2.9.0"

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD curl -sf http://localhost:5001/health || exit 1

ENTRYPOINT ["mlflow"]
