.PHONY: install dev test lint lint-all lint-fix typecheck check clean \
        db-up db-down db-reset migrate migrate-create \
        docker-up docker-down docker-build

# ---------------------------------------------------------------- development

install:
	pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload --port 8000

test:
	pytest tests/ -v

# ------------------------------------------------------------------- quality
#
# `lint` is the enforced gate and is expected to stay green. It covers every
# first-party tree, including `migrations/`, whose narrow per-file exemptions
# are declared and justified in pyproject.toml.
#
# It previously read `ruff check autosafety/` — a directory that has never
# existed in this repository — so it silently checked nothing at all. See
# docs/phase10_design.md.

LINT_PATHS = app/ tests/ scripts/ migrations/

lint:
	ruff check $(LINT_PATHS)

# Advisory: adds the line-length rule excluded from the enforced gate. Expected
# to report findings; not a blocker. Baseline count in docs/phase10_design.md.
lint-all:
	ruff check $(LINT_PATHS) --extend-select E501

# Safe fixes only. Never pass --unsafe-fixes here.
lint-fix:
	ruff check $(LINT_PATHS) --fix

# Advisory. mypy was configured in pyproject.toml from the start but no target
# ever invoked it, so it had never run: it reports 47 pre-existing errors across
# 16 files. Baselined in docs/phase10_design.md and deliberately kept out of
# `check` until that debt is paid, rather than weakening the mypy settings to
# manufacture a green result.
typecheck:
	mypy app/

# What CI and a pre-push check should run. Both parts are expected to pass.
check: lint test

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# -------------------------------------------------------------- infrastructure

db-up:
	docker compose up postgres redis neo4j -d

db-down:
	docker compose down

# DESTRUCTIVE: deletes the postgres_data, redis_data, and neo4j_data volumes.
#
# This target used to run `rm -rf postgres_data redis_data neo4j_data`, which
# removed local directories that do not exist: docker-compose.yml declares
# *named volumes*. The command was a silent no-op, so `make db-reset` restarted
# the stack with every row still in place while reporting success. An operator
# who trusted it would debug against data they believed had been wiped.
#
# It now really does reset, and therefore requires explicit confirmation.
db-reset:
	@echo "This DELETES all PostgreSQL, Redis, and Neo4j data (named volumes)."
	@echo "Re-run with CONFIRM=yes to proceed:  make db-reset CONFIRM=yes"
	@test "$(CONFIRM)" = "yes"
	docker compose down -v
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
