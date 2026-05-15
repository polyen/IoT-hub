.PHONY: help up-edge down-edge up-cloud down-cloud logs ps compose-check \
        evaluate evaluate-cv evaluate-stt evaluate-llm

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

up-edge-prod: ## Start edge stack on RPi5 with Hailo-8 HAT (adds /dev/hailo0 + hailo_platform mount)
	$(COMPOSE) -f $(EDGE_COMPOSE) -f hub/docker-compose.prod.yml up -d

up-edge-dev: ## Start edge + expose MQTT 1883 for mock_sensors (dev only)
	$(COMPOSE) -f $(EDGE_COMPOSE) -f hub/docker-compose.dev.yml up -d

setup-mock-certs: ## Fetch CA from RPi and generate mock-sensors client cert
	bash mock_sensors/setup_certs.sh

mock-sensors: ## Run all mock sensors against RPi (port 8883, mTLS)
	uv run python mock_sensors/run_all.py

down-edge: ## Stop and remove edge containers
	$(COMPOSE) -f $(EDGE_COMPOSE) down

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

evaluate: evaluate-cv evaluate-stt evaluate-llm ## Run full evaluation suite and aggregate report
	uv run python -m training.evaluation.report $(EVAL_FLAGS)

evaluate-cv: ## Run CV evaluation (fire/smoke mAP, fall F1, latency FPS)
	uv run python -m training.evaluation.cv_fire_smoke --dataset datasets/fire_smoke/test --output $(RESULTS_DIR) $(EVAL_FLAGS)
	uv run python -m training.evaluation.cv_fall --dataset datasets/fall_validation --output $(RESULTS_DIR) $(EVAL_FLAGS)
	uv run python -m training.evaluation.cv_latency --output $(RESULTS_DIR) $(EVAL_FLAGS)

evaluate-stt: ## Run STT latency benchmark (Hailo Whisper vs faster-whisper)
	uv run python -m training.evaluation.stt_latency --output $(RESULTS_DIR) $(EVAL_FLAGS)

evaluate-llm: ## Run LLM tool call accuracy evaluation
	uv run python -m training.evaluation.agent_accuracy --queries training/llm_eval/queries.yaml --output $(RESULTS_DIR) $(EVAL_FLAGS)
