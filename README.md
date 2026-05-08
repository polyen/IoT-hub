# IoT Smart Home Hub

Local-first, privacy-preserving IoT hub combining computer vision, voice control, and an LLM agent — all running on edge hardware (Raspberry Pi 5 + Hailo-8 NPU).

For full architecture, design decisions, and data tier classification see [materials/iot_hub_project_summary.md](materials/iot_hub_project_summary.md).

## Repository layout

```
hub/            # main Python package (edge + backend + web sub-packages)
training/       # model training scripts (runs on laptop / Colab)
tests/
  unit/
  integration/
  chaos/
.github/
  workflows/    # GitHub Actions CI/CD
```

## Quick start (development)

```bash
# Requires Python 3.11+ and uv (https://github.com/astral-sh/uv)
uv sync --extra dev
pre-commit install
pytest
```

## Tooling

| Tool | Purpose |
|------|---------|
| [ruff](https://docs.astral.sh/ruff/) | Lint |
| [black](https://black.readthedocs.io/) | Format |
| [mypy](https://mypy.readthedocs.io/) | Static type-check |
| [pytest](https://pytest.org/) + pytest-asyncio | Tests |
| [pre-commit](https://pre-commit.com/) | Git hooks |

## CI/CD

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `ci.yml` | PR / push to main | lint, typecheck, unit tests, docker build dry-run |
| `cd-vps.yml` | push to main (`hub/backend/**`, `hub/web/**`) | build + push to GHCR, SSH deploy to VPS |
| `cd-edge.yml` | push to main (`hub/edge/**`) | build arm64 image, push to GHCR (Watchtower pulls on RPi5) |

### Required GitHub Secrets

Set these under **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `VPS_HOST` | IP or hostname of the VPS |
| `VPS_USER` | SSH username on the VPS (e.g. `deploy`) |
| `VPS_SSH_KEY` | Private SSH key (RSA/Ed25519) for VPS access |
| `GHCR_PAT` | GitHub Personal Access Token with `read:packages` scope (used by VPS to pull images) |

> `GITHUB_TOKEN` is automatically provided by Actions and used for pushing images to GHCR — no manual setup needed.

### GitHub Environments

Create two environments in **Settings → Environments**:

- **`vps`** — used by `cd-vps.yml`; optionally add required reviewers
- **`edge`** — used by `cd-edge.yml`; **add required reviewers** (deployment to physical hardware needs human approval)
