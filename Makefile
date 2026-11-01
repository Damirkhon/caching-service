.DEFAULT_GOAL := help
# Every uv command fails on a stale uv.lock instead of silently re-locking it.
export UV_LOCKED := 1
.PHONY: help install fmt lint typecheck test check run migrate up down smoke

help: ## List every target
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Create .venv and install the locked dependencies
	uv sync

fmt: ## Auto-format and apply safe lint fixes
	uv run ruff check --fix .
	uv run ruff format .

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Type-check with mypy --strict
	uv run mypy

test: ## Run the tests with the coverage gate
	uv run pytest --cov

check: lint typecheck test ## The gate every commit must pass

run: ## Run the service locally with auto-reload
	uv run uvicorn --factory caching_service.main:create_app --reload

migrate: ## Apply database migrations
	uv run alembic upgrade head

up: ## Build and start the Docker Compose stack, returning once it is healthy
	docker compose up --build --detach --wait

down: ## Stop the Docker Compose stack
	docker compose down

smoke: ## End-to-end smoke test against the Compose stack
	bash scripts/smoke.sh
