#!/usr/bin/env bash
# Entrypoint for the llm container.
#
# 1. If MODEL_PATH is unset/empty, derive it from MODEL_URL basename so the
#    two values are guaranteed aligned (defends against .env drift where the
#    operator overrode LLM_MODEL_URL but forgot LLM_MODEL_PATH).
# 2. If MODEL_PATH IS set, ensure its basename matches MODEL_URL (no name
#    drift between Dockerfile / compose / .env).
# 3. Download the model into MODEL_PATH on first start.
# 4. If MODEL_SHA256 is set, verify the downloaded file matches; on mismatch,
#    delete the bad blob and refuse to start so the operator notices.
# 5. exec llama-cpp-python server.
set -euo pipefail

: "${MODEL_URL:?MODEL_URL is required}"

# Default models directory — used for auto-derived MODEL_PATH.
MODEL_DIR_DEFAULT="/app/models"

# Auto-derive MODEL_PATH from URL when not set explicitly.  This is the
# safest default: if you only set LLM_MODEL_URL, the path follows.
if [[ -z "${MODEL_PATH:-}" ]]; then
    url_basename_raw="$(basename "${MODEL_URL%%\?*}")"
    MODEL_PATH="${MODEL_DIR_DEFAULT}/${url_basename_raw}"
    echo "[INFO] MODEL_PATH not set — derived from URL: ${MODEL_PATH}"
fi

MODEL_DIR="$(dirname "${MODEL_PATH}")"
mkdir -p "${MODEL_DIR}"

# Sanity check: when MODEL_PATH is set explicitly, its basename must match
# MODEL_URL.  Catches the historical bug where the Dockerfile said qwen3.5-4b
# but the URL pointed at qwen2.5-3b → operator silently got the wrong model.
url_basename="$(basename "${MODEL_URL%%\?*}" | tr '[:upper:]' '[:lower:]')"
path_basename="$(basename "${MODEL_PATH}" | tr '[:upper:]' '[:lower:]')"
if [[ "${url_basename}" != "${path_basename}" ]]; then
    echo "[ERROR] MODEL_URL basename ($(basename "${MODEL_URL%%\?*}")) does not match MODEL_PATH basename ($(basename "${MODEL_PATH}"))." >&2
    echo "[ERROR] Refusing to download — the file would be misnamed and downstream code would load the wrong model." >&2
    echo "[HINT]  Either unset LLM_MODEL_PATH in .env to auto-derive, or align both LLM_MODEL_URL and LLM_MODEL_PATH." >&2
    exit 2
fi

if [[ ! -f "${MODEL_PATH}" ]]; then
    echo "[INFO] Downloading model from ${MODEL_URL} → ${MODEL_PATH}..."
    tmp="${MODEL_PATH}.partial"
    rm -f "${tmp}"
    curl -L --fail --progress-bar "${MODEL_URL}" -o "${tmp}"
    mv "${tmp}" "${MODEL_PATH}"
fi

if [[ -n "${MODEL_SHA256:-}" ]]; then
    echo "[INFO] Verifying SHA256..."
    actual="$(sha256sum "${MODEL_PATH}" | awk '{print $1}')"
    if [[ "${actual}" != "${MODEL_SHA256}" ]]; then
        echo "[ERROR] SHA256 mismatch for ${MODEL_PATH}:" >&2
        echo "[ERROR]   expected: ${MODEL_SHA256}" >&2
        echo "[ERROR]   actual:   ${actual}" >&2
        rm -f "${MODEL_PATH}"
        exit 3
    fi
    echo "[INFO] SHA256 ok."
else
    echo "[WARN] MODEL_SHA256 not set — skipping integrity check. Set LLM_MODEL_SHA256 in .env to enable."
fi

exec python -m llama_cpp.server \
    --model "${MODEL_PATH}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --n_ctx "${N_CTX}" \
    --n_threads "${N_THREADS}" \
    --chat_format chatml
