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
	qdrant-up qdrant-down preview-index index gold match-images migrate \
	test lint check \
	agent-api agent-worker agent-test space web web-backend web-dev studio build-web build-studio

help: ## Show commands; start with make setup
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@ / {printf "\n%s\n", substr($$0, 5)} \
		/^[a-zA-Z_-]+:.*## / {printf "  make %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Setup

setup: ## Install locked Python/frontend dependencies and Neon CLI; create .env if absent
	$(UV) sync --locked --all-packages
	$(BUN) install --frozen-lockfile
	@test -f .env || cp .env.example .env
	@echo "Configure PostgreSQL and Qdrant in .env. SigLIP 2 search needs no API key. See README.md for data setup."

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

migrate: ## Apply catalogue migrations using the direct PostgreSQL connection
	$(BACKEND_RUN) alembic -c backend/alembic.ini upgrade head

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
	$(UV) run --all-packages --extra agent --extra web pytest

lint: ## Run Python and frontend lint checks
	$(UV) run ruff check .
	$(BUN) run lint

check: test lint build ## Run Python tests, lint checks, and frontend build

##@ Image Studio

agent-api: build-studio ## Serve Image Studio independently of collection search
	$(BACKEND_RUN) --extra agent uvicorn app.services.agent.application:create_agent_app --factory --host 127.0.0.1 --port $(PORT)

agent-worker: ## Process queued image tasks inside the HF Space container
	$(BACKEND_RUN) --extra agent python -m app.services.agent.worker

agent-test: ## Test task execution, budgets, recovery, and API with fake model services
	$(UV) run --all-packages --extra agent --extra web pytest backend/tests/test_agent.py backend/tests/test_chat.py backend/tests/test_process_executor.py backend/tests/test_space.py backend/tests/test_agent_provider.py

space: build ## Serve the search edition on port 7860
	$(BACKEND_RUN) uvicorn app.main:app --host 127.0.0.1 --port 7860

##@ Collection companion (web)

build-web: ## Build search plus the collection companion
	$(BUN) run --cwd frontend build:web

web: build-web ## Serve the web edition with private Codex workers (one API process)
	EXPLORER_GATEWAY_URL=http://127.0.0.1:$(PORT) $(BACKEND_RUN) --extra web uvicorn app.web:app --host 127.0.0.1 --port $(PORT)

web-backend: ## Run the web API with reload; use make web-dev in another terminal
	EXPLORER_PUBLIC_URL=http://127.0.0.1:5173 $(BACKEND_RUN) --extra web uvicorn app.web:app --reload --host 127.0.0.1 --port 8000

web-dev: ## Run the web edition in Vite
	$(BUN) run --cwd frontend dev:web

build-studio: ## Build the deferred Image Studio prototype
	$(BUN) run --cwd frontend build:studio

studio: build-studio ## Serve the separate Image Studio prototype and its optional worker
	$(BACKEND_RUN) --extra agent python -m app.space
