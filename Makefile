.PHONY: help install run test lint format check docker-config docker-build docker-up docker-up-detached docker-down docker-clean docker-logs docker-ps health

.DEFAULT_GOAL := help

APP_MODULE := orchestrator.main:app
API_URL := http://localhost:8000

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Available commands:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install project dependencies with uv
	uv sync

run: ## Run the FastAPI app locally without Docker
	uv run uvicorn $(APP_MODULE) --host 0.0.0.0 --port 8000 --reload

test: ## Run the test suite
	uv run pytest -q

lint: ## Run Ruff lint checks
	uv run ruff check .

format: ## Format Python code with Ruff
	uv run ruff format .

check: lint test ## Run the standard local verification checks

docker-up: ## Start the full Docker Compose stack
	docker compose up -d --build

docker-down: ## Stop the Docker Compose stack
	docker compose down

docker-clean: ## Stop the stack and remove volumes
	docker compose down -v

docker-logs: ## Follow Docker Compose logs
	docker compose logs -f

docker-ps: ## Show Docker Compose service status
	docker compose ps

health: ## Call the local API health endpoint
	curl $(API_URL)/healthz
