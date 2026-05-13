#!/usr/bin/env bash
# edge-bootstrap.sh — idempotent installer for Raspberry Pi 5 + Hailo-8 HAT
#
# Storage layout:
#   SD card  (/dev/mmcblk0p2) — OS only; do NOT write project data here
#   SSD      (/dev/nvme0n1p3, /mnt/ssd) — all project files, models, Docker
#
# Run as root on the target device: sudo bash edge-bootstrap.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Pre-conditions
# ---------------------------------------------------------------------------
[[ "$(uname -m)" == "aarch64" ]] || error "Must run on ARM64 (aarch64). Got: $(uname -m)"
[[ "$(id -u)" -eq 0 ]]           || error "Must be run as root (sudo)."
[[ "$(uname -s)" == "Linux" ]]   || error "Must run on Linux."

REAL_USER="${SUDO_USER:-$USER}"
info "Running as ${REAL_USER} via sudo."

# ---------------------------------------------------------------------------
# 2. Check Hailo HAT
# ---------------------------------------------------------------------------
info "Checking Hailo HAT..."
if [[ -e /dev/hailo0 ]]; then
    info "Hailo device found at /dev/hailo0."
elif command -v hailortcli &>/dev/null && hailortcli scan 2>/dev/null | grep -q "Hailo"; then
    info "Hailo device detected via hailortcli."
else
    warn "Hailo device not found. Continuing — install HailoRT runtime in step 4."
fi

# ---------------------------------------------------------------------------
# 3. Install Docker + compose plugin
# ---------------------------------------------------------------------------
if command -v docker &>/dev/null; then
    info "Docker already installed: $(docker --version)"
else
    info "Installing Docker via official convenience script..."
    curl -fsSL https://get.docker.com | sh
    info "Docker installed."
fi

if docker compose version &>/dev/null; then
    info "Docker Compose plugin already available."
else
    warn "Docker Compose plugin not found. It should be included with the Docker install above."
fi

# ---------------------------------------------------------------------------
# 4. Install HailoRT runtime (placeholder)
# ---------------------------------------------------------------------------
if [[ -e /dev/hailo0 ]] || command -v hailortcli &>/dev/null; then
    info "HailoRT already present — skipping install."
else
    warn "TODO: Download and install HailoRT runtime from https://hailo.ai/developer-zone/"
    warn "      After downloading the .deb, run: dpkg -i hailort_*.deb"
    warn "      Then reboot and re-run this script."
fi

# ---------------------------------------------------------------------------
# 5. Ensure SSD is mounted at /mnt/ssd
#    The SSD (/dev/nvme0n1p3) is pre-formatted ext4 — no LUKS needed.
# ---------------------------------------------------------------------------
SSD_DEV="/dev/nvme0n1p3"
SSD_MOUNT="/mnt/ssd"

[[ -b "${SSD_DEV}" ]] || error "SSD device ${SSD_DEV} not found. Run lsblk to check hardware."

if mountpoint -q "${SSD_MOUNT}"; then
    info "SSD already mounted at ${SSD_MOUNT}."
else
    warn "SSD not mounted — mounting now..."
    mkdir -p "${SSD_MOUNT}"
    mount "${SSD_DEV}" "${SSD_MOUNT}"
    info "Mounted ${SSD_DEV} at ${SSD_MOUNT}."
fi

# Persist mount across reboots (nofail = system boots even without SSD)
# SSD_UUID="$(blkid -s UUID -o value "${SSD_DEV}")"
# if ! grep -q "UUID=${SSD_UUID}" /etc/fstab; then
#     echo "UUID=${SSD_UUID} ${SSD_MOUNT} ext4 defaults,noatime,nofail 0 2" >> /etc/fstab
#     info "Added ${SSD_MOUNT} to /etc/fstab (UUID=${SSD_UUID})."
# else
#     info "${SSD_MOUNT} already present in /etc/fstab."
# fi

# ---------------------------------------------------------------------------
# 6. Create project directory tree on SSD
# ---------------------------------------------------------------------------
PROJECT_ROOT="${SSD_MOUNT}/iot-hub"

for d in \
    data/db \
    data/mqtt \
    data/uploads \
    models/hailo \
    models/whisper \
    logs \
    certs
do
    mkdir -p "${PROJECT_ROOT}/${d}"
done

chown -R "${REAL_USER}:${REAL_USER}" "${PROJECT_ROOT}"
info "Project directory tree ready at ${PROJECT_ROOT}."

# Convenience symlink so configs can reference /opt/iot-hub regardless of mount point
if [[ -L /opt/iot-hub ]]; then
    info "Symlink /opt/iot-hub already exists → $(readlink /opt/iot-hub)."
elif [[ -e /opt/iot-hub ]]; then
    warn "/opt/iot-hub exists but is not a symlink — skipping."
else
    ln -s "${PROJECT_ROOT}" /opt/iot-hub
    info "Created symlink /opt/iot-hub → ${PROJECT_ROOT}."
fi

# ---------------------------------------------------------------------------
# 7. Move Docker data root to SSD (keeps SD card from filling up)
# ---------------------------------------------------------------------------
DOCKER_SSD="${SSD_MOUNT}/docker"
DAEMON_JSON="/etc/docker/daemon.json"

CURRENT_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo '')"
if [[ "${CURRENT_ROOT}" == "${DOCKER_SSD}" ]]; then
    info "Docker data root already on SSD at ${DOCKER_SSD}."
else
    info "Moving Docker data root to SSD..."
    systemctl stop docker

    mkdir -p "${DOCKER_SSD}"

    if [[ -d /var/lib/docker ]] && [[ -n "$(ls -A /var/lib/docker 2>/dev/null)" ]]; then
        info "Copying existing Docker data to SSD (this may take a while)..."
        rsync -aH /var/lib/docker/ "${DOCKER_SSD}/"
    fi

    cat > "${DAEMON_JSON}" <<DAEMON
{
  "data-root": "${DOCKER_SSD}",
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
DAEMON

    systemctl start docker
    info "Docker data root is now ${DOCKER_SSD}."
fi

# ---------------------------------------------------------------------------
# 8. Add user to docker group
# ---------------------------------------------------------------------------
if id -nG "${REAL_USER}" | grep -qw docker; then
    info "${REAL_USER} already in docker group."
else
    usermod -aG docker "${REAL_USER}"
    info "Added ${REAL_USER} to docker group. Re-login required to take effect."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
SSD_FREE="$(df -h "${SSD_MOUNT}" | awk 'NR==2{print $4}')"
echo ""
info "=== Edge Bootstrap Complete ==="
info "  Docker:         $(docker --version 2>/dev/null || echo 'installed')"
info "  Docker root:    $(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo 'unknown')"
info "  Hailo device:   $([[ -e /dev/hailo0 ]] && echo 'present' || echo 'not detected (install HailoRT)')"
info "  SSD mount:      $( mountpoint -q "${SSD_MOUNT}" && echo "${SSD_MOUNT} mounted (${SSD_FREE} free)" || echo 'NOT mounted')"
info "  Project root:   ${PROJECT_ROOT}"
info "  Docker group:   $(id -nG "${REAL_USER}" | grep -qw docker && echo 'ok' || echo 'added (re-login needed)')"
echo ""
warn "Next steps:"
warn "  1. If HailoRT is not installed, download and install it from hailo.ai/developer-zone."
warn "  2. Re-login (or run: newgrp docker) so the docker group takes effect."
warn "  3. Clone the project repo into ${PROJECT_ROOT}/repo  (or bind-mount it there)."
warn "  4. Run: docker compose -f /opt/iot-hub/repo/hub/docker-compose.edge.yml up -d"
