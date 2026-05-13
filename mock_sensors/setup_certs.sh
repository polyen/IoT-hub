#!/usr/bin/env bash
# Fetch the CA cert from the running mosquitto container on the RPi,
# then generate a mock-sensors client cert signed by that CA.
# Run once before starting mock sensors against port 8883.
#
# Usage:
#   bash mock_sensors/setup_certs.sh
#   RPI_HOST=192.168.0.53 RPI_USER=vlad bash mock_sensors/setup_certs.sh
set -euo pipefail

RPI_HOST="${RPI_HOST:-192.168.0.53}"
RPI_USER="${RPI_USER:-vlad}"
CERTS_DIR="$(cd "$(dirname "$0")" && pwd)/certs"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

mkdir -p "${CERTS_DIR}"

# ── 1. Pull CA cert + key from the mosquitto container ──────────────────────
info "Fetching CA cert from ${RPI_USER}@${RPI_HOST} (mosquitto container)..."
ssh "${RPI_USER}@${RPI_HOST}" \
    "docker exec mosquitto cat /mosquitto/config/certs/ca.crt" \
    > "${CERTS_DIR}/ca.crt"

info "Fetching CA key..."
ssh "${RPI_USER}@${RPI_HOST}" \
    "docker exec mosquitto cat /mosquitto/config/certs/ca.key" \
    > "${CERTS_DIR}/ca.key"
chmod 600 "${CERTS_DIR}/ca.key"

# ── 2. Generate mock-sensors client cert ────────────────────────────────────
info "Generating mock-sensors client key + cert..."
openssl genrsa -out "${CERTS_DIR}/mock-sensors.key" 2048 2>/dev/null
openssl req -new \
    -key "${CERTS_DIR}/mock-sensors.key" \
    -out "${CERTS_DIR}/mock-sensors.csr" \
    -subj "/O=IoT Hub/CN=mock-sensors"

printf "extendedKeyUsage=clientAuth\n" > /tmp/mock-ext.cnf
openssl x509 -req -days 365 \
    -in  "${CERTS_DIR}/mock-sensors.csr" \
    -CA  "${CERTS_DIR}/ca.crt" \
    -CAkey "${CERTS_DIR}/ca.key" \
    -CAcreateserial \
    -out "${CERTS_DIR}/mock-sensors.crt" \
    -extfile /tmp/mock-ext.cnf
rm "${CERTS_DIR}/mock-sensors.csr" /tmp/mock-ext.cnf

chmod 600 "${CERTS_DIR}/mock-sensors.key"
chmod 644 "${CERTS_DIR}/mock-sensors.crt" "${CERTS_DIR}/ca.crt"

info "Done. Certs in ${CERTS_DIR}:"
ls -lh "${CERTS_DIR}"
warn "Remember: ca.key is a secret — never commit it."
