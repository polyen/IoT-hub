#!/bin/sh
set -e

echo "[entrypoint] Waiting for postgres..."
python - <<'PYEOF'
import socket, time, sys
for i in range(30):
    try:
        socket.create_connection(("postgres", 5432), timeout=2).close()
        print("[entrypoint] postgres is ready", flush=True)
        sys.exit(0)
    except OSError as e:
        print(f"[entrypoint]   retry {i+1}/30: {e}", flush=True)
        time.sleep(2)
print("[entrypoint] ERROR: postgres never became ready", file=sys.stderr)
sys.exit(1)
PYEOF

echo "[entrypoint] Running database migrations..."
alembic -c /app/hub/backend/alembic.ini upgrade head
echo "[entrypoint] Migrations done. Starting server..."
exec uvicorn hub.backend.main:app --host 0.0.0.0 --port 8000
