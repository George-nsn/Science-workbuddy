.PHONY: up down logs test lint typecheck build migrate eval

eval:
	cd apps/api && python -m science_buddy.evaluation ../../data/eval/queries.jsonl

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

test:
	cd apps/api && pytest

lint:
	cd apps/api && ruff check src tests
	npm run lint:web

typecheck:
	cd apps/api && mypy src
	npm run typecheck:web

build:
	npm run build:web

migrate:
	cd apps/api && alembic upgrade head
