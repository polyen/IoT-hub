#!/usr/bin/env bash
# deploy-model.sh — post-training deployment: ONNX export → HEF compile → GHCR push.
#
# Compiles locally using the Hailo DFC Docker image (17.5 GB, already loaded).
# Pushes only the resulting HEF (~5 MB) to GHCR.
# RPi model-puller picks it up automatically within 5 minutes.
#
# Usage:
#   scripts/deploy-model.sh --model fire_smoke --version v1.0
#   scripts/deploy-model.sh --model fire_smoke --version v1.1 --weights runs/fire_smoke/train2/weights/best.pt
#
# Optional flags:
#   --calib-dir <path>   directory of int8 calibration images (auto-probed if omitted)
#   --end-nodes a,b      comma-separated graph cut nodes (auto-set for fire_smoke/YOLO26)
#
# Required env vars (or set in .env):
#   GHCR_OWNER   — GitHub username
#   GHCR_TOKEN   — GitHub PAT with write:packages scope
#
# Flow:
#   1. Export ONNX from best.pt  (skips if .onnx already exists)
#   2. Copy to models/onnx/{model}_{version}.onnx
#   3. dvc add + dvc push  (versioned backup to GDrive)
#   4. git commit .dvc pointer
#   5. docker run hailo_dfc → HEF
#   6. oras push HEF to GHCR as OCI artifact
#      → RPi model-puller promotes within 5 min
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[deploy]${NC} $*"; }
error() { echo -e "${RED}[deploy]${NC} $*" >&2; exit 1; }

# Load .env from repo root if present
REPO_ROOT="$(git rev-parse --show-toplevel)"
[[ -f "${REPO_ROOT}/.env" ]] && set -a && source "${REPO_ROOT}/.env" && set +a

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
MODEL=""
VERSION=""
WEIGHTS=""
END_NODES=""
CALIB_DIR_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)      MODEL="$2";         shift 2 ;;
        --version)    VERSION="$2";       shift 2 ;;
        --weights)    WEIGHTS="$2";       shift 2 ;;
        --end-nodes)  END_NODES="$2";     shift 2 ;;
        --calib-dir)  CALIB_DIR_ARG="$2"; shift 2 ;;
        *) error "Unknown argument: $1" ;;
    esac
done

[[ -n "$MODEL" ]]   || error "Usage: $0 --model <name> --version <vX.Y> [--weights <path>]"
[[ -n "$VERSION" ]] || error "Usage: $0 --model <name> --version <vX.Y> [--weights <path>]"

WEIGHTS="${WEIGHTS:-runs/${MODEL}/train/weights/best.pt}"

# YOLO26 detection HEFs must be cut into SEPARATE box + class outputs. A single
# concatenated output shares one uint8 quantisation scale between box coords
# (~0-640) and class scores (0-1), collapsing every class score to zero — the
# detector then loads and runs but never fires. See training/convert_to_hef.py.
if [[ -z "$END_NODES" && "$MODEL" == "fire_smoke" ]]; then
    END_NODES="/model.23/Mul_2,/model.23/Sigmoid"
fi

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GHCR_OWNER="${GHCR_OWNER:?Set GHCR_OWNER in env or .env}"
GHCR_TOKEN="${GHCR_TOKEN:?Set GHCR_TOKEN in env or .env}"
GHCR_REPO="iot-hub-models"
GHCR_TAG="${MODEL}-${VERSION}"

# Hailo DFC image (loaded locally via docker load)
HAILO_DFC_IMAGE="${HAILO_DFC_IMAGE:-hailo8_ai_sw_suite_2025-10:1}"

DEST_DIR="models/onnx"
HEF_DIR="models/hef"
DEST_ONNX="${DEST_DIR}/${MODEL}_${VERSION}.onnx"
DEST_HEF="${HEF_DIR}/${MODEL}_${VERSION}.hef"

cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
command -v docker &>/dev/null || error "docker not found."
command -v dvc    &>/dev/null || error "dvc not found. Run: uv sync --extra dev"
command -v oras   &>/dev/null || error "oras not found. Install: https://oras.land"
command -v git    &>/dev/null || error "git not found."

docker image inspect "$HAILO_DFC_IMAGE" &>/dev/null \
    || error "Hailo DFC image '$HAILO_DFC_IMAGE' not found. Run: docker load -i hailo8_ai_sw_suite_*.tar.gz"

# ---------------------------------------------------------------------------
# Step 1 & 2: Export ONNX → models/onnx/
# ---------------------------------------------------------------------------
if [[ -f "$DEST_ONNX" ]]; then
    warn "ONNX already exists: $DEST_ONNX — skipping export."
else
    [[ -f "$WEIGHTS" ]] || error "Weights not found: $WEIGHTS"

    ONNX_CANDIDATE="$(dirname "$WEIGHTS")/best.onnx"
    if [[ ! -f "$ONNX_CANDIDATE" ]]; then
        info "Exporting ONNX from $WEIGHTS ..."
        uv run yolo export model="$WEIGHTS" format=onnx imgsz=640
        [[ -f "$ONNX_CANDIDATE" ]] || error "Export failed — best.onnx not found."
    else
        info "Found existing ONNX: $ONNX_CANDIDATE"
    fi

    mkdir -p "$DEST_DIR"
    cp "$ONNX_CANDIDATE" "$DEST_ONNX"
    info "Copied → $DEST_ONNX"

    # Step 3: DVC add + push
    info "DVC add + push ..."
    dvc add "$DEST_ONNX"
    dvc push "${DEST_ONNX}.dvc"

    # Step 4: git commit pointer
    git add "${DEST_ONNX}.dvc" "${DEST_DIR}/.gitignore" 2>/dev/null || true
    if ! git diff --cached --quiet; then
        git commit -m "feat: add ONNX for ${MODEL} ${VERSION}"
        info "Git commit created."
    fi
fi

# ---------------------------------------------------------------------------
# Step 5: Compile ONNX → HEF via Hailo DFC (local Docker)
# ---------------------------------------------------------------------------
if [[ -f "$DEST_HEF" ]]; then
    warn "HEF already exists: $DEST_HEF — skipping compilation."
else
    info "Compiling HEF with $HAILO_DFC_IMAGE ..."
    mkdir -p "$HEF_DIR"

    # Calibration set: explicit --calib-dir wins, else probe known locations.
    # Real images are mandatory for usable int8 accuracy — the convert_to_hef.py
    # synthetic-random fallback badly degrades quantisation.
    CALIB_FLAG=""
    CALIB_MOUNT=""
    CALIB_DIR="$CALIB_DIR_ARG"
    if [[ -z "$CALIB_DIR" ]]; then
        for cand in "datasets/${MODEL}/calibration" \
                    "datasets/${MODEL}_mixed/valid/images" \
                    "datasets/${MODEL}/valid/images"; do
            if [[ -d "$cand" ]]; then CALIB_DIR="$cand"; break; fi
        done
    fi
    if [[ -n "$CALIB_DIR" && -d "$CALIB_DIR" ]]; then
        CALIB_FLAG="--calib-set /calib"
        CALIB_MOUNT="-v $(pwd)/${CALIB_DIR}:/calib:ro"
        info "Calibration images: ${CALIB_DIR}"
    else
        warn "No calibration images found — compiling with synthetic data (poor int8 accuracy)."
        warn "Pass --calib-dir <path>, or 'dvc pull' the dataset first."
    fi

    # End nodes: split the YOLO26 head into separate box/class outputs (see top
    # of file). convert_to_hef.py takes one --end-nodes per node.
    END_NODE_FLAGS=""
    if [[ -n "$END_NODES" ]]; then
        IFS=',' read -ra _end_nodes <<< "$END_NODES"
        for _n in "${_end_nodes[@]}"; do
            END_NODE_FLAGS+=" --end-nodes ${_n}"
        done
        info "Graph end-nodes: ${END_NODES}"
    fi

    docker run --rm --platform linux/amd64 \
        -v "$(pwd)/${DEST_DIR}:/onnx:ro" \
        -v "$(pwd)/${HEF_DIR}:/hef_output" \
        -v "$(pwd)/training:/training:ro" \
        ${CALIB_MOUNT} \
        "$HAILO_DFC_IMAGE" \
        python /training/convert_to_hef.py \
            --onnx "/onnx/${MODEL}_${VERSION}.onnx" \
            --out /hef_output \
            --model-name "${MODEL}_${VERSION}" \
            ${END_NODE_FLAGS} \
            ${CALIB_FLAG}

    [[ -f "$DEST_HEF" ]] || error "Compilation failed — HEF not found at $DEST_HEF"
    info "Compiled → $DEST_HEF"
fi

# ---------------------------------------------------------------------------
# Step 6: Push HEF to GHCR as OCI artifact
# ---------------------------------------------------------------------------
info "Pushing HEF to ghcr.io/${GHCR_OWNER}/${GHCR_REPO}:${GHCR_TAG} ..."
echo "$GHCR_TOKEN" | oras login ghcr.io -u "$GHCR_OWNER" --password-stdin
oras push "ghcr.io/${GHCR_OWNER}/${GHCR_REPO}:${GHCR_TAG}" \
    "${DEST_HEF}:application/octet-stream"

info "Done."
info "RPi model-puller will promote ${MODEL} ${VERSION} within 5 minutes."
info "Monitor RPi: journalctl -u model-puller -f"
