# HEF Compilation

Hailo Executable Format (HEF) files are compiled from ONNX models using the
Hailo Dataflow Compiler (DFC), which runs only on **x86_64 Ubuntu 22.04**.

## Manual trigger

```bash
gh workflow run compile-hef.yml -f model=fire_smoke -f version=v1.4
```

## What the workflow does

1. Checks out the repo and installs Python deps via uv
2. Pulls the calibration data subset from DVC (Google Drive)
3. Downloads the registered `.onnx` from MLflow model registry
4. Runs the Hailo DFC Docker image to convert ONNX → HAR → HEF
5. Uploads the HEF back to MLflow as a run artifact
6. Pushes the HEF as an OCI artifact to GHCR (`ghcr.io/<owner>/iot-hub-models`)
7. Sends a `repository_dispatch` event to trigger a watchtower pull on the edge

## Required GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `GDRIVE_SERVICE_ACCOUNT_JSON` | GCP service account key for DVC pull |
| `MLFLOW_TRACKING_URI` | URI of the MLflow tracking server |
| `MLFLOW_TOKEN` | Bearer token for MLflow REST API |
| `GHCR_PAT` | GitHub PAT with `write:packages` scope |
| `EDGE_DISPATCH_TOKEN` | GitHub PAT to send repository_dispatch to edge |

## Local conversion (x86_64 Ubuntu only)

Install Hailo SDK:

```bash
# Download hailo_sdk_client wheel from hailo.ai/developer-zone/
pip install hailo_sdk_client-*.whl
```

Then run:

```bash
python training/convert_to_hef.py \
    --onnx path/to/model.onnx \
    --out ./hef_output \
    --model-name fire_smoke
```

In CI mode (prints JSON artifact paths):

```bash
python training/convert_to_hef.py \
    --onnx model.onnx --out ./hef_output \
    --model-name fire_smoke --ci
```
