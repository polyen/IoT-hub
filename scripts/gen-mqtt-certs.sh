#!/usr/bin/env bash
# Generate self-signed CA + broker + per-device TLS certificates for mTLS MQTT.
# Usage: bash gen-mqtt-certs.sh [--device DEVICE_NAME] [--out DIR]
# Default out: hub/mosquitto/certs/
set -euo pipefail

DEVICE=""
OUT_DIR="hub/mosquitto/certs"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --device) DEVICE="$2"; shift 2 ;;
        --out)    OUT_DIR="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

CA_DAYS=3650
CERT_DAYS=825
COUNTRY="UA"
ORG="IoT Hub"

mkdir -p "${OUT_DIR}"
cd "${OUT_DIR}"

# ── CA ──────────────────────────────────────────────────────────────────────
if [[ ! -f ca.key ]]; then
    echo "[INFO] Generating CA key + cert..."
    openssl genrsa -out ca.key 4096
    openssl req -new -x509 -days "${CA_DAYS}" -key ca.key -out ca.crt \
        -subj "/C=${COUNTRY}/O=${ORG}/CN=IoT-Hub-CA"
else
    echo "[INFO] CA already exists — skipping"
fi

# ── Broker cert ─────────────────────────────────────────────────────────────
if [[ ! -f broker.key ]]; then
    echo "[INFO] Generating broker cert..."
    openssl genrsa -out broker.key 2048
    openssl req -new -key broker.key -out broker.csr \
        -subj "/C=${COUNTRY}/O=${ORG}/CN=mosquitto"
    openssl x509 -req -in broker.csr -CA ca.crt -CAkey ca.key \
        -CAcreateserial -out broker.crt -days "${CERT_DAYS}" \
        -extfile <(echo "extendedKeyUsage=serverAuth")
    rm broker.csr
fi

# ── Per-device / per-service cert ───────────────────────────────────────────
if [[ -n "${DEVICE}" ]]; then
    if [[ ! -f "${DEVICE}.key" ]]; then
        echo "[INFO] Generating cert for device: ${DEVICE}"
        openssl genrsa -out "${DEVICE}.key" 2048
        openssl req -new -key "${DEVICE}.key" -out "${DEVICE}.csr" \
            -subj "/C=${COUNTRY}/O=${ORG}/CN=${DEVICE}"
        openssl x509 -req -in "${DEVICE}.csr" -CA ca.crt -CAkey ca.key \
            -CAcreateserial -out "${DEVICE}.crt" -days "${CERT_DAYS}" \
            -extfile <(echo "extendedKeyUsage=clientAuth")
        rm "${DEVICE}.csr"
        echo "[INFO] Done: ${DEVICE}.key + ${DEVICE}.crt"
    else
        echo "[INFO] Cert for ${DEVICE} already exists — skipping"
    fi
fi

chmod 600 ./*.key 2>/dev/null || true
chmod 644 ./*.crt 2>/dev/null || true

echo ""
echo "[DONE] Certs in: $(pwd)"
echo "  CA:     ca.crt"
echo "  Broker: broker.crt + broker.key"
[[ -n "${DEVICE}" ]] && echo "  Device: ${DEVICE}.crt + ${DEVICE}.key"
echo ""
echo "Recovery: CA key (ca.key) is the recovery secret — store it offline."
