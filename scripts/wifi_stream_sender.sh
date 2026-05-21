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
RECONNECT_DELAY=3   # seconds to wait before reconnecting after stream drop

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
    # VideoToolbox: Apple hardware encoder — no CPU load, runs at realtime on any Mac.
    # Falls back to libx264 ultrafast if h264_videotoolbox is unavailable.
    if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_videotoolbox; then
      ENCODE_FLAGS=(-vcodec h264_videotoolbox -profile:v baseline -level:v 3.1 -realtime 1 -b:v 1500k -maxrate 1500k -bufsize 3000k)
    else
      ENCODE_FLAGS=(-vcodec libx264 -profile:v baseline -preset ultrafast -tune zerolatency -b:v 1500k -maxrate 1500k -bufsize 3000k)
    fi
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
    ENCODE_FLAGS=(-vcodec libx264 -profile:v baseline -preset ultrafast -tune zerolatency -threads 0 -b:v 1500k -maxrate 1500k -bufsize 3000k)
    ;;
  *)
    echo "ERROR: Unsupported OS: $OS. Use ffmpeg manually:"
    echo "  ffmpeg -f <your_capture_device> -i <source> -vcodec libx264 -preset ultrafast -tune zerolatency -f rtsp $RTSP_TARGET"
    exit 1
    ;;
esac

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " WiFi Camera Sender"
echo " Target  : $RTSP_TARGET"
echo " Camera  : $CAMERA_INDEX  |  ${TARGET_WIDTH}x${TARGET_HEIGHT} @ ${TARGET_FPS}fps"
echo " Encoder : ${ENCODE_FLAGS[1]}"
echo " OS      : $OS"
echo " Press Ctrl-C to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Stream loop ───────────────────────────────────────────────────────────────
# Restarts automatically on stream drop (mediamtx timeout, network glitch).
# macOS uses h264_videotoolbox (hardware) to stay at realtime without CPU load.
# -tune zerolatency is included in ENCODE_FLAGS for libx264; not passed for
# VideoToolbox (unsupported option).
while true; do
  ffmpeg \
    "${INPUT_FLAGS[@]}" \
    "${ENCODE_FLAGS[@]}" \
    -r "$TARGET_FPS" \
    -s "${TARGET_WIDTH}x${TARGET_HEIGHT}" \
    -pix_fmt yuv420p \
    -an \
    -f rtsp \
    -rtsp_transport tcp \
    "$RTSP_TARGET" || true

  echo "[sender] Stream ended — reconnecting in ${RECONNECT_DELAY}s (Ctrl-C to stop)"
  sleep "$RECONNECT_DELAY"
done
