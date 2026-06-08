.PHONY: help up-edge down-edge up-cloud down-cloud logs ps compose-check \
        evaluate evaluate-cv evaluate-cv-compare evaluate-stt evaluate-voice \
        evaluate-llm evaluate-npu

ENV_FILE ?= .env
EDGE_COMPOSE = hub/docker-compose.edge.yml
CLOUD_COMPOSE = hub/docker-compose.cloud.yml
COMPOSE = docker compose --env-file $(ENV_FILE)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------
up-edge: ## Start all edge services
	$(COMPOSE) -f $(EDGE_COMPOSE) up -d

up-edge-prod: ## Start edge stack on RPi5; CV + voice run as systemd services (hailo_platform/glibc conflict)
	$(COMPOSE) -f $(EDGE_COMPOSE) -f hub/docker-compose.prod.yml up -d --scale cv=0 --scale voice=0
	@echo "CV + voice run outside Docker. Install once:"
	@echo "  sudo cp scripts/iot-hub-cv.service scripts/iot-hub-voice.service /etc/systemd/system/"
	@echo "  sudo systemctl daemon-reload && sudo systemctl enable --now iot-hub-cv iot-hub-voice"

up-edge-dev: ## Start edge + expose MQTT 1883 for mock_sensors (dev only)
	$(COMPOSE) -f $(EDGE_COMPOSE) -f hub/docker-compose.dev.yml up -d

setup-mock-certs: ## Fetch CA from RPi and generate mock-sensors client cert
	bash mock_sensors/setup_certs.sh

mock-sensors: ## Run all mock sensors against RPi (port 8883, mTLS)
	uv run python mock_sensors/run_all.py

down-edge: ## Stop and remove edge containers
	$(COMPOSE) -f $(EDGE_COMPOSE) down

down-edge-prod: ## Stop edge containers and remove Hailo platform mounts (for RPi5 prod setup)
	$(COMPOSE) -f $(EDGE_COMPOSE) -f hub/docker-compose.prod.yml down

down-edge-v: ## Stop edge containers and remove volumes
	$(COMPOSE) -f $(EDGE_COMPOSE) down -v

logs-edge: ## Tail edge logs
	$(COMPOSE) -f $(EDGE_COMPOSE) logs -f

ps-edge: ## Show edge container status
	$(COMPOSE) -f $(EDGE_COMPOSE) ps

compose-check-edge: ## Validate edge compose config
	$(COMPOSE) -f $(EDGE_COMPOSE) config --quiet && echo "Edge config OK"

# ---------------------------------------------------------------------------
# Cloud
# ---------------------------------------------------------------------------
up-cloud: ## Start all cloud services
	$(COMPOSE) -f $(CLOUD_COMPOSE) up -d

down-cloud: ## Stop and remove cloud containers
	$(COMPOSE) -f $(CLOUD_COMPOSE) down

compose-check-cloud: ## Validate cloud compose config
	$(COMPOSE) -f $(CLOUD_COMPOSE) config --quiet && echo "Cloud config OK"

# ---------------------------------------------------------------------------
# Dev helpers
# ---------------------------------------------------------------------------
up-infra: ## Start only infra services (postgres, redis, mosquitto)
	$(COMPOSE) -f $(EDGE_COMPOSE) up -d postgres redis mosquitto

pre-commit: ## Run pre-commit on all files
	.venv/bin/pre-commit run --all-files

lint: ## Run ruff + black check
	.venv/bin/ruff check hub/ training/ tests/
	.venv/bin/black --check hub/ training/ tests/

typecheck: ## Run mypy
	.venv/bin/mypy hub/

test: ## Run unit tests
	.venv/bin/pytest tests/unit -q

# ---------------------------------------------------------------------------
# Evaluation (T5.1 / T5.2)
# ---------------------------------------------------------------------------
EVAL_FLAGS ?=
RESULTS_DIR ?= materials/evaluation_results

evaluate: evaluate-cv evaluate-stt evaluate-voice evaluate-llm ## Run full evaluation suite and aggregate report
	uv run python -m training.evaluation.report $(EVAL_FLAGS)

# CV_MODEL: fine-tuned detector .pt (enables mAP@.5-.95 via YOLO.val()).
# CV_DATA_YAML: Ultralytics data.yaml for the val() split.
CV_MODEL ?=
CV_DATA_YAML ?= datasets/fire_smoke/data.yaml

evaluate-cv: ## Run CV evaluation (fire/smoke mAP@.5-.95, fall F1, latency FPS)
	uv run python -m training.evaluation.cv_fire_smoke --dataset datasets/fire_smoke/test --model "$(CV_MODEL)" --data-yaml "$(CV_DATA_YAML)" --output $(RESULTS_DIR) $(EVAL_FLAGS)
	uv run python -m training.evaluation.cv_fall --dataset datasets/fall_validation --output $(RESULTS_DIR) $(EVAL_FLAGS)
	uv run python -m training.evaluation.cv_latency --output $(RESULTS_DIR) $(EVAL_FLAGS)

evaluate-cv-compare: ## P1.1: Comparative YOLO26 vs YOLOv11 vs YOLOv8 on Hailo-8 (needs HEFs on RPi 5)
	uv run python -m training.evaluation.cv_detector_compare \
		--config materials/evaluation_results/cv_detector_compare/config.yaml \
		--dataset datasets/fire_smoke_mixed/test \
		--output materials/evaluation_results/cv_detector_compare $(EVAL_FLAGS)

evaluate-stt: ## Run STT WER/CER on the UA corpus + latency benchmark
	uv run python -m training.evaluation.stt_wer --output $(RESULTS_DIR) $(EVAL_FLAGS)
	uv run python -m training.evaluation.stt_latency --output $(RESULTS_DIR) $(EVAL_FLAGS)

evaluate-voice: ## End-to-end voice latency (STT → intent) against the 5s NFR-2 budget
	uv run python -m training.evaluation.voice_e2e_latency --output $(RESULTS_DIR) $(EVAL_FLAGS)

evaluate-npu: ## P1.2: NPU contention — CV FPS alone vs CV + STT-on-NPU (needs Hailo + HEF=...)
	uv run python -m training.evaluation.npu_contention --hef $(HEF) --output $(RESULTS_DIR) $(EVAL_FLAGS)

evaluate-llm: ## Run LLM tool call accuracy evaluation
	uv run python -m training.evaluation.agent_accuracy --queries training/llm_eval/queries.yaml --output $(RESULTS_DIR) $(EVAL_FLAGS)
