# IoT Smart Home Hub

Local-first, privacy-preserving IoT hub combining computer vision, voice control, and an
LLM agent — all running on edge hardware (Raspberry Pi 5 + Hailo-8 NPU), with an optional
cloud VPS replica.

- **CV** — YOLO26n detection (person / fire / smoke) → ByteTrack → YOLO-pose → fall
  detection → ArcFace identity, all on the Hailo-8 NPU.
- **Voice** — Silero VAD → openWakeWord → Ukrainian STT (**Moonshine-base-uk** ONNX) →
  intent → TTS (piper).
- **Agent** — a lightweight **SetFit INT8 ONNX intent classifier** (head over
  `multilingual-e5-small`, conf-gated at 0.6) routes commands to deterministic tools, a
  GBNF-grammar-constrained local LLM, or a free-form Qwen 2.5 3B/1.5B; the LLM is deliberately
  kept off the latency-critical path (measured 99.5 % / ~58 ms classifier vs 60–200 s LLM on
  RPi5). Every tool call is gated by a policy engine (AUTO / CONFIRM / DENY).

Data is classified into tiers T0–T3; raw frames/audio (T0) never leave the LAN. Privacy is
enforced at the application layer, not by network isolation.

For setup and operational details see the guides under [`docs/`](docs/) and the
per-component READMEs (e.g. [`scripts/edge-bootstrap-README.md`](scripts/edge-bootstrap-README.md)).

## Repository layout

```
hub/
  backend/      # FastAPI app (routes, SQLAlchemy/Timescale, Alembic, deploy API)
  edge/         # cv / voice / agent / mlops / storage / sync pipelines
  web/          # React 18 + Vite PWA (floorplan editor, camera streams)
  esp32/        # ESPHome sensor firmware
  cloud/        # VPS-side bridge, Telegram bot, ntfy
training/       # model training + ONNX→HEF conversion (laptop / Colab / Kaggle)
mock_sensors/   # MQTT sensor simulators for local development
scripts/        # deploy-model.sh, model-puller, systemd units, helpers
docs/           # setup & operational guides (datasets, HEF compilation, voice)
tests/          # unit / integration / chaos
.github/workflows/   # CI/CD
```

## Quick start (development)

```bash
# Requires Python 3.11+ and uv (https://github.com/astral-sh/uv)
uv sync --extra dev          # optional extras: training, voice
pre-commit install
cp .env.example .env         # required for any compose / deploy command

make lint                    # ruff check + black --check
make typecheck               # mypy --strict on hub/
make test                    # unit tests

# single test:
.venv/bin/pytest tests/unit/test_orchestrator.py::test_name -q
# integration tests (require docker / postgres) are opt-in:
.venv/bin/pytest tests/integration -m integration
```

### Running the stack (compose, driven by `.env`)

```bash
make up-infra        # postgres + redis + mosquitto only
make up-edge         # full edge stack on the RPi5
make up-edge-dev     # + dev override (exposes MQTT 1883 for mock_sensors)
make up-edge-prod    # prod: cv & voice run as host systemd services, not containers
make up-cloud        # VPS-side stack
make mock-sensors    # simulate ESP32 sensors against the hub (8883 mTLS)
make logs-edge / make ps-edge
```

> **Prod note:** in `up-edge-prod` the `cv` and `voice` containers are scaled to 0 and run
> as host systemd services (`scripts/iot-hub-cv.service`, `scripts/iot-hub-voice.service`)
> due to a `hailo_platform`/glibc conflict. `docker compose up -d cv` is a no-op there — use
> `sudo systemctl restart iot-hub-cv`. Both processes share the single Hailo-8 via
> `group_id="SHARED"` + the HailoRT arbiter (`sudo systemctl enable --now hailort.service`);
> see [`scripts/edge-bootstrap-README.md`](scripts/edge-bootstrap-README.md) for the full setup.

## Deployment (bare hardware → running hub)

End-to-end runbook for provisioning a fresh Raspberry Pi 5 + Hailo-8 into a running hub. Each
stage is idempotent; see [`scripts/edge-bootstrap-README.md`](scripts/edge-bootstrap-README.md)
for the long-form walkthrough.

**Stage 1 — Provision the edge node** (once, on the RPi5, as root):

```bash
sudo bash scripts/edge-bootstrap.sh     # Docker, /mnt/ssd layout, /opt/iot-hub, dir tree
# Then install HailoRT + the multi-process arbiter so cv & voice can share the NPU:
sudo systemctl enable --now hailort.service
```

**Stage 2 — Certificates** (mTLS for MQTT + TLS for the web UI):

```bash
bash scripts/gen-mqtt-certs.sh                      # CA + broker cert → hub/mosquitto/certs/
bash scripts/gen-mqtt-certs.sh --device esp32-salon # one client cert per device
bash scripts/gen-web-cert.sh                        # self-signed web UI cert
```

**Stage 3 — Configure and bring up the stack:**

```bash
cp .env.example .env        # fill POSTGRES_PASSWORD, DEPLOY_TOKEN, GRAFANA_ADMIN_PASSWORD, …
make up-edge-prod           # full stack; cv & voice run as host systemd services
sudo cp scripts/iot-hub-cv.service scripts/iot-hub-voice.service /etc/systemd/system/
sudo systemctl enable --now iot-hub-cv iot-hub-voice
```

**Stage 4 — Enable automatic model delivery** (GHCR → RPi5 promote):

```bash
sudo cp scripts/model-puller.{sh,service,timer} /etc/systemd/system/   # adjust paths
sudo systemctl enable --now model-puller.timer    # polls GHCR every 5 min, promotes new HEFs
```

**Stage 5 — Enable code auto-update** (optional, pulls latest `main`):

```bash
sudo bash scripts/install-updater.sh              # installs iot-hub-updater systemd timer
```

**Stage 6 — Cloud VPS replica** (optional, off-site mirror):

```bash
make up-cloud                                  # bridge + PG subscriber + Telegram bot + ntfy
bash scripts/setup-vps-replication.sh          # one-time logical-replication subscription
```

Day-to-day: `make logs-edge`, `make ps-edge`, `journalctl -u iot-hub-cv -f`,
`journalctl -u model-puller -f`.

## Tooling

| Tool | Purpose |
|------|---------|
| [uv](https://github.com/astral-sh/uv) | Python env & dependency management |
| [ruff](https://docs.astral.sh/ruff/) | Lint |
| [black](https://black.readthedocs.io/) | Format (line length 100) |
| [mypy](https://mypy.readthedocs.io/) | Static type-check (`--strict` on `hub/`) |
| [pytest](https://pytest.org/) + pytest-asyncio | Tests (`asyncio_mode = auto`) |
| [pre-commit](https://pre-commit.com/) | Git hooks (ruff, black, mypy, dataset-guard) |
| [DVC](https://dvc.org/) | Dataset & model versioning (GDrive remote) |
| [MLflow](https://mlflow.org/) | Experiment tracking (`http://localhost:5001`) |

## Scripts

All operational scripts live in `scripts/`. Run them from the repo root.

**Edge provisioning & lifecycle**

| Script | Purpose |
|--------|---------|
| `edge-bootstrap.sh` | Idempotent RPi5 installer: Docker, `/mnt/ssd` layout, `/opt/iot-hub` symlink, project dir tree, seed manifests. Run once as root. |
| `install-updater.sh` | Installs the `iot-hub-updater` systemd timer (auto-pull `main`). Run after cloning. |
| `edge-updater.sh` | The updater body — pulls latest `main` and restarts changed containers (invoked by the timer). |
| `iot-hub-cv.service` / `iot-hub-voice.service` | systemd units for the CV / voice pipelines (host services in prod — Hailo/glibc workaround). |

**Security / certificates**

| Script | Purpose |
|--------|---------|
| `gen-mqtt-certs.sh` | Self-signed CA + broker + per-device certs for **mTLS MQTT on 8883**. `--device NAME` per client. |
| `gen-web-cert.sh` | Self-signed TLS cert for the web UI (valid for `HUB_DOMAIN` + local IPs). |

**Model lifecycle (train → compile → deploy)**

| Script | Purpose |
|--------|---------|
| `deploy-model.sh` | One-shot `.pt` → ONNX → DVC backup → **HEF compile (Hailo DFC)** → GHCR push. See [below](#deploying-a-trained-model-to-the-edge). |
| `model-puller.{sh,service,timer}` | RPi5 systemd timer: polls GHCR every 5 min, downloads new HEFs, calls the backend deploy API to promote atomically. |
| `record-wake-word.py` | Records samples to train a custom openWakeWord activation phrase. |

**Cloud / replication**

| Script | Purpose |
|--------|---------|
| `setup-vps-replication.sh` | One-time VPS setup: creates the logical-replication subscription from edge Postgres (filtered `WHERE tier >= 1`). |

**Benchmarking & evaluation**

| Script | Purpose |
|--------|---------|
| `llm_bench_all.sh` / `run_bench_phase0.sh` | LLM model comparison on the RPi5 (Qwen 2.5 3B vs candidates, CV active). |
| `bench_compare.py` | Renders an LLM benchmark comparison markdown from two result JSONs. |
| `test-stt.sh` | Quick STT smoke test — records 5 s and transcribes. |
| `wifi_stream_sender.sh` | Pushes a local camera to the RPi `mediamtx` RTSP server (dev without a fixed IP camera). |

**Dev / CI helpers**

| Script | Purpose |
|--------|---------|
| `check_datasets.py` | Pre-commit guard — fails if a staged `datasets/` file isn't DVC-tracked (keeps T0 off the remote). |

> The same flows are also exposed as `make` targets — run `make help` for the full list
> (`up-edge*`, `down-edge*`, `mock-sensors`, `evaluate*`, `lint`, `typecheck`, `test`).

## Model training & deployment

Training is reproducible via DVC stages (`dvc.yaml`) with hyperparameters in `params.yaml`.

```bash
dvc repro                    # run all stages (wake_word, fire_smoke, mining, evaluate)
# or a single architecture, tracked as an MLflow run:
dvc exp run -S train.fire_smoke.base_model=yolo26n.pt -S train.fire_smoke.name=train
make evaluate                # CV + STT + LLM eval suites → materials/evaluation_results
```

The fire/smoke detector is **YOLO26n**, 3 classes `{0:person, 1:fire, 2:smoke}`, trained on
the COCO-person + D-Fire mixed dataset. Trained weights land in
`runs/fire_smoke/<name>/weights/best.pt`.

### Deploying a trained model to the edge

A trained `.pt` becomes a running NPU model in one command — `scripts/deploy-model.sh` handles
ONNX export → DVC backup → HEF compilation → GHCR push. The RPi5 then pulls and promotes it
automatically.

**Prerequisites on the build machine** (x86_64 Ubuntu, or macOS via `linux/amd64` emulation):

- `docker`, `dvc`, `oras`, and the Hailo DFC image loaded
  (`docker load -i hailo8_ai_sw_suite_*.tar.gz`).
- `.env` with `GHCR_OWNER` and `GHCR_TOKEN` (a GitHub PAT with `write:packages`).

```bash
scripts/deploy-model.sh --model fire_smoke --version v1.4
```

The script:

1. **Exports ONNX** from `runs/fire_smoke/train/weights/best.pt` (reuses `best.onnx` if present)
   and copies it to `models/onnx/fire_smoke_v1.4.onnx`.
2. **`dvc add` + `dvc push`** — versioned backup of the ONNX to the GDrive remote, then
   `git commit`s the `.dvc` pointer.
3. **Compiles the HEF** via the Hailo DFC Docker image. For YOLO26 the graph is cut into
   **separate box + class outputs** (`--end-nodes /model.23/Mul_2,/model.23/Sigmoid`,
   auto-set for `fire_smoke`) and quantized using calibration images from
   `datasets/fire_smoke_mixed/valid/images`. A single concatenated output would collapse all
   class scores to zero — the detector would load but never fire.
4. **Pushes the HEF** to `ghcr.io/<owner>/iot-hub-models:fire_smoke-v1.4` as an OCI artifact.

Other kinds use the same flow: `--model pose|face|whisper` (the tag prefix maps to deploy
`kind`s `yolo|pose|face|whisper`).

**On the RPi5**, the `model-puller` systemd timer polls GHCR every 5 minutes
(`scripts/model-puller.{sh,service,timer}`), downloads any new tag into
`/opt/iot-hub/models/versions/`, updates `manifest.json`, and calls the backend deploy API to
promote it. Promotion atomically swaps the `current_yolo.hef` symlink, runs a smoke test,
records `deployments.json`, and signals the CV pipeline to hot-reload (no stream drop).
It needs `GHCR_OWNER`, `GHCR_TOKEN` (`read:packages`), and `DEPLOY_TOKEN` in its environment.

```bash
journalctl -u model-puller -f                          # watch the puller on the RPi
curl -s localhost:8000/api/deploy/yolo/status | jq     # current version + history

# Manual promote / rollback (X-Deploy-Token = DEPLOY_TOKEN):
curl -X POST -H "X-Deploy-Token: $DEPLOY_TOKEN" \
  localhost:8000/api/deploy/yolo/promote/fire_smoke-v1.4
curl -X POST -H "X-Deploy-Token: $DEPLOY_TOKEN" \
  localhost:8000/api/deploy/yolo/rollback
```

### Troubleshooting: expired DVC (GDrive) token

The DVC GDrive remote uses OAuth user credentials, whose refresh token Google expires/revokes
periodically (≈7 days for unverified apps). `dvc push` then fails with:

```
ERROR: Failed to authenticate GDrive: Access token refresh failed:
invalid_grant: Token has been expired or revoked.
```

Delete the cached token and re-authenticate interactively (a browser window opens for consent):

```bash
# macOS — <client_id> is gdrive_client_id from .dvc/config
rm -f "$HOME/Library/Caches/pydrive2fs/<client_id>/default.json"
# Linux:
rm -f "$HOME/.cache/pydrive2fs/<client_id>/default.json"

dvc push          # opens the browser; complete the OAuth consent
```

A fresh token is written back to `default.json`. Then re-run `scripts/deploy-model.sh …` — it
will skip the already-created `.dvc` pointer and push successfully. Run `dvc push` in a real
terminal (not a captured subprocess) so the consent URL / browser is visible.

## CI/CD

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `ci.yml` | PR / push to main | lint, typecheck, unit tests, multi-arch docker build dry-run |
| `cd-vps.yml` | push to main (`hub/backend/**`, `hub/web/**`) | build + push to GHCR, SSH deploy to VPS |
| `cd-edge.yml` | push to main (`hub/edge/**`) | build arm64 image, push to GHCR (Watchtower pulls on RPi5) |
| `compile-hef.yml` | manual (`workflow_dispatch`) | reference only — HEF compilation runs locally via `scripts/deploy-model.sh` (the 17.5 GB Hailo DFC image is too large for CI runners) |

### Required GitHub Secrets

Set these under **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `VPS_HOST` | IP or hostname of the VPS |
| `VPS_USER` | SSH username on the VPS (e.g. `deploy`) |
| `VPS_SSH_KEY` | Private SSH key (RSA/Ed25519) for VPS access |
| `GHCR_PAT` | GitHub PAT with `read:packages` scope (used by VPS to pull images) |

> `GITHUB_TOKEN` is automatically provided by Actions and used for pushing images to GHCR — no
> manual setup needed. The `GHCR_OWNER` / `GHCR_TOKEN` used by `deploy-model.sh` and the RPi
> `model-puller` are **local env / `.env` values**, not GitHub Secrets.

### GitHub Environments

Create two environments in **Settings → Environments**:

- **`vps`** — used by `cd-vps.yml`; optionally add required reviewers
- **`edge`** — used by `cd-edge.yml`; **add required reviewers** (deployment to physical hardware needs human approval)
