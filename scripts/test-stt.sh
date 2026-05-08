#!/usr/bin/env bash
# Quick STT test — records 5 seconds and transcribes
set -euo pipefail
OUTFILE="$(mktemp /tmp/stt_test_XXXX.wav)"
echo "Recording 5 seconds..."
arecord -f S16_LE -r 16000 -c 1 -d 5 "${OUTFILE}" 2>/dev/null
echo "Transcribing..."
python -m hub.edge.voice.hailo_whisper --record "${OUTFILE}"
rm -f "${OUTFILE}"
