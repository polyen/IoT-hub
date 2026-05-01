#!/usr/bin/env bash
# =============================================================================
# gen-mqtt-certs.sh — Generate self-signed CA + broker + client certificates
#                     for Mosquitto mTLS (T3.1)
#
# Usage:
#   ./scripts/gen-mqtt-certs.sh [--out-dir <path>]
#
# Default output: hub/mosquitto/certs/
#
# Generates:
#   ca.key / ca.crt           — Root CA (keep ca.key offline after generation)
#   broker.key / broker.crt   — Broker server cert (signed by CA)
#   bridge-client.key / .crt  — Bridge client cert (edge → VPS)
#   passwd                    — Mosquitto password file (hashed) with default accounts
#
# After running, copy certs to VPS broker and update mosquitto.conf mTLS section.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DAYS_CA=3650       # 10 years
DAYS_CERT=825      # ~2 years (Apple / browser limit)
KEY_BITS=4096
DIGEST="sha256"
COUNTRY="UA"
ORG="IoT-Hub"

OUT_DIR="hub/mosquitto/certs"

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --out-dir) OUT_DIR="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

mkdir -p "$OUT_DIR"

echo "[gen-mqtt-certs] Output directory: $OUT_DIR"

# ---------------------------------------------------------------------------
# 1. Root CA
# ---------------------------------------------------------------------------
if [[ -f "$OUT_DIR/ca.key" ]]; then
    echo "[gen-mqtt-certs] CA already exists — skipping CA generation."
else
    echo "[gen-mqtt-certs] Generating CA key and certificate..."
    openssl genrsa -out "$OUT_DIR/ca.key" "$KEY_BITS"
    openssl req -new -x509 \
        -days "$DAYS_CA" \
        -key "$OUT_DIR/ca.key" \
        -out "$OUT_DIR/ca.crt" \
        -"$DIGEST" \
        -subj "/C=${COUNTRY}/O=${ORG}/CN=${ORG}-CA"
    echo "[gen-mqtt-certs] CA generated: $OUT_DIR/ca.crt"
fi

# ---------------------------------------------------------------------------
# Helper: sign a CSR with the CA
# ---------------------------------------------------------------------------
sign_cert() {
    local name="$1"
    local cn="$2"
    local ext="$3"   # "server" | "client"

    local ext_conf
    if [[ "$ext" == "server" ]]; then
        ext_conf="extendedKeyUsage=serverAuth"
    else
        ext_conf="extendedKeyUsage=clientAuth"
    fi

    openssl genrsa -out "$OUT_DIR/${name}.key" 2048

    openssl req -new \
        -key "$OUT_DIR/${name}.key" \
        -out "$OUT_DIR/${name}.csr" \
        -"$DIGEST" \
        -subj "/C=${COUNTRY}/O=${ORG}/CN=${cn}"

    openssl x509 -req \
        -days "$DAYS_CERT" \
        -in "$OUT_DIR/${name}.csr" \
        -CA "$OUT_DIR/ca.crt" \
        -CAkey "$OUT_DIR/ca.key" \
        -CAcreateserial \
        -out "$OUT_DIR/${name}.crt" \
        -"$DIGEST" \
        -extfile <(echo "$ext_conf")

    rm "$OUT_DIR/${name}.csr"
    echo "[gen-mqtt-certs] Signed: $OUT_DIR/${name}.crt"
}

# ---------------------------------------------------------------------------
# 2. Broker server certificate
# ---------------------------------------------------------------------------
if [[ ! -f "$OUT_DIR/broker.crt" ]]; then
    echo "[gen-mqtt-certs] Generating broker certificate..."
    sign_cert "broker" "mosquitto-broker" "server"
fi

# ---------------------------------------------------------------------------
# 3. Bridge client certificate (edge → VPS)
# ---------------------------------------------------------------------------
if [[ ! -f "$OUT_DIR/bridge-client.crt" ]]; then
    echo "[gen-mqtt-certs] Generating bridge client certificate..."
    sign_cert "bridge-client" "iot-hub-bridge" "client"
fi

# ---------------------------------------------------------------------------
# 4. Password file (hashed) — default service accounts
# ---------------------------------------------------------------------------
PASSWD_FILE="hub/mosquitto/passwd"

if [[ ! -f "$PASSWD_FILE" ]]; then
    echo "[gen-mqtt-certs] Generating password file..."
    echo "WARNING: Default passwords are placeholders — change before production!"

    declare -A ACCOUNTS=(
        ["backend"]="change_me_backend_pw"
        ["cv-agent"]="change_me_cv_agent_pw"
        ["llm-agent"]="change_me_llm_agent_pw"
        ["monitoring"]="change_me_monitoring_pw"
        ["vps-bridge"]="change_me_bridge_pw"
    )

    touch "$PASSWD_FILE"
    for user in "${!ACCOUNTS[@]}"; do
        mosquitto_passwd -b "$PASSWD_FILE" "$user" "${ACCOUNTS[$user]}"
        echo "[gen-mqtt-certs]   Added user: $user"
    done
else
    echo "[gen-mqtt-certs] Password file already exists — skipping."
fi

# ---------------------------------------------------------------------------
# 5. Permissions
# ---------------------------------------------------------------------------
chmod 600 "$OUT_DIR"/*.key 2>/dev/null || true
chmod 644 "$OUT_DIR"/*.crt 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
cat << EOF

[gen-mqtt-certs] Done!

Next steps:
  1. Review and update passwords in: $PASSWD_FILE
     (mosquitto_passwd -b $PASSWD_FILE <user> <new_password>)
  2. Copy ca.crt to all MQTT clients (ESP32, cv-agent, llm-agent).
  3. Uncomment the mTLS listener in hub/mosquitto/mosquitto.conf.
  4. Uncomment bridge config in hub/mosquitto/conf.d/bridge.conf and set VPS_MQTT_HOST.
  5. Store ca.key securely offline — it is not needed at runtime.

Cert files: $OUT_DIR/
EOF
