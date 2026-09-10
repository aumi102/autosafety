.PHONY: install dev test lint format format-check lint-fix typecheck evaluate check clean \
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

PYTHON ?= python
LINT_PATHS = app/ tests/ scripts/ migrations/
# migrations/ is linted but never reformatted: an applied revision is a record
# of what ran, not a file to restyle.
FORMAT_PATHS = app/ tests/ scripts/

lint:
	ruff check $(LINT_PATHS)

# Formatting gate. `ruff format` is the repository's formatter as of the Phase 11
# completion pass; this fails if anything is unformatted.
format-check:
	ruff format --check $(FORMAT_PATHS)

format:
	ruff format $(FORMAT_PATHS)

# Safe fixes only. Never pass --unsafe-fixes here.
lint-fix:
	ruff check $(LINT_PATHS) --fix

# Enforced. Covers every first-party tree under the strictness policy in
# pyproject.toml (including disallow_untyped_defs for app/). Expected to stay at
# zero errors.
typecheck:
	mypy app/ scripts/ tests/

# Phase 7 and Phase 8 safety evaluations. docs/phase7_closeout_report.md makes
# these mandatory CI gates. Both are fully offline: no database, no graph, no
# external provider, no credential.
evaluate:
	python scripts/evaluate_phase7_answers.py
	python scripts/evaluate_phase8_conversations.py

# The canonical pre-commit gate. It delegates to scripts/check.py rather than
# repeating the commands, so the Makefile, CI, and a developer without `make`
# all run exactly the same gates. Nothing here needs Docker, a secret, or a
# paid provider.
check:
	$(PYTHON) scripts/check.py

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
