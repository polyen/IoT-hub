#!/usr/bin/env bash
# llm_bench_all.sh
# ─────────────────────────────────────────────────────────────────────────────
# Single entry-point for LLM model comparison on RPi 5.
#
# What it does:
#   1. Build the LLM Docker image (once; skip with --no-build)
#   2. Pre-download all GGUF models that aren't already on disk
#   3. For each model: start a temporary container → wait for /v1/models →
#      run Phase B benchmark → stop and remove the container
#   4. Aggregate all results into materials/evaluation_results/llm_bench_matrix.md
#      and print a coloured summary table
#
# Usage (on RPi 5, from project root):
#   bash scripts/llm_bench_all.sh [OPTIONS]
#
# Options:
#   --cv-active     Mark benchmark as "CV pipeline active" (adds load context)
#   --n-runs N      Repeats per query (default 3)
#   --no-build      Skip docker build (image must already exist as iot-hub-llm-bench:latest)
#
# Env overrides:
#   MODELS_DIR    GGUF storage dir   (default /opt/iot-hub/models/llm)
#   RESULTS_DIR   JSON/MD output     (default materials/evaluation_results)
#   TIMEOUT_S     Per-request limit  (default 300)
#   MAX_TOKENS    max_tokens/request (default 192)
#   LLM_PORT      llama.cpp port     (default 8001)

set -euo pipefail

# ─── Locate uv ────────────────────────────────────────────────────────────────
# uv is installed to ~/.local/bin by the official installer but that directory
# is often absent from PATH in non-interactive shells (SSH, cron, sudo -u).
# Check the standard locations before giving up.
_find_uv() {
  if command -v uv &>/dev/null; then command -v uv; return; fi
  for candidate in \
      "/home/vlad/.local/bin/uv" \
      "${HOME}/.local/bin/uv" \
      "/usr/local/bin/uv" \
      "/usr/bin/uv"; do
    [[ -x "${candidate}" ]] && { echo "${candidate}"; return; }
  done
  return 1
}
UV="$(_find_uv)" || {
  echo "[ERR] uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
}

# ─── Model registry ───────────────────────────────────────────────────────────
# Each row: "name | gguf_filename | download_url | n_ctx | n_threads | mem_limit"
#
#   name          label that appears in result JSON/markdown
#   gguf_filename must match the URL basename (case-insensitive); the entrypoint
#                 enforces this so the wrong model is never silently loaded
#   n_ctx         KV-cache window (tokens)
#   n_threads     CPU inference threads
#   mem_limit     Docker memory cap — prevents OOM kills on RPi 5
#
# To add a new model: append a row and re-run. Already-downloaded GGUFs are
# reused; the new model is downloaded, benchmarked, and included in the matrix.
MODELS=(
  "qwen2.5-3b-instruct|qwen2.5-3b-instruct-q4_k_m.gguf|https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf|4096|4|4g"
  "qwen3.5-4b-q4|qwen3.5-4b-q4_k_m.gguf|https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf|8192|6|6g"
)

# ─── Defaults ─────────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${MODELS_DIR:-/opt/iot-hub/models/llm}"
RESULTS_DIR="${RESULTS_DIR:-${PROJECT_DIR}/materials/evaluation_results}"
N_RUNS="${N_RUNS:-3}"
TIMEOUT_S="${TIMEOUT_S:-300}"
MAX_TOKENS="${MAX_TOKENS:-192}"
LLM_PORT="${LLM_PORT:-8001}"
IMAGE_TAG="iot-hub-llm-bench:latest"
CONTAINER_NAME="llm-bench"
MATRIX_MD="${RESULTS_DIR}/llm_bench_matrix.md"
MATRIX_JSON="${RESULTS_DIR}/llm_bench_matrix.json"

# ─── Args ─────────────────────────────────────────────────────────────────────
CV_ACTIVE=false
NO_BUILD=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cv-active) CV_ACTIVE=true ; shift ;;
    --no-build)  NO_BUILD=true  ; shift ;;
    --n-runs)    N_RUNS="$2"    ; shift 2 ;;
    *) echo "[WARN] Unknown arg: $1" ; shift ;;
  esac
done

# ─── Colours (disabled when not a TTY) ────────────────────────────────────────
if [[ -t 1 ]]; then
  BOLD='\033[1m' CYN='\033[0;36m' GRN='\033[0;32m' YLW='\033[1;33m' RED='\033[0;31m' RST='\033[0m'
else
  BOLD='' CYN='' GRN='' YLW='' RED='' RST=''
fi
info()   { printf "${CYN}[INFO]${RST}  %s\n" "$*"; }
ok()     { printf "${GRN}[OK]${RST}    %s\n" "$*"; }
warn()   { printf "${YLW}[WARN]${RST}  %s\n" "$*"; }
err()    { printf "${RED}[ERR]${RST}   %s\n" "$*" >&2; }
banner() { printf "\n${BOLD}${CYN}══════════════════════════════════════════${RST}\n${BOLD} %s${RST}\n${BOLD}${CYN}══════════════════════════════════════════${RST}\n" "$*"; }

# ─── Cleanup ──────────────────────────────────────────────────────────────────
cleanup() {
  if docker inspect "${CONTAINER_NAME}" &>/dev/null; then
    warn "Stopping container ${CONTAINER_NAME} ..."
    docker stop "${CONTAINER_NAME}" &>/dev/null || true
    docker rm   "${CONTAINER_NAME}" &>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# ─── Sanity checks ────────────────────────────────────────────────────────────
cd "${PROJECT_DIR}"
mkdir -p "${MODELS_DIR}" "${RESULTS_DIR}"

info "Project  : ${PROJECT_DIR}"
info "Models   : ${MODELS_DIR}"
info "Results  : ${RESULTS_DIR}"
info "n_runs=${N_RUNS}  timeout=${TIMEOUT_S}s  max_tokens=${MAX_TOKENS}  cv_active=${CV_ACTIVE}"

if curl -sf "http://localhost:${LLM_PORT}/v1/models" &>/dev/null; then
  err "Port ${LLM_PORT} is already serving — stop the running LLM container first,"
  err "or set LLM_PORT=<free-port> and retry."
  exit 1
fi

total_steps=$(( ${#MODELS[@]} + 2 ))  # build + download + N×bench + aggregate
step=0
next_step() { step=$(( step + 1 )); banner "Step ${step}/${total_steps} — $*"; }

# ─── Step 1: build image ──────────────────────────────────────────────────────
next_step "Build Docker image"
if [[ "${NO_BUILD}" == "true" ]]; then
  if ! docker image inspect "${IMAGE_TAG}" &>/dev/null; then
    err "Image '${IMAGE_TAG}' not found and --no-build was set.  Run without --no-build first."
    exit 1
  fi
  info "Reusing existing image: ${IMAGE_TAG}"
else
  docker build \
    -f hub/edge/agent/Dockerfile.llm \
    -t "${IMAGE_TAG}" \
    --build-arg CMAKE_ARGS="-DLLAMA_NATIVE=ON" \
    . 2>&1 | tail -6
  ok "Image: ${IMAGE_TAG}"
fi

# ─── Step 2: download models ──────────────────────────────────────────────────
next_step "Download models (cached if already present)"
for entry in "${MODELS[@]}"; do
  IFS='|' read -r m_name m_gguf m_url _ctx _thr _mem <<< "${entry}"
  dest="${MODELS_DIR}/${m_gguf}"
  if [[ -f "${dest}" ]]; then
    size="$(du -sh "${dest}" 2>/dev/null | awk '{print $1}')"
    ok "${m_name}: cached  ${dest}  (${size})"
  else
    info "${m_name}: downloading …"
    tmp="${dest}.partial"
    rm -f "${tmp}"
    curl -L --fail -# "${m_url}" -o "${tmp}"
    mv "${tmp}" "${dest}"
    size="$(du -sh "${dest}" 2>/dev/null | awk '{print $1}')"
    ok "${m_name}: saved  (${size})"
  fi
done

# ─── Steps 3…N: benchmark each model ─────────────────────────────────────────
cv_flag=()
[[ "${CV_ACTIVE}" == "true" ]] && cv_flag=(--cv-active)

for entry in "${MODELS[@]}"; do
  IFS='|' read -r m_name m_gguf m_url m_ctx m_threads m_mem <<< "${entry}"

  next_step "Benchmark  ${m_name}"

  # Ensure no leftover container on the port
  docker stop "${CONTAINER_NAME}" &>/dev/null || true
  docker rm   "${CONTAINER_NAME}" &>/dev/null || true

  info "Starting container  (ctx=${m_ctx}  threads=${m_threads}  mem=${m_mem})"
  docker run -d \
    --name "${CONTAINER_NAME}" \
    -p "${LLM_PORT}:8001" \
    -v "${MODELS_DIR}:/app/models:ro" \
    -e "MODEL_URL=${m_url}" \
    -e "MODEL_PATH=/app/models/${m_gguf}" \
    -e "N_CTX=${m_ctx}" \
    -e "N_THREADS=${m_threads}" \
    -e "HOST=0.0.0.0" \
    -e "PORT=8001" \
    --memory="${m_mem}" \
    "${IMAGE_TAG}" >/dev/null

  # Wait until /v1/models responds (model load ≈ 20-40 s on RPi 5)
  info "Waiting for server …"
  waited=0
  until curl -sf "http://localhost:${LLM_PORT}/v1/models" &>/dev/null; do
    if (( waited >= 300 )); then
      err "${m_name}: server did not start within 300 s"
      docker logs "${CONTAINER_NAME}" --tail 30
      exit 1
    fi
    sleep 5
    (( waited += 5 )) || true
    printf "."
  done
  printf "\n"
  ok "${m_name} ready  (startup: ${waited} s)"

  # Each model writes into its own sub-directory so aggregate can rglob them all
  result_dir="${RESULTS_DIR}/$(printf '%s' "${m_name}" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/_/g')"
  mkdir -p "${result_dir}"

  info "Running Phase B  (n_runs=${N_RUNS}, timeout=${TIMEOUT_S} s, max_tokens=${MAX_TOKENS}) …"
  "${UV}" run python -m training.llm_eval.tool_accuracy_bench \
    --phase      B \
    "${cv_flag[@]}" \
    --model-name "${m_name}" \
    --queries    training/llm_eval/queries.yaml \
    --output     "${result_dir}" \
    --n-runs     "${N_RUNS}" \
    --timeout-s  "${TIMEOUT_S}" \
    --max-tokens "${MAX_TOKENS}" \
    --llm-url    "http://localhost:${LLM_PORT}"

  ok "${m_name}: done  →  ${result_dir}/"

  docker stop "${CONTAINER_NAME}" &>/dev/null || true
  docker rm   "${CONTAINER_NAME}" &>/dev/null || true
done

# ─── Final step: aggregate & print ────────────────────────────────────────────
next_step "Aggregate comparison matrix"
uv run python -m training.llm_eval.tool_accuracy_bench \
  --mode       aggregate \
  --output     "${RESULTS_DIR}" \
  --matrix-out "${MATRIX_MD}"

# ── Coloured terminal summary (parsed from the aggregate JSON) ─────────────────
printf "\n${BOLD}${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}\n"
printf "${BOLD}  LLM Benchmark Results${RST}\n"
printf "${BOLD}${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}\n\n"

"${UV}" run python3 - "${MATRIX_JSON}" <<'PYEOF'
import json, sys, os

path = sys.argv[1]
if not os.path.exists(path):
    print("(aggregate JSON not found)")
    sys.exit(0)

rows = json.loads(open(path).read()).get("rows", [])
if not rows:
    print("(no results to display)")
    sys.exit(0)

BOLD = "\033[1m"; CYN = "\033[0;36m"; GRN = "\033[0;32m"
YLW = "\033[1;33m"; RED = "\033[0;31m"; RST = "\033[0m"

def f(v, dec=3, suffix=""):
    return f"{v:.{dec}f}{suffix}" if v is not None else "—"

def colour_acc(v):
    if v is None:      return "—"
    s = f"{v:.4f}"
    if v >= 0.80:      return f"{GRN}{s}{RST}"
    if v >= 0.60:      return f"{YLW}{s}{RST}"
    return f"{RED}{s}{RST}"

# Header
print(f"  {'Model':<28} {'Acc':>6} {'tok/s':>6} {'lat_mean':>9} {'lat_p95':>8} {'RAM':>5}  {'det':>5} {'str':>5} {'cre':>5} {'unk':>5}")
print(f"  {'─'*28} {'─'*6} {'─'*6} {'─'*9} {'─'*8} {'─'*5}  {'─'*5} {'─'*5} {'─'*5} {'─'*5}")

for r in rows:
    bcat = r.get("by_category", {}) if isinstance(r.get("by_category"), dict) else {}
    # by_category might be stored flat in the aggregate row — check top-level keys
    det = bcat.get("deterministic") or r.get("deterministic")
    stru = bcat.get("structured")   or r.get("structured")
    cre  = bcat.get("creative")     or r.get("creative")
    unk  = bcat.get("unknown")      or r.get("unknown")

    cv_mark = " ●" if r.get("cv_active") else "  "
    print(
        f"  {r['model']:<28}"
        f" {colour_acc(r.get('accuracy')):>6}"
        f" {f(r.get('tok_s'), 2):>6}"
        f" {f(r.get('latency_mean_s'), 2, 's'):>9}"
        f" {f(r.get('latency_p95_s'), 2, 's'):>8}"
        f" {f(r.get('ram_gb'), 1, 'G'):>5}"
        f"  {f(det, 2):>5} {f(stru, 2):>5} {f(cre, 2):>5} {f(unk, 2):>5}"
        f"{cv_mark}"
    )

print(f"\n  ● = CV pipeline active during benchmark")
PYEOF

printf "\n"
ok "Markdown matrix : ${MATRIX_MD}"
ok "JSON data       : ${MATRIX_JSON}"
printf "\n${BOLD}Done.${RST}\n\n"
