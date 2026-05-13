#!/usr/bin/env bash
set -euo pipefail

# Override MODEL_URL via env for bench/migration (see hub/docker-compose.bench.yml)
MODEL_URL="${MODEL_URL:-https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf}"
MODEL_DIR="$(dirname "${MODEL_PATH}")"

mkdir -p "${MODEL_DIR}"

if [[ ! -f "${MODEL_PATH}" ]]; then
    echo "[INFO] Downloading model to ${MODEL_PATH}..."
    curl -L --progress-bar "${MODEL_URL}" -o "${MODEL_PATH}"
fi

exec python -m llama_cpp.server \
    --model "${MODEL_PATH}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --n_ctx "${N_CTX}" \
    --n_threads "${N_THREADS}" \
    --chat_format chatml
