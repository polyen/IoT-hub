#!/usr/bin/env bash
# Phase 0 runbook — benchmark Qwen2.5-3B (baseline) vs Qwen3.5-4B (candidate)
# on RPi 5 16 GB with CV cascade active.
#
# Prerequisites (on RPi 5):
#   1. Docker Compose v2 installed
#   2. Project cloned to ~/IoT-hub/IoT-hub (adjust PROJECT_DIR below if different)
#   3. CV pipeline running (or use --no-cv-active for clean numbers without load)
#   4. uv installed: curl -LsSf https://astral.sh/uv/install.sh | sh
#
# Runtime: ~60-90 min total (model downloads + 100 queries × 3 runs × 2 models)
# Disk:    ~2.75 GB Qwen3.5-4B + ~1.85 GB Qwen2.5-3B (both stay in llm-models volume)
#
# Usage:
#   ssh pi@raspberrypi.local
#   cd ~/IoT-hub/IoT-hub
#   bash scripts/run_bench_phase0.sh
#
# To skip CV load (clean numbers only):
#   BENCH_CV_ACTIVE=false bash scripts/run_bench_phase0.sh

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
COMPOSE_EDGE="hub/docker-compose.edge.yml"
COMPOSE_BENCH="hub/docker-compose.bench.yml"
QUERIES="training/llm_eval/queries.yaml"
RESULTS_BASE="materials/evaluation_results/baseline"
RESULTS_CAND="materials/evaluation_results/qwen3.5"
RESULTS_CMP="materials/evaluation_results/llm_bench_comparison_2026_05.md"
LLM_URL="${LLM_URL:-http://localhost:8001}"
BENCH_CV_ACTIVE="${BENCH_CV_ACTIVE:-true}"
N_RUNS="${N_RUNS:-3}"
# RPi5 + 4B Q4 ≈ 3 tok/s decode; 128 max_tokens → ≥45 s wall-clock per query.
# Default 180 s/timeout + 128 max_tokens; override via env if needed.
BENCH_TIMEOUT_S="${BENCH_TIMEOUT_S:-180}"
BENCH_MAX_TOKENS="${BENCH_MAX_TOKENS:-128}"

cd "${PROJECT_DIR}"
mkdir -p "${RESULTS_BASE}" "${RESULTS_CAND}"

CV_ACTIVE_FLAG=""
if [[ "${BENCH_CV_ACTIVE}" == "true" ]]; then
    CV_ACTIVE_FLAG="--cv-active"
fi

# ─── helpers ──────────────────────────────────────────────────────────────────

wait_llm_healthy() {
    local container="${1:-llm}"
    local max_wait="${2:-300}"   # seconds
    local waited=0
    echo "[INFO] Waiting for ${container} to be healthy (max ${max_wait}s) ..."
    while true; do
        status=$(docker inspect --format='{{.State.Health.Status}}' "${container}" 2>/dev/null || echo "missing")
        if [[ "${status}" == "healthy" ]]; then
            echo "[INFO] ${container} is healthy"
            return 0
        fi
        if (( waited >= max_wait )); then
            echo "[ERROR] ${container} did not become healthy within ${max_wait}s (status: ${status})"
            docker logs "${container}" --tail 30
            exit 1
        fi
        sleep 5
        (( waited += 5 ))
        echo "[INFO]   ... ${waited}s (status: ${status})"
    done
}

run_bench() {
    local model_name="$1"
    local output_dir="$2"
    echo ""
    echo "════════════════════════════════════════════"
    echo "[BENCH] model=${model_name}  cv_active=${BENCH_CV_ACTIVE}"
    echo "════════════════════════════════════════════"
    uv run python -m training.llm_eval.tool_accuracy_bench \
        --phase B \
        ${CV_ACTIVE_FLAG} \
        --model-name "${model_name}" \
        --queries "${QUERIES}" \
        --output "${output_dir}" \
        --n-runs "${N_RUNS}" \
        --timeout-s "${BENCH_TIMEOUT_S}" \
        --max-tokens "${BENCH_MAX_TOKENS}" \
        --llm-url "${LLM_URL}"
    echo "[BENCH] Done → ${output_dir}/llm_bench_${model_name}.json"
}

# ─── Step 1: baseline — Qwen2.5-3B ────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════"
echo " STEP 1/4 — Start baseline LLM (Qwen2.5-3B-Instruct)"
echo "══════════════════════════════════════════════════════"
# Use default compose (no bench override = Qwen2.5-3B)
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_EDGE}" up -d llm
# Model download on first run can take 3-5 min (1.85 GB)
wait_llm_healthy llm 360

echo ""
echo "══════════════════════════════════════════════════════"
echo " STEP 2/4 — Run Phase B bench on Qwen2.5-3B"
echo "══════════════════════════════════════════════════════"
run_bench "qwen2.5-3b-instruct" "${RESULTS_BASE}"

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_EDGE}" stop llm
echo "[INFO] Baseline LLM stopped"

# ─── Step 2: candidate — Qwen3.5-4B ───────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════"
echo " STEP 3/4 — Start candidate LLM (Qwen3.5-4B, bench override)"
echo "══════════════════════════════════════════════════════"
# Compose override adds MODEL_URL + MODEL_PATH for Qwen3.5-4B + mem_limit 6g
# The model is downloaded to a different filename — baseline GGUF is preserved
docker compose --env-file "${ENV_FILE}" \
    -f "${COMPOSE_EDGE}" \
    -f "${COMPOSE_BENCH}" \
    up -d llm
# Download: 2.74 GB, ~5-8 min on a typical home connection
wait_llm_healthy llm 600

# Optional: quick sanity check (single query)
echo "[INFO] Smoke test: single query to Qwen3.5-4B..."
uv run python - <<'EOF'
import httpx, json, sys
try:
    r = httpx.post(
        "http://localhost:8001/v1/chat/completions",
        json={"model": "qwen3.5-4b", "messages": [{"role": "user", "content": "Turn off living room lights. Respond with JSON: {\"tool\": \"...\", \"args\": {...}}"}], "max_tokens": 64, "temperature": 0},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    content = body["choices"][0]["message"]["content"]
    print(f"[SMOKE] Response: {content[:120]}")
except Exception as e:
    print(f"[SMOKE] WARN: {e} — bench will still run but check chat_format if accuracy is low")
EOF

echo ""
echo "══════════════════════════════════════════════════════"
echo " STEP 4/4 — Run Phase B bench on Qwen3.5-4B"
echo "══════════════════════════════════════════════════════"
run_bench "Qwen3.5-4B-Q4_K_M" "${RESULTS_CAND}"

docker compose --env-file "${ENV_FILE}" \
    -f "${COMPOSE_EDGE}" \
    -f "${COMPOSE_BENCH}" \
    stop llm
echo "[INFO] Candidate LLM stopped"

# ─── Step 3: generate comparison ──────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════"
echo " Generating comparison: ${RESULTS_CMP}"
echo "══════════════════════════════════════════════════════"
BASELINE_JSON="${RESULTS_BASE}/llm_bench_qwen2.5-3b-instruct.json"
CANDIDATE_JSON="${RESULTS_CAND}/llm_bench_Qwen3.5-4B-Q4_K_M.json"

if [[ ! -f "${BASELINE_JSON}" ]]; then
    echo "[ERROR] Baseline JSON not found: ${BASELINE_JSON}" >&2
    exit 1
fi
if [[ ! -f "${CANDIDATE_JSON}" ]]; then
    echo "[ERROR] Candidate JSON not found: ${CANDIDATE_JSON}" >&2
    exit 1
fi

# bench_compare.py exits 0 on GO, 1 on NO-GO
set +e
uv run python scripts/bench_compare.py \
    --baseline "${BASELINE_JSON}" \
    --candidate "${CANDIDATE_JSON}" \
    --out "${RESULTS_CMP}"
VERDICT=$?
set -e

echo ""
if (( VERDICT == 0 )); then
    echo "╔══════════════════════════════════════════╗"
    echo "║  Phase 0 complete — GO ✓                ║"
    echo "║  Proceed to Phase 1 (upgrade LLM)       ║"
    echo "╚══════════════════════════════════════════╝"
else
    echo "╔══════════════════════════════════════════╗"
    echo "║  Phase 0 complete — NO-GO ✗             ║"
    echo "║  See ${RESULTS_CMP}  ║"
    echo "║  Consider: Phi-4 mini / Gemma 3 4B      ║"
    echo "╚══════════════════════════════════════════╝"
fi
echo ""
echo "Full report: ${PROJECT_DIR}/${RESULTS_CMP}"
