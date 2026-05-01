.PHONY: help up-edge down-edge up-cloud down-cloud logs ps compose-check

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
