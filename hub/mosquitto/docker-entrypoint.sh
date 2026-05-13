#!/usr/bin/env bash
# Generate self-signed broker certs on first start if none exist.
# Production deployments should replace these with certs from gen-mqtt-certs.sh.
set -euo pipefail

CERTS=/mosquitto/config/certs

if [[ ! -f "${CERTS}/broker.crt" ]]; then
    echo "[entrypoint] No TLS certs found — generating self-signed certs..."
    mkdir -p "${CERTS}"

    openssl genrsa -out "${CERTS}/ca.key" 4096 2>/dev/null
    openssl req -new -x509 -days 3650 \
        -key "${CERTS}/ca.key" \
        -out "${CERTS}/ca.crt" \
        -subj "/O=IoT Hub/CN=IoT-Hub-CA"

    openssl genrsa -out "${CERTS}/broker.key" 2048 2>/dev/null
    openssl req -new \
        -key "${CERTS}/broker.key" \
        -out /tmp/broker.csr \
        -subj "/O=IoT Hub/CN=mosquitto"
    printf "extendedKeyUsage=serverAuth\n" > /tmp/broker-ext.cnf
    openssl x509 -req -days 825 \
        -in /tmp/broker.csr \
        -CA "${CERTS}/ca.crt" \
        -CAkey "${CERTS}/ca.key" \
        -CAcreateserial \
        -out "${CERTS}/broker.crt" \
        -extfile /tmp/broker-ext.cnf
    rm /tmp/broker.csr /tmp/broker-ext.cnf

    chmod 600 "${CERTS}"/*.key
    chmod 644 "${CERTS}"/*.crt
    chown -R mosquitto:mosquitto "${CERTS}"

    echo "[entrypoint] Self-signed certs ready. Replace with gen-mqtt-certs.sh for production."
fi

# Fix acl.conf permissions to suppress mosquitto warnings
ACL=/mosquitto/config/acl.conf
if [[ -f "${ACL}" ]]; then
    chown mosquitto:mosquitto "${ACL}"
    chmod 0700 "${ACL}"
fi

exec "$@"
