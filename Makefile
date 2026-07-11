.PHONY: install dev test lint clean db-up db-down db-reset migrate

install:
	pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload --port 8000

test:
	pytest tests/ -v

lint:
	ruff check autosafety/

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

db-up:
	docker compose up postgres redis neo4j -d

db-down:
	docker compose down

db-reset: db-down
	rm -rf postgres_data redis_data neo4j_data
	docker compose up -d

migrate:
	alembic upgrade head

migrate-create:
	alembic revision --autogenerate -m "$(MSG)"

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-build:
	docker compose build
