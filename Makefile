.DEFAULT_GOAL := help

# Also expose the workspace's local tools to nested package scripts.
export PATH := $(PATH):$(CURDIR)/data/tools/node_modules/.bin

UV ?= uv
BUN ?= $(shell command -v bun 2>/dev/null || printf '%s' '$(CURDIR)/data/tools/node_modules/.bin/bun')
PORT ?= 8000
LIMIT ?=
SCAN_LIMIT ?=
WORKERS ?= 10
BATCH_SIZE ?= 10
INDEX_ARGS ?=

BACKEND_RUN = $(UV) run --package multimodal-backend
INDEX_COMMAND = $(BACKEND_RUN) cronjob/index_images.py $(if $(strip $(LIMIT)),--limit $(LIMIT)) $(if $(strip $(SCAN_LIMIT)),--scan-limit $(SCAN_LIMIT)) --workers $(WORKERS) --batch-size $(BATCH_SIZE) $(INDEX_ARGS)

.PHONY: help setup \
	build run backend dev generate-client \
	qdrant-up qdrant-down preview-index index gold match-images \
	test lint check \
	agent-api agent-worker agent-test agent-lock agent-deploy

help: ## Show commands; start with make setup
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@ / {printf "\n%s\n", substr($$0, 5)} \
		/^[a-zA-Z_-]+:.*## / {printf "  make %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Setup

setup: ## Install locked Python/frontend dependencies and Neon CLI; create .env if absent
	$(UV) sync --locked --all-packages
	$(BUN) install --frozen-lockfile
	@test -f .env || cp .env.example .env
	@echo "Set GEMINI_API_KEY in .env before indexing or searching. See README.md for data setup."

##@ Application

build: ## Type-check and build the frontend
	$(BUN) run build

run: build ## Build and serve the app (default port 8000; override with PORT=8001)
	$(BACKEND_RUN) uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

backend: ## Run the API with reload on port 8000 (foreground)
	$(BACKEND_RUN) uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev: ## Run Vite on port 5173; run make backend in another terminal
	$(BUN) run dev

generate-client: ## Regenerate the frontend API client from the backend schema
	bash scripts/generate-client.sh

##@ Collection data and indexing

qdrant-up: ## Start the local vector database (requires Docker)
	docker compose up -d qdrant

qdrant-down: ## Stop Qdrant, keeping its stored data
	docker compose stop qdrant

preview-index: ## Preview the sample without embedding requests or Qdrant writes
	$(INDEX_COMMAND) $(if $(strip $(LIMIT)),,--limit 50) --dry-run

index: ## Reuse saved selection; set LIMIT to select a new sample
	$(INDEX_COMMAND)

gold: ## Convert the official silver CSV to gold Parquet
	$(UV) run cronjob/silver_to_gold.py

match-images: ## Create the optional image manifest and coverage report
	$(UV) run cronjob/match_images.py

##@ Validation

test: ## Run Python tests; requires a disposable TEST_POSTGRES_URL
	$(UV) run pytest

lint: ## Run Python and frontend lint checks
	$(UV) run ruff check .
	$(BUN) run lint

check: test lint build ## Run Python tests, lint checks, and frontend build

##@ Image Studio

agent-api: build ## Serve Image Studio independently of collection search
	$(BACKEND_RUN) uvicorn app.services.agent.application:create_agent_app --factory --host 127.0.0.1 --port $(PORT)

agent-worker: ## Process queued image tasks using Modal sandboxes
	$(BACKEND_RUN) python -m app.services.agent.worker

agent-test: ## Test agent isolation, budgets, recovery, and API with fake cloud services
	$(UV) run pytest backend/tests/test_agent.py backend/tests/test_chat.py

agent-lock: ## Export locked dependencies for the Modal images
	$(UV) export --only-group agent-runtime --no-emit-project --no-hashes --no-header --output-file deploy/agent-requirements.txt
	$(UV) export --package multimodal-backend --no-dev --no-emit-workspace --no-hashes --no-header --output-file deploy/service-requirements.txt

agent-deploy: build ## Deploy configured cloud creation services to Modal
	$(BACKEND_RUN) modal deploy deploy/modal_app.py
