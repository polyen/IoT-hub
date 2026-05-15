#!/usr/bin/env bash
# model-puller.sh — polls GHCR for new HEF artifacts and promotes them on the RPi.
#
# Runs as a systemd oneshot service every 5 minutes (see model-puller.timer).
# Reads config from environment (set via EnvironmentFile in the service unit).
#
# Required env vars:
#   GHCR_OWNER       — GitHub username / org (e.g. "vlad")
#   GHCR_TOKEN       — GitHub PAT with read:packages scope
#   DEPLOY_TOKEN     — same token used by the backend X-Deploy-Token header
#
# Optional:
#   MODELS_DIR       — defaults to /opt/iot-hub/models
#   BACKEND_URL      — defaults to http://localhost:8000
#   GHCR_REPO        — defaults to iot-hub-models
set -euo pipefail

GHCR_OWNER="${GHCR_OWNER:?Need GHCR_OWNER in environment}"
GHCR_TOKEN="${GHCR_TOKEN:?Need GHCR_TOKEN in environment}"
DEPLOY_TOKEN="${DEPLOY_TOKEN:?Need DEPLOY_TOKEN in environment}"
MODELS_DIR="${MODELS_DIR:-/opt/iot-hub/models}"
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
GHCR_REPO="${GHCR_REPO:-iot-hub-models}"

# Maps the model name prefix in a GHCR tag to the kind used by ModelStore/deploy API.
# Tag format: {model_name}-{version}  e.g. fire_smoke-v1.4, whisper-v2.0
kind_for_model() {
    case "$1" in
        fire_smoke) echo "yolo"    ;;
        pose)       echo "pose"    ;;
        face)       echo "face"    ;;
        whisper)    echo "whisper" ;;
        *)          echo ""        ;;
    esac
}

# Exchange GitHub PAT for a short-lived GHCR registry token.
ghcr_registry_token() {
    curl -sf \
        -u "${GHCR_OWNER}:${GHCR_TOKEN}" \
        "https://ghcr.io/token?scope=repository:${GHCR_OWNER}/${GHCR_REPO}:pull&service=ghcr.io" \
        | jq -r '.token'
}

# List all tags in the GHCR package, one per line.
list_tags() {
    local bearer="$1"
    curl -sf \
        -H "Authorization: Bearer ${bearer}" \
        "https://ghcr.io/v2/${GHCR_OWNER}/${GHCR_REPO}/tags/list" \
        | jq -r '.tags[]? // empty'
}

# Download the single-layer OCI blob for a tag to a destination path.
pull_blob() {
    local bearer="$1" tag="$2" dest="$3"
    local manifest blob_digest

    manifest=$(curl -sf \
        -H "Authorization: Bearer ${bearer}" \
        -H "Accept: application/vnd.oci.image.manifest.v1+json" \
        "https://ghcr.io/v2/${GHCR_OWNER}/${GHCR_REPO}/manifests/${tag}")

    blob_digest=$(echo "${manifest}" | jq -r '.layers[0].digest')

    curl -sfL \
        -H "Authorization: Bearer ${bearer}" \
        "https://ghcr.io/v2/${GHCR_OWNER}/${GHCR_REPO}/blobs/${blob_digest}" \
        -o "${dest}"
}

# Return the set of version strings already recorded in deployments.json.
deployed_versions() {
    local json="${MODELS_DIR}/deployments.json"
    [[ -f "${json}" ]] && jq -r '.[].version' "${json}" 2>/dev/null || true
}

# Append / update an entry in manifest.json (sha256, kind, size).
update_manifest() {
    local version="$1" sha256="$2" kind="$3" size="$4"
    local manifest="${MODELS_DIR}/manifest.json"
    local tmp="${manifest}.tmp"
    local existing="{}"
    [[ -f "${manifest}" ]] && existing=$(cat "${manifest}")
    echo "${existing}" | jq \
        --arg v "${version}" --arg s "${sha256}" \
        --arg k "${kind}"    --argjson sz "${size}" \
        '.[$v] = {sha256: $s, kind: $k, size: $sz}' > "${tmp}"
    mv "${tmp}" "${manifest}"
}

main() {
    local bearer
    bearer=$(ghcr_registry_token)

    local deployed
    deployed=$(deployed_versions)

    local any_new=false
    while IFS= read -r tag; do
        # Skip tags that are already in deployments.json
        if echo "${deployed}" | grep -qxF "${tag}"; then
            continue
        fi

        # Parse model name and version from tag (format: {model}-{semver})
        # "fire_smoke-v1.4"  →  model=fire_smoke  version=v1.4
        local version model kind
        version="${tag##*-}"
        model="${tag%-${version}}"
        kind=$(kind_for_model "${model}")

        if [[ -z "${kind}" ]]; then
            echo "[model-puller] Unknown model '${model}' (tag: ${tag}) — skipping"
            continue
        fi

        echo "[model-puller] New artifact: ${tag}  (kind=${kind})"

        local dest="${MODELS_DIR}/versions/${tag}.hef"
        mkdir -p "${MODELS_DIR}/versions"

        pull_blob "${bearer}" "${tag}" "${dest}"
        echo "[model-puller] Downloaded → ${dest}"

        local sha256 size
        sha256=$(sha256sum "${dest}" | cut -d' ' -f1)
        size=$(stat -c%s "${dest}")
        update_manifest "${tag}" "${sha256}" "${kind}" "${size}"

        local response
        response=$(curl -sf -X POST \
            -H "X-Deploy-Token: ${DEPLOY_TOKEN}" \
            "${BACKEND_URL}/api/deploy/${kind}/promote/${tag}")
        echo "[model-puller] Promoted: ${response}"
        any_new=true

    done < <(list_tags "${bearer}")

    ${any_new} || echo "[model-puller] No new artifacts."
}

main
