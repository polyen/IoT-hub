#!/bin/sh
set -e
echo "[entrypoint] Running database migrations..."
alembic -c /app/hub/backend/alembic.ini upgrade head
echo "[entrypoint] Migrations done. Starting server..."
exec uvicorn hub.backend.main:app --host 0.0.0.0 --port 8000
