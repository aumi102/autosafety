# 09 — Roadmap: Phase 0 and Phase 1

## Overall 6-week roadmap

```text
Week 1: Data foundation
Week 2: Text-to-SQL
Week 3: GraphRAG foundation
Week 4: Hybrid agent
Week 5: Frontend UX
Week 6: Evaluation and production hardening
```

## Phase 0 — Repository and architecture foundation

### Goal

Create a clean, runnable repo foundation with docs, Docker Compose, FastAPI skeleton, database migrations, and smoke tests.

### Deliverables

```text
README.md
docs/* architecture docs
backend FastAPI skeleton
Docker Compose: api, postgres, redis, neo4j
Alembic configured
SQLAlchemy models for source/app/domain baseline
pytest smoke tests
.env.example
Makefile or task runner
CI skeleton
```

### Done criteria

```text
docker compose up starts all services
GET /healthz returns 200
Alembic migration applies cleanly
pytest passes
README explains local setup
no secret committed
```

### Phase 0 tasks

1. Initialize repo structure.
2. Add Python backend with FastAPI.
3. Add Postgres, Redis, Neo4j in Docker Compose.
4. Add SQLAlchemy/Alembic.
5. Implement `/healthz` and `/readyz`.
6. Add base models for app/source/domain tables.
7. Add initial migration.
8. Add tests for health and DB connectivity.
9. Add docs and ADR.
10. Add CI workflow for lint/test/migration check.

## Phase 1 — NHTSA data foundation

### Goal

Ingest a constrained, real NHTSA dataset into normalized PostgreSQL tables and produce a data quality report.

### MVP scope

```text
years: 2020–2026
makes: Ford, Honda, Toyota, Tesla first; expand after data quality is stable
sources: complaints and recalls first
```

### Deliverables

```text
config/nhtsa_sources.yml
source_runs table
raw_source_rows table
ingestion CLI
complaint parser
recall parser
vehicle/component normalizer
data quality report generator
sample analytics SQL
```

### Done criteria

```text
can load complaint records for selected scope
can load recall records for selected scope
vehicles/components normalized
source_run records created
row counts and parse errors reported
sample SQL returns top components by vehicle
```

### Phase 1 commands target

```bash
make ingest-complaints SCOPE=sample
make ingest-recalls SCOPE=sample
make data-quality-report
make query-smoke
```

### Phase 1 sample SQL smoke

```sql
SELECT
  component,
  COUNT(*) AS complaint_count
FROM v_complaints_analytics
WHERE normalized_make = 'FORD'
  AND normalized_model = 'F-150'
  AND model_year = 2022
GROUP BY component
ORDER BY complaint_count DESC
LIMIT 10;
```

## Phase 1 risks

### Risk: flat file parsing mismatch

Mitigation: keep raw rows, write schema tests, report parse failures instead of crashing the entire run.

### Risk: make/model naming inconsistency

Mitigation: build a normalization table and keep original values.

### Risk: data volume too high

Mitigation: first implement `sample` mode and `scope` filters before full ingestion.

## Phase 1 output report

Create:

```text
docs/runs/phase1_ingestion_report.md
```

Required verdict:

```text
PASS: both complaints and recalls loaded with query smoke passing
PASS_WITH_WARNINGS: loaded but with non-blocking parse/null warnings
FAIL: no useful rows loaded or migrations/tests fail
```
