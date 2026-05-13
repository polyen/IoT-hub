#!/usr/bin/env bash
# edge-updater.sh — pull latest main from GitHub and restart changed containers.
# Runs as the project user via systemd timer (iot-hub-updater.timer).
set -euo pipefail

REPO_DIR="/mnt/ssd/iot-hub/repo"
COMPOSE_FILE="${REPO_DIR}/hub/docker-compose.edge.yml"
ENV_FILE="${REPO_DIR}/.env"
BRANCH="main"

ts() { date -Iseconds; }

cd "${REPO_DIR}"

git fetch origin "${BRANCH}" --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/${BRANCH}")

if [[ "${LOCAL}" == "${REMOTE}" ]]; then
    exit 0
fi

echo "[$(ts)] Update detected: ${LOCAL:0:7} → ${REMOTE:0:7}"
git log --oneline "${LOCAL}..${REMOTE}"

git pull --ff-only origin "${BRANCH}"

echo "[$(ts)] Rebuilding changed images..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" build

echo "[$(ts)] Restarting containers..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --remove-orphans

echo "[$(ts)] Update complete."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" ps --format "table {{.Name}}\t{{.Status}}"
