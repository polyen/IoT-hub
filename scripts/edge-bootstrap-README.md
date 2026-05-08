# Edge Bootstrap — Raspberry Pi 5 Setup

## Prerequisites

- Raspberry Pi 5 with Hailo-8 HAT installed
- Raspberry Pi OS (64-bit, bookworm) freshly imaged
- Internet connection
- (Optional) NVMe SSD in the PCIe slot

## Usage

```bash
curl -fsSL https://raw.githubusercontent.com/your-org/iot-hub/main/scripts/edge-bootstrap.sh | sudo bash
```

Or clone the repo and run locally:

```bash
sudo bash scripts/edge-bootstrap.sh
```

The script is **idempotent** — safe to re-run after failures or reboots.

## What it does

1. Checks the running OS is ARM64 Linux and the caller is root
2. Detects the Hailo-8 HAT (`/dev/hailo0` or `hailortcli scan`)
3. Installs Docker + Compose plugin via the official convenience script
4. Installs HailoRT runtime *(manual step — see below)*
5. Partitions the NVMe SSD (`/dev/nvme0n1p1`)
6. LUKS-encrypts the partition, formats ext4, mounts at `/mnt/edge-data`
7. Persists the LUKS mapping in `/etc/crypttab` and `/etc/fstab`
8. Adds the calling user to the `docker` group

## HailoRT manual install

The script cannot auto-download HailoRT because it requires a hailo.ai developer account.

1. Go to https://hailo.ai/developer-zone/
2. Download the `.deb` package for your OS version
3. `sudo dpkg -i hailort_*.deb`
4. Reboot and re-run the bootstrap script

## LUKS passphrase recovery

**Store your LUKS passphrase in a password manager before the partition is encrypted.**
If the passphrase is lost, the data on the NVMe is unrecoverable.

To add a backup key slot (e.g., a recovery key file):

```bash
sudo cryptsetup luksAddKey /dev/nvme0n1p1 /path/to/recovery.key
```

To list active key slots:

```bash
sudo cryptsetup luksDump /dev/nvme0n1p1 | grep -i "key slot"
```
