#!/usr/bin/env bash
# install-updater.sh — installs the iot-hub-updater systemd timer on the RPi.
# Run AFTER cloning the repo: sudo bash scripts/install-updater.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || error "Must be run as root (sudo)."

REAL_USER="${SUDO_USER:-$USER}"
REPO_DIR="/mnt/ssd/iot-hub/repo"
UPDATER="${REPO_DIR}/scripts/edge-updater.sh"

[[ -f "${UPDATER}" ]] || error "Updater script not found at ${UPDATER}. Clone the repo first."
[[ -f "${REPO_DIR}/.env" ]] || error ".env not found in ${REPO_DIR}. Run: cp .env.example .env && nano .env"

chmod +x "${UPDATER}"

# ---------------------------------------------------------------------------
# Verify SSH access to GitHub (needed for git pull)
# ---------------------------------------------------------------------------
info "Testing SSH access to GitHub..."
if ! sudo -u "${REAL_USER}" ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
    warn "SSH to GitHub failed. Make sure the RPi's SSH key is added to your GitHub account:"
    warn "  cat ~/.ssh/id_ed25519.pub   # or id_rsa.pub"
    warn "  Then add it at: https://github.com/settings/keys"
    warn "Continuing install — fix SSH before the timer can pull updates."
fi

# ---------------------------------------------------------------------------
# Systemd service
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/iot-hub-updater.service <<EOF
[Unit]
Description=IoT Hub — pull updates from GitHub and restart containers
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=oneshot
User=${REAL_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${UPDATER}
StandardOutput=append:/mnt/ssd/iot-hub/logs/updater.log
StandardError=append:/mnt/ssd/iot-hub/logs/updater.log
EOF

# ---------------------------------------------------------------------------
# Systemd timer (every 5 minutes, starting 2 min after boot)
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/iot-hub-updater.timer <<EOF
[Unit]
Description=IoT Hub — poll GitHub for updates every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now iot-hub-updater.timer

info "Timer installed and started."
info "  Status:  systemctl status iot-hub-updater.timer"
info "  Logs:    tail -f /mnt/ssd/iot-hub/logs/updater.log"
info "  Run now: systemctl start iot-hub-updater.service"
