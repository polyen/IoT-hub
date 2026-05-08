#!/usr/bin/env bash
# edge-bootstrap.sh — idempotent installer for Raspberry Pi 5 + Hailo-8 HAT
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
# 5. Setup NVMe partition
# ---------------------------------------------------------------------------
MOUNT_POINT="/mnt/edge-data"
MAPPED_NAME="edge-data"
LUKS_DEV="/dev/mapper/${MAPPED_NAME}"

if [[ -e /dev/nvme0 ]]; then
    info "NVMe device found."

    # Determine partition (use existing p1 or create one)
    NVME_PART="/dev/nvme0n1p1"

    if mountpoint -q "${MOUNT_POINT}"; then
        info "NVMe already mounted at ${MOUNT_POINT} — skipping."
    else
        # ---------------------------------------------------------------------------
        # 6. LUKS encrypt the partition
        # ---------------------------------------------------------------------------
        if cryptsetup isLuks "${NVME_PART}" 2>/dev/null; then
            info "LUKS container already set up on ${NVME_PART}."
        else
            info "Partitioning ${NVME_PART} (creates new GPT partition table)..."
            parted -s /dev/nvme0n1 mklabel gpt mkpart primary ext4 0% 100%
            info "Setting up LUKS encryption on ${NVME_PART}..."
            warn "You will be prompted to enter a passphrase. Store it safely (see README)."
            cryptsetup luksFormat "${NVME_PART}"
            info "LUKS format complete."
        fi

        if [[ -e "${LUKS_DEV}" ]]; then
            info "LUKS device already open at ${LUKS_DEV}."
        else
            info "Opening LUKS container..."
            cryptsetup luksOpen "${NVME_PART}" "${MAPPED_NAME}"
        fi

        if blkid "${LUKS_DEV}" | grep -q "ext4"; then
            info "ext4 filesystem already present on ${LUKS_DEV}."
        else
            info "Formatting ${LUKS_DEV} as ext4..."
            mkfs.ext4 -L edge-data "${LUKS_DEV}"
        fi

        mkdir -p "${MOUNT_POINT}"
        mount "${LUKS_DEV}" "${MOUNT_POINT}"
        info "Mounted ${LUKS_DEV} at ${MOUNT_POINT}."

        # Persist mount via /etc/crypttab + /etc/fstab
        NVME_UUID="$(blkid -s UUID -o value "${NVME_PART}")"
        if ! grep -q "${MAPPED_NAME}" /etc/crypttab 2>/dev/null; then
            echo "${MAPPED_NAME} UUID=${NVME_UUID} none luks,discard" >> /etc/crypttab
            info "Added ${MAPPED_NAME} to /etc/crypttab."
        fi
        LUKS_UUID="$(blkid -s UUID -o value "${LUKS_DEV}")"
        if ! grep -q "${MOUNT_POINT}" /etc/fstab; then
            echo "UUID=${LUKS_UUID} ${MOUNT_POINT} ext4 defaults,noatime 0 2" >> /etc/fstab
            info "Added ${MOUNT_POINT} to /etc/fstab."
        fi
    fi
else
    warn "No NVMe device at /dev/nvme0 — skipping NVMe/LUKS setup."
fi

# ---------------------------------------------------------------------------
# 7. Add user to docker group
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
echo ""
info "=== Edge Bootstrap Complete ==="
info "  Docker:       $(docker --version 2>/dev/null || echo 'installed')"
info "  Hailo device: $( [[ -e /dev/hailo0 ]] && echo 'present' || echo 'not detected (install HailoRT)')"
info "  NVMe mount:   $( mountpoint -q ${MOUNT_POINT} 2>/dev/null && echo "${MOUNT_POINT} mounted" || echo 'not mounted')"
info "  Docker group: $( id -nG "${REAL_USER}" | grep -qw docker && echo 'ok' || echo 'added (re-login needed)')"
echo ""
warn "Next steps:"
warn "  1. If HailoRT is not installed, download and install it from hailo.ai."
warn "  2. Re-login (or newgrp docker) so docker group takes effect."
warn "  3. Run: docker compose -f hub/docker-compose.edge.yml up -d"
