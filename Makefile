.PHONY: help install run test lint format check migrate downgrade revision docker-up docker-down docker-clean docker-logs docker-ps health

.DEFAULT_GOAL := help

API_URL := http://localhost:8000

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Available commands:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install project dependencies with uv
	uv sync

run: ## Run the FastAPI app locally without Docker
	uv run python -m orchestrator.main

test: ## Run the test suite
	uv run pytest -q

lint: ## Run Ruff lint checks
	uv run ruff check .

format: ## Format Python code with Ruff
	uv run ruff format .

check: lint test ## Run the standard local verification checks

migrate: ## Apply database migrations
	uv run alembic upgrade head

downgrade: ## Roll back the latest database migration
	uv run alembic downgrade -1

revision: ## Create a new Alembic autogeneration revision; pass message with m="..."
	uv run alembic revision --autogenerate -m "$(m)"

docker-up: ## Start the full Docker Compose stack in the foreground
	docker compose up -d

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
