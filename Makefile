.PHONY: install test test-int lint fmt type check db-up db-down migrate serve

install:
	uv sync --all-groups

test:
	uv run pytest -m "not integration"

test-int:
	uv run pytest -m integration

test-all:
	uv run pytest

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests && uv run ruff check --fix src tests

type:
	uv run mypy

check: lint type test

db-up:
	@bash scripts/docker-preflight.sh || true
	docker compose up -d db

db-down:
	docker compose down

migrate:
	uv run alembic upgrade head

serve:
	uv run uvicorn pitchprob.api.main:app --reload
