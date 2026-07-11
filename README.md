# AutoSafety GraphSQL Copilot

Production-grade analyst copilot for exploring public vehicle-safety data from NHTSA.

The product combines:

- **Text-to-SQL** for structured analytics over complaints, recalls, investigations, and manufacturer communications.
- **GraphRAG** for vehicle → component → complaint/recall/investigation/communication evidence traversal.
- **Evidence-first answer synthesis** with SQL, tables, citations, graph paths, confidence, and caveats.

## Product one-liner

AutoSafety GraphSQL Copilot is a hybrid GraphRAG and Text-to-SQL analyst for investigating vehicle safety complaints, recalls, defect investigations, and manufacturer communications using official public NHTSA data.

## Initial MVP thesis

The MVP should prove one hard workflow end-to-end:

> Find 2020–2026 vehicle models with high or rising component-specific complaints, then check whether public NHTSA recall, investigation, or manufacturer communication records contain related evidence.

This forces the system to demonstrate SQL analytics, entity extraction, graph traversal, semantic retrieval, citations, and safe answer synthesis.

## Non-negotiable product rules

1. **Complaint volume is not defect proof.** The product must never claim that complaints prove a defect or official causality.
2. **Matched evidence must be labeled.** A relation discovered by make/model/year/component/semantic similarity is only `potentially_related`, not `officially_caused_by`.
3. **SQL is read-only.** The agent may execute only safe `SELECT`/`WITH` statements against a read-only database user.
4. **Bulk ingestion should use NHTSA flat files.** Public APIs are useful for probes and examples, but bulk data loading should primarily use downloadable datasets.
5. **Every answer must expose evidence.** SQL, cited records, graph paths, warnings, and confidence should be available in the response payload.

## Documentation map

| File | Purpose |
|---|---|
| `docs/00_project_brief.md` | Product definition, scope, personas, use cases |
| `docs/01_system_architecture.md` | Target production architecture and service boundaries |
| `docs/02_data_sources_and_ingestion.md` | NHTSA source strategy and ingestion pipeline |
| `docs/03_database_schema.md` | PostgreSQL schema design |
| `docs/04_graph_schema.md` | Neo4j graph model and relationship semantics |
| `docs/05_agent_workflow.md` | LangGraph/state-machine design |
| `docs/06_api_contract.md` | Backend API contract |
| `docs/07_evaluation_plan.md` | SQL, GraphRAG, hybrid, safety eval |
| `docs/08_security_safety_guardrails.md` | SQL safety, privacy, domain caveats |
| `docs/09_roadmap_phase0_phase1.md` | First implementation phases |
| `docs/contracts/answer_contract.md` | Required answer JSON shape |
| `docs/prompts/phase0_bootstrap_prompt.md` | Prompt for Codex/Claude to start repo implementation |
| `docs/adr/ADR-0001-architecture-stack.md` | Architecture decision record |

## Suggested first command sequence

```bash
mkdir autosafety-graphsql
cd autosafety-graphsql
mkdir -p docs docs/adr docs/contracts docs/prompts
# copy these docs into the repo
```

Then run the Phase 0 implementation prompt from:

```text
docs/prompts/phase0_bootstrap_prompt.md
```

---

## Phase 0 Implementation

### What was built

- FastAPI backend with health endpoints (`/`, `/healthz`, `/readyz`, `/v1/health`)
- API stubs for vehicles, chat sessions, and NHTSA ingestion probe
- SQLAlchemy models for all Phase 0/1 tables (app, domain, source, citations)
- Alembic migration for initial schema
- SQL safety validator (SELECT/WITH only, blocks DDL/DML/injection)
- Answer contract model (full response shape per `docs/contracts/answer_contract.md`)
- NHTSA client placeholder with URL constants and Phase 1 TODO stubs
- Docker Compose with postgres (pgvector), redis, neo4j
- pytest test suite

### What is intentionally NOT implemented (Phase 1+)

- GraphRAG agent
- Text-to-SQL engine
- NHTSA bulk ingestion (complaints, recalls, investigations, manufacturer communications)
- Neo4j graph population
- Frontend/UI
- Eval dashboard
- JWT authentication
- Celery background jobs

### Local setup

```bash
# Install dependencies
pip install -e ".[dev]"

# Start infrastructure
docker compose up postgres redis neo4j -d

# Run migrations
alembic upgrade head

# Start API
uvicorn app.main:app --reload --port 8000

# Run tests
pytest tests/ -v
```

### Health checks

```bash
curl http://localhost:8000/
# {"message":"AutoSafety GraphQL Copilot","phase":"phase_0","version":"0.1.0"}

curl http://localhost:8000/healthz
# {"status":"ok"}

curl http://localhost:8000/readyz
# {"status":"ready"}

curl http://localhost:8000/v1/health
# {"status":"ok","version":"0.1.0"}
```

### API stubs (Phase 0)

All endpoints return stub responses. Real implementation in Phase 1.

- `GET /v1/vehicles/search` — returns empty list, notes Phase 0 stub
- `POST /v1/chat/sessions` — creates session stub
- `POST /v1/chat/sessions/{id}/messages` — returns safety response, notes Phase 0
- `POST /v1/ingestion/nhtsa/probe` — returns deferred status
