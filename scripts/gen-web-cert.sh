#!/usr/bin/env bash
# Generate a mkcert-signed TLS certificate for the IoT hub web UI.
#
# mkcert creates a local CA whose root certificate needs to be trusted
# ONCE per device. This script:
#   1. Installs mkcert + qrencode if missing.
#   2. Creates the local CA (idempotent).
#   3. Issues a cert covering HUB_DOMAIN + all local IPv4 addresses.
#   4. Serves rootCA.pem on a temporary HTTP server and prints a QR code
#      so any phone/tablet can scan it and install the CA in one tap.
#
# Usage:
#   sudo ./scripts/gen-web-cert.sh                  # defaults
#   HUB_DOMAIN=hub.lan sudo ./scripts/gen-web-cert.sh

set -euo pipefail

DOMAIN="${HUB_DOMAIN:-iot-hub.local}"
CERT_DIR="${CERTS_HOST_DIR:-/opt/iot-hub/certs}"
QR_PORT="${CA_SERVE_PORT:-8888}"

# ---------------------------------------------------------------------------
# 1. Ensure dependencies
# ---------------------------------------------------------------------------
install_if_missing() {
    local cmd="$1" pkg="$2"
    if ! command -v "$cmd" &>/dev/null; then
        echo "Installing ${pkg}..."
        if command -v apt-get &>/dev/null; then
            apt-get install -y "$pkg"
        elif command -v brew &>/dev/null; then
            brew install "$pkg"
        else
            echo "ERROR: cannot install ${pkg} automatically. Install it manually." >&2
            exit 1
        fi
    fi
}

install_if_missing mkcert mkcert
install_if_missing qrencode qrencode
install_if_missing python3 python3

# ---------------------------------------------------------------------------
# 2. Create / reuse the local CA
# ---------------------------------------------------------------------------
CAROOT="$(mkcert -CAROOT)"
export CAROOT
mkcert -install   # idempotent — skips if CA already exists

# ---------------------------------------------------------------------------
# 3. Collect local IPv4 addresses
# ---------------------------------------------------------------------------
HOSTS=("$DOMAIN" "localhost")
while IFS= read -r ip; do
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && HOSTS+=("$ip")
done < <(hostname -I 2>/dev/null | tr ' ' '\n')

# Pick the first non-loopback IP for the QR URL
LOCAL_IP=""
for h in "${HOSTS[@]}"; do
    [[ "$h" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && [[ "$h" != "127."* ]] && { LOCAL_IP="$h"; break; }
done
LOCAL_IP="${LOCAL_IP:-127.0.0.1}"

echo "Issuing certificate for: ${HOSTS[*]}"

# ---------------------------------------------------------------------------
# 4. Issue the certificate
# ---------------------------------------------------------------------------
mkdir -p "$CERT_DIR"

mkcert \
    -cert-file "${CERT_DIR}/hub.crt" \
    -key-file  "${CERT_DIR}/hub.key" \
    "${HOSTS[@]}"

chmod 600 "${CERT_DIR}/hub.key"
chmod 644 "${CERT_DIR}/hub.crt"

# Copy rootCA so we can serve it by a friendly filename
cp "${CAROOT}/rootCA.pem" "${CERT_DIR}/rootCA.pem"
chmod 644 "${CERT_DIR}/rootCA.pem"

echo ""
echo "Certificate written to ${CERT_DIR}/"

# ---------------------------------------------------------------------------
# 5. Serve rootCA.pem + show QR code
# ---------------------------------------------------------------------------
CA_URL="http://${LOCAL_IP}:${QR_PORT}/rootCA.pem"

echo ""
echo "Starting temporary CA server on ${CA_URL} ..."
echo "Press Ctrl-C or Enter when all devices have installed the CA."
echo ""

# Serve only the rootCA.pem file (minimal attack surface)
SERVE_DIR="$(mktemp -d)"
cp "${CERT_DIR}/rootCA.pem" "${SERVE_DIR}/rootCA.pem"

# Start HTTP server in background
python3 -m http.server "$QR_PORT" --directory "$SERVE_DIR" &>/dev/null &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null; rm -rf "$SERVE_DIR"' EXIT INT TERM

# Print QR code to terminal
echo "Scan this QR code on each device to install the CA certificate:"
echo ""
qrencode -t ANSIUTF8 "$CA_URL"
echo ""
echo "URL: ${CA_URL}"
echo ""

# ---------------------------------------------------------------------------
# 6. Platform instructions
# ---------------------------------------------------------------------------
cat <<'INSTRUCTIONS'
After opening the URL on each device:

  iOS / iPadOS  : tap "Allow" → Settings → General → VPN & Device Mgmt
                  → IoT Hub CA → Install → Settings → General → About
                  → Certificate Trust Settings → enable IoT Hub CA

  Android       : Chrome will ask to install — choose "CA Certificate"

  macOS         : open the URL in Safari → double-click the downloaded file
                  → Keychain → "Always Trust"

  Windows       : open the URL → run the .pem file → install into
                  "Trusted Root Certification Authorities"

  Linux (Debian): sudo cp rootCA.pem /usr/local/share/ca-certificates/iot-hub.crt
                  && sudo update-ca-certificates

INSTRUCTIONS

read -r -p "Press Enter once devices are done, to stop the CA server... "

echo ""
echo "CA server stopped."
echo ""
echo "Restart the web container to apply the new certificate:"
echo "  docker compose -f hub/docker-compose.edge.yml restart web"
