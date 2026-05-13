#!/usr/bin/env bash
# wifi_stream_sender.sh — push a local camera to the RPi mediamtx RTSP server.
#
# Usage:
#   ./scripts/wifi_stream_sender.sh [RPI_IP] [CAMERA_INDEX]
#
# Examples:
#   ./scripts/wifi_stream_sender.sh                     # interactive: asks for IP
#   ./scripts/wifi_stream_sender.sh 192.168.1.42        # default camera (index 0)
#   ./scripts/wifi_stream_sender.sh 192.168.1.42 1      # second camera
#
# Requirements:
#   - ffmpeg installed (brew install ffmpeg  /  apt install ffmpeg)
#   - RPi running: docker compose -f hub/docker-compose.edge.yml up mediamtx
#
# The stream lands at rtsp://RPI_IP:8554/camera and is consumed by the CV
# pipeline container (hub/edge/cv/pipeline.py) via RTSP_URL=rtsp://mediamtx:8554/camera.

set -euo pipefail

RPI_IP="${1:-}"
CAMERA_INDEX="${2:-0}"
TARGET_FPS=15
TARGET_WIDTH=640
TARGET_HEIGHT=480

# ── Locate ffmpeg ──────────────────────────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
  echo "ERROR: ffmpeg not found."
  echo "  macOS:  brew install ffmpeg"
  echo "  Linux:  sudo apt install ffmpeg"
  exit 1
fi

# ── Resolve RPi IP ────────────────────────────────────────────────────────────
if [[ -z "$RPI_IP" ]]; then
  read -rp "RPi IP address [e.g. 192.168.1.42]: " RPI_IP
fi
if [[ -z "$RPI_IP" ]]; then
  echo "ERROR: RPi IP is required."
  exit 1
fi

RTSP_TARGET="rtsp://${RPI_IP}:8554/camera"

# ── Detect OS and pick the right capture device ───────────────────────────────
OS="$(uname -s)"

case "$OS" in
  Darwin)
    INPUT_FLAGS=(-f avfoundation -framerate "$TARGET_FPS" -video_size "${TARGET_WIDTH}x${TARGET_HEIGHT}" -i "${CAMERA_INDEX}:none")
    ;;
  Linux)
    # Try V4L2 first; fall back to x11grab for screen share.
    DEVICE="/dev/video${CAMERA_INDEX}"
    if [[ -e "$DEVICE" ]]; then
      INPUT_FLAGS=(-f v4l2 -framerate "$TARGET_FPS" -video_size "${TARGET_WIDTH}x${TARGET_HEIGHT}" -i "$DEVICE")
    else
      echo "WARNING: $DEVICE not found — falling back to screen capture (x11grab)"
      DISPLAY_VAR="${DISPLAY:-:0}"
      INPUT_FLAGS=(-f x11grab -framerate "$TARGET_FPS" -video_size "${TARGET_WIDTH}x${TARGET_HEIGHT}" -i "${DISPLAY_VAR}.0+0,0")
    fi
    ;;
  *)
    echo "ERROR: Unsupported OS: $OS. Use ffmpeg manually:"
    echo "  ffmpeg -f <your_capture_device> -i <source> -vcodec libx264 -preset ultrafast -tune zerolatency -f rtsp $RTSP_TARGET"
    exit 1
    ;;
esac

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " WiFi Camera Sender"
echo " Target : $RTSP_TARGET"
echo " Camera : $CAMERA_INDEX  |  ${TARGET_WIDTH}x${TARGET_HEIGHT} @ ${TARGET_FPS}fps"
echo " OS     : $OS"
echo " Press Ctrl-C to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Stream ────────────────────────────────────────────────────────────────────
# -preset ultrafast + -tune zerolatency minimise encode latency so the CV
# pipeline sees near-real-time frames. Profile baseline keeps compatibility with
# most RTSP clients. Buffer sizes (-bufsize, -maxrate) cap bitrate for WiFi.
exec ffmpeg \
  "${INPUT_FLAGS[@]}" \
  -vcodec libx264 \
  -profile:v baseline \
  -preset ultrafast \
  -tune zerolatency \
  -r "$TARGET_FPS" \
  -s "${TARGET_WIDTH}x${TARGET_HEIGHT}" \
  -b:v 1500k \
  -maxrate 1500k \
  -bufsize 3000k \
  -pix_fmt yuv420p \
  -an \
  -f rtsp \
  -rtsp_transport tcp \
  "$RTSP_TARGET"
