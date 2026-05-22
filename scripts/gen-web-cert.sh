#!/usr/bin/env bash
# Generate a self-signed TLS certificate for the IoT hub web UI.
#
# The certificate is valid for HUB_DOMAIN (default: iot-hub.local) and all
# local IPv4 addresses found on the machine.  Import hub.crt into your
# browser / OS trust store to avoid the security warning.
#
# Usage:
#   ./scripts/gen-web-cert.sh                  # defaults
#   HUB_DOMAIN=hub.lan ./scripts/gen-web-cert.sh

set -euo pipefail

DOMAIN="${HUB_DOMAIN:-iot-hub.local}"
CERT_DIR="${CERTS_HOST_DIR:-/opt/iot-hub/certs}"
DAYS=825   # ~2.25 years — macOS/iOS cap is 825 days for trusted certs

mkdir -p "$CERT_DIR"

# Build SAN list: DNS names + every local IPv4 address
SAN="DNS:${DOMAIN},DNS:localhost"
while IFS= read -r ip; do
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && SAN="${SAN},IP:${ip}"
done < <(hostname -I 2>/dev/null | tr ' ' '\n')

echo "Generating certificate for: ${SAN}"

openssl req -x509 \
    -newkey rsa:4096 \
    -keyout "${CERT_DIR}/hub.key" \
    -out    "${CERT_DIR}/hub.crt" \
    -days   "${DAYS}" \
    -nodes \
    -subj   "/CN=${DOMAIN}/O=IoT Hub/C=UA" \
    -addext "subjectAltName=${SAN}"

chmod 600 "${CERT_DIR}/hub.key"
chmod 644 "${CERT_DIR}/hub.crt"

echo ""
echo "Done. Files written to ${CERT_DIR}/"
echo ""
echo "To trust the certificate:"
echo "  macOS:   open '${CERT_DIR}/hub.crt'  → add to Keychain → set to Always Trust"
echo "  Linux:   sudo cp '${CERT_DIR}/hub.crt' /usr/local/share/ca-certificates/iot-hub.crt && sudo update-ca-certificates"
echo "  Android: Settings → Security → Install certificate"
echo ""
echo "Restart the web container after running this script:"
echo "  docker compose -f hub/docker-compose.edge.yml restart web"
