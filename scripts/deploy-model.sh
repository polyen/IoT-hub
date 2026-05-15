#!/usr/bin/env bash
# deploy-model.sh — post-training deployment: export ONNX, push to DVC, trigger CI HEF compile.
#
# Usage:
#   scripts/deploy-model.sh --model fire_smoke --version v1.0
#   scripts/deploy-model.sh --model fire_smoke --version v1.1 --weights runs/fire_smoke/train2/weights/best.pt
#
# What it does:
#   1. Export ONNX from best.pt (skips if best.onnx already exists alongside best.pt)
#   2. Copy to models/onnx/{model}_{version}.onnx
#   3. dvc add + dvc push
#   4. git commit the .dvc pointer file
#   5. gh workflow run compile-hef.yml  → CI compiles HEF and pushes to GHCR
#      RPi model-puller picks it up automatically within 5 minutes.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[deploy]${NC} $*"; }
error() { echo -e "${RED}[deploy]${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
MODEL=""
VERSION=""
WEIGHTS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)    MODEL="$2";   shift 2 ;;
        --version)  VERSION="$2"; shift 2 ;;
        --weights)  WEIGHTS="$2"; shift 2 ;;
        *) error "Unknown argument: $1" ;;
    esac
done

[[ -n "$MODEL" ]]   || error "Usage: $0 --model <name> --version <vX.Y> [--weights <path/best.pt>]"
[[ -n "$VERSION" ]] || error "Usage: $0 --model <name> --version <vX.Y> [--weights <path/best.pt>]"

# Default weights path: runs/{model}/train/weights/best.pt
if [[ -z "$WEIGHTS" ]]; then
    WEIGHTS="runs/${MODEL}/train/weights/best.pt"
fi

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
command -v gh   &>/dev/null || error "'gh' CLI not found. Install: https://cli.github.com"
command -v dvc  &>/dev/null || error "'dvc' not found. Run: uv sync --extra dev"
command -v git  &>/dev/null || error "'git' not found."

[[ -f "$WEIGHTS" ]] || error "Weights file not found: $WEIGHTS"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

DEST_DIR="models/onnx"
DEST="$DEST_DIR/${MODEL}_${VERSION}.onnx"

if [[ -f "$DEST" ]]; then
    warn "ONNX already exists: $DEST — skipping export and DVC steps."
    warn "If you want to re-export, delete the file first."
else
    # ---------------------------------------------------------------------------
    # Step 1: Export ONNX
    # ---------------------------------------------------------------------------
    WEIGHTS_DIR="$(dirname "$WEIGHTS")"
    ONNX_CANDIDATE="${WEIGHTS_DIR}/best.onnx"

    if [[ -f "$ONNX_CANDIDATE" ]]; then
        info "Found existing ONNX: $ONNX_CANDIDATE"
    else
        info "Exporting ONNX from $WEIGHTS ..."
        uv run yolo export model="$WEIGHTS" format=onnx imgsz=640
        [[ -f "$ONNX_CANDIDATE" ]] || error "Export failed — best.onnx not found at $ONNX_CANDIDATE"
        info "Export complete."
    fi

    # ---------------------------------------------------------------------------
    # Step 2: Stage in models/onnx/
    # ---------------------------------------------------------------------------
    mkdir -p "$DEST_DIR"
    cp "$ONNX_CANDIDATE" "$DEST"
    info "Copied → $DEST"

    # ---------------------------------------------------------------------------
    # Step 3: DVC add + push
    # ---------------------------------------------------------------------------
    info "Running dvc add ..."
    dvc add "$DEST"

    info "Running dvc push ..."
    dvc push "$DEST"

    # ---------------------------------------------------------------------------
    # Step 4: Git commit the .dvc pointer
    # ---------------------------------------------------------------------------
    git add "${DEST}.dvc" .gitignore 2>/dev/null || true
    if git diff --cached --quiet; then
        warn "Nothing new to commit (pointer unchanged)."
    else
        git commit -m "feat: add ONNX for ${MODEL} ${VERSION}"
        info "Git commit created."
    fi
fi

# ---------------------------------------------------------------------------
# Step 5: Trigger GitHub Actions HEF compile
# ---------------------------------------------------------------------------
info "Triggering compile-hef.yml (model=${MODEL} version=${VERSION}) ..."
gh workflow run compile-hef.yml \
    --field "model=${MODEL}" \
    --field "version=${VERSION}"

info "Done. CI is compiling HEF and will push to GHCR."
info "RPi model-puller will promote the new model within 5 minutes."
info ""
info "Monitor CI:  gh run list --workflow=compile-hef.yml"
info "Monitor RPi: journalctl -u model-puller -f   (on the RPi)"
