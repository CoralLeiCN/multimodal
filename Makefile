.DEFAULT_GOAL := help

# Also expose the workspace's local tools to nested package scripts.
export PATH := $(PATH):$(CURDIR)/data/tools/node_modules/.bin

UV ?= uv
PORT ?= 8000
BUN ?= $(shell command -v bun 2>/dev/null || printf '%s' '$(CURDIR)/data/tools/node_modules/.bin/bun')
LIMIT ?= 50
SCAN_LIMIT ?= 1000
INDEX_ARGS ?=

.PHONY: help setup qdrant-up qdrant-down preview-index index build run backend dev test lint check generate-client gold match-images

help: ## Show commands; start with make setup
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  make %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install locked Python/frontend dependencies and create .env if absent
	$(UV) sync --locked --all-packages
	$(BUN) install --frozen-lockfile
	@test -f .env || cp .env.example .env
	@echo "Set GEMINI_API_KEY in .env before indexing or searching. See README.md for data setup."

qdrant-up: ## Start the local vector database (requires Docker)
	docker compose up -d qdrant

qdrant-down: ## Stop Qdrant, keeping its stored data
	docker compose stop qdrant

preview-index: ## Preview the sample without embedding requests or Qdrant writes
	$(UV) run --package multimodal-backend cronjob/index_images.py --limit $(LIMIT) --scan-limit $(SCAN_LIMIT) $(INDEX_ARGS) --dry-run

index: ## Index the sample through Gemini; requires .env, local data, and Qdrant
	$(UV) run --package multimodal-backend cronjob/index_images.py --limit $(LIMIT) --scan-limit $(SCAN_LIMIT) $(INDEX_ARGS)

build: ## Type-check and build the frontend
	$(BUN) run build

run: build ## Build and serve the app (default port 8000; override with PORT=8001)
	$(UV) run --package multimodal-backend uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

backend: ## Run the API with reload on port 8000 (foreground)
	$(UV) run --package multimodal-backend uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev: ## Run Vite on port 5173; run make backend in another terminal
	$(BUN) run dev

test: ## Run Python tests with isolated services and no Gemini requests
	$(UV) run pytest

lint: ## Run Python and frontend lint checks
	$(UV) run ruff check .
	$(BUN) run lint

check: test lint build ## Run Python tests, lint checks, and frontend build

generate-client: ## Regenerate the frontend API client from the backend schema
	bash scripts/generate-client.sh

gold: ## Convert the official silver CSV to gold Parquet
	$(UV) run cronjob/silver_to_gold.py

match-images: ## Create the optional image manifest and coverage report
	$(UV) run cronjob/match_images.py
