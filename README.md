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

---

## Phase 1: NHTSA Ingestion Foundation

### What was built

- NHTSA API client (`app/services/nhtsa_client.py`) — complaints and recalls via EIEARS API
- Ingestion service (`app/services/ingestion/`) — idempotent upserts, per-vehicle error isolation
- CLI script (`scripts/ingest_phase1_nhtsa.py`) — dry-run, limit flags
- Real DB-backed vehicle/complaint/recall APIs
- Data quality summary endpoint

### Seed scope

9 vehicles: Ford F-150, Honda Accord, Toyota Camry (2020–2022).

### Setup

```bash
# Install
pip install -e ".[dev]"

# Start infra
docker compose up postgres redis neo4j -d

# Migrate
alembic upgrade head

# Dry run ingestion
python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv --dry-run --limit-vehicles 2

# Live ingestion
python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv

# Start server
uvicorn app.main:app --reload --port 8000
```

### Verify ingestion

```bash
curl http://localhost:8000/v1/ingestion/data-quality/summary
curl "http://localhost:8000/v1/vehicles/search?make=Ford"
```

### Intentional gaps (Phase 2+)

- Full NHTSA bulk flat file ingestion
- Investigations and manufacturer communications
- Neo4j graph population
- Text-to-SQL and GraphRAG
- JWT auth, frontend, eval dashboard

---

## Phase 1.5: Reliable Complaints Ingestion (Flat-File)

### Why

Phase 1 complaint API (`/complaints/complaintsByVehicle`) is unreliable for Ford F-150 due to API model-name normalization issues (400/empty responses). Phase 1.5 solves this with local flat-file ingestion.

### What was built

- `app/services/ingestion/complaints_flat_file.py` — flat-file complaint ingestor
- `--complaints-flat-file` and `--complaints-source` CLI flags
- `POST /v1/ingestion/nhtsa/phase1-5/complaints-flat-file/run` API endpoint
- Extended data quality metrics for complaints

### Commands

```bash
# Dry run with test fixture
python scripts/ingest_phase1_nhtsa.py \
  --seed data/seeds/phase1_vehicles.csv \
  --complaints-only --complaints-source flat-file \
  --complaints-flat-file tests/fixtures/nhtsa_complaints_sample.csv \
  --dry-run

# Live ingestion
python scripts/ingest_phase1_nhtsa.py \
  --seed data/seeds/phase1_vehicles.csv \
  --complaints-only --complaints-source flat-file \
  --complaints-flat-file tests/fixtures/nhtsa_complaints_sample.csv
```

> **WARNING:** `tests/fixtures/nhtsa_complaints_sample.csv` is **synthetic test fixture data** — NOT real NHTSA records. Provide a real NHTSA complaint flat-file export for actual ingestion.

> **WARNING:** Complaint volume alone does not prove a safety defect. Complaint records are user-submitted public reports and may be noisy.

### Limitations

- Real NHTSA complaint flat-file must be provided by developer (not auto-downloaded)
- Complaints only — recalls still use Phase 1 API
- No production file upload yet (local file path required)

---

## Phase 2: SQL Analytics Foundation

### What was built

- SQL analytics service with template-based query generation
- Deterministic question parser (no LLM) extracting make/model/year/limit
- 6 supported question templates covering complaints and recalls
- Read-only SQL executor with safety validation
- Answer contract conforming responses via `/v1/sql-analytics/query` and `/v1/chat/.../messages`

### Supported questions

```bash
# Start server
uvicorn app.main:app --reload --port 8000

# SQL analytics endpoint
curl -X POST http://localhost:8000/v1/sql-analytics/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Top complaint components for Ford F-150 2020"}'

curl -X POST http://localhost:8000/v1/sql-analytics/query \
  -H "Content-Type: application/json" \
  -d '{"question": "How many complaints does Honda Accord 2021 have?"}'

curl -X POST http://localhost:8000/v1/sql-analytics/query \
  -H "Content-Type: application/json" \
  -d '{"question": "List recalls for Ford F-150 2020"}'

curl -X POST http://localhost:8000/v1/sql-analytics/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which vehicles have the most complaints?"}'
```

> **WARNING:** Phase 2 is **deterministic template-based** SQL analytics. Full LLM Text-to-SQL is deferred to Phase 4.
> **WARNING:** Complaint volume alone does not prove a safety defect.

---

## Phase 3: Neo4j Graph Foundation

### What was built

- Neo4j client with dependency injection
- Graph schema: 6 uniqueness constraints + 4 indexes for MERGE idempotency
- Graph builder: PostgreSQL → Neo4j projection via MERGE (idempotent, per-vehicle error isolation)
- Graph retrieval: vehicle neighborhood, recall paths via predefined Cypher templates
- API endpoints: health, schema/setup, build, status, vehicle neighborhood, recall paths
- CLI: `scripts/build_phase3_graph.py`
- 27 tests (no live Neo4j required)

### Setup and commands

```bash
# Start infra (Neo4j + Postgres)
docker compose up postgres redis neo4j -d

# Setup schema (constraints + indexes) — idempotent
python scripts/build_phase3_graph.py --setup-schema

# Dry run — count records without writing
python scripts/build_phase3_graph.py --dry-run --limit-vehicles 5

# Build graph projection
python scripts/build_phase3_graph.py --build --limit-vehicles 5

# Check graph status
python scripts/build_phase3_graph.py --status

# API endpoints
curl http://localhost:8000/v1/graph/health
curl http://localhost:8000/v1/graph/status
```

### Graph model

```
VehicleMake → HAS_MODEL → VehicleModel → HAS_YEAR → ModelYear
                                                    ↓
                                        HAS_COMPLAINT → Complaint → MENTIONS_COMPONENT → Component
                                                    ↓
                                      AFFECTS ← Recall → RELATED_TO_COMPONENT → Component
```

### Safety caveats

- **Complaint volume alone does not prove a safety defect.**
- **Recall links via AFFECTS are official only** when campaign records explicitly apply.
- **Recall links via RELATED_TO_COMPONENT are "potentially related by shared vehicle/component"** — NOT "caused by" or "officially linked."

### Intentional gaps (Phase 4+)

- Vector embeddings and semantic similarity
- Full GraphRAG retrieval
- LLM Text-to-Cypher
- LLM Text-to-SQL
- LLM-based path ranking
- Graph visualization frontend
- Background job / Celery for large builds
- JWT auth

---

## Phase 4: Hybrid SQL + Graph Evidence Answers

### What was built

- Hybrid service: orchestrates Phase 2 SQL analytics + Phase 3 graph retrieval
- Hybrid parser: deterministic detection of SQL+graph question patterns
- Answer composer: merges SQL results + graph evidence into answer contract
- Hybrid API endpoint: `POST /v1/hybrid/query`
- Chat endpoint routes hybrid questions to Phase 4

### Setup

Phase 4 requires Phase 2 SQL analytics and Phase 3 graph to be populated:

```bash
# Start infra
docker compose up postgres redis neo4j -d

# Setup and build graph (if not done)
python scripts/build_phase3_graph.py --setup-schema
python scripts/build_phase3_graph.py --build --limit-vehicles 5

# Try hybrid endpoint
curl -X POST http://localhost:8000/v1/hybrid/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which component has the most complaints for Ford F-150 2020, and are there related recalls?"}'

# Or via chat endpoint
curl -X POST http://localhost:8000/v1/chat/sessions/test-session/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Show complaints and recall evidence for Honda Accord 2021."}'
```

### Supported hybrid question patterns

| Question | Returns |
|---|---|
| "Top component + related recalls for Ford F-150 2020" | SQL top components + graph recall paths |
| "Complaints and recall evidence for Honda Accord 2021" | SQL count + graph recall neighborhood |
| "Does Toyota Camry have complaints and recalls?" | SQL + graph evidence |
| "Vehicles with most complaints + graph" | SQL ranking + top vehicle recall paths |

### Safety caveats

- **Complaint volume alone does not prove a safety defect.**
- **Graph recall links via shared component are POTENTIAL associations only.**
- **Only Recall → AFFECTS → ModelYear is official** when NHTSA campaign explicitly applies.
- No causality claims from complaint-recall co-occurrence.

## Phase 5: Component-Level Graph Links

### What was built

- Component nodes MERGE'd into Neo4j before complaint/recall processing
- `Complaint -[:MENTIONS_COMPONENT]-> Component` when complaint has `original_component`
- `Recall -[:RELATED_TO_COMPONENT]-> Component` when recall has component data
- Component evidence queries: `get_component_evidence_for_vehicle()` and `get_shared_component_recall_paths()`
- Phase 5 stats tracked: `component_nodes_merged`, `complaint_component_links_seen`, `complaint_component_links_merged`, `recall_component_links_seen`, `recall_component_links_merged`, `component_links_skipped`, `component_link_errors`
- API endpoint: `GET /v1/graph/vehicles/{vehicle_id}/component-evidence`
- Hybrid service integrates component evidence into SQL+graph answers

### Graph relationship counts (5 vehicles)

| Relationship | Count |
|---|---|
| MENTIONS_COMPONENT | 5 |
| RELATED_TO_COMPONENT | 0 (recall component data missing in source) |
| Component nodes | 5 |

### Safety caveats

- **Complaint volume alone does not prove a safety defect.**
- **Graph recall links via shared component are POTENTIAL associations only.**
- **Only Recall → AFFECTS → ModelYear is official** when NHTSA campaign explicitly applies.
- **RELATED_TO_COMPONENT may remain zero** because current NHTSA recall records lack component fields — this reflects missing source data, not absence of safety relevance.
- No causality claims from complaint-recall co-occurrence.

### Setup

Rebuild graph to apply Phase 5 links:

```bash
python scripts/build_phase3_graph.py --setup-schema
python scripts/build_phase3_graph.py --build --limit-vehicles 5
python scripts/build_phase3_graph.py --status
```

### API endpoints

```bash
# Component evidence for a vehicle
curl http://localhost:8000/v1/graph/vehicles/{vehicle_id}/component-evidence

# Hybrid question with component evidence
curl -X POST http://localhost:8000/v1/hybrid/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which component has the most complaints for Ford F-150 2020, and are there related recalls?"}'
```

### Intentional gaps (Phase 6+)

- LLM answer generation and synthesis (Phase 7)
- Full GraphRAG with semantic chunk retrieval
- Embedding-based complaint-recall matching
- Graph visualization frontend
- Background job / Celery for large builds
- JWT auth

---

## Phase 6: GraphRAG Retrieval Foundation

### What was built

- Canonical evidence documents from PostgreSQL complaints and recalls
- Deterministic text chunking with stable SHA-256 chunk IDs
- Embedding providers: DeterministicTestProvider (tests/dev) and LocalSentenceTransformerProvider (optional)
- pgvector-backed vector store with JSONB brute-force fallback
- Semantic retrieval with citation metadata and deduplication
- Neo4j graph expansion from retrieved source entities (predefined Cypher only)
- Safety caveats: similarity ≠ causality, shared component = potential association

### Architecture flow

```
PostgreSQL complaints/recalls
  → Document builder (canonical text + metadata)
    → Chunker (stable IDs, deterministic)
      → Embedding provider (deterministic or sentence-transformers)
        → Vector store (pgvector or JSONB fallback)
          → Semantic retrieval + citation
            → Neo4j expansion (predefined Cypher)
              → Response (chunks, citations, graph paths, warnings)
```

### Setup

```bash
# Start infra
docker compose up postgres redis neo4j -d

# Run migrations (creates evidence_documents + evidence_chunks + embedding_vector column)
alembic upgrade head

# Check index status
python scripts/index_phase6_graphrag.py --status

# Index documents (force-reembed for fresh vector column after migration 0003)
python scripts/index_phase6_graphrag.py --source-type all --limit 50 --force-reembed

# Query retrieval
python scripts/query_phase6_graphrag.py \
  --question "brake complaints and recalls for Ford F-150 2020" \
  --top-k 5

# Filtered retrieval
python scripts/query_phase6_graphrag.py \
  --question "brake complaints" \
  --top-k 5 \
  --source-type complaint \
  --make Ford \
  --model F-150 \
  --model-year 2020

# Evaluation
python scripts/evaluate_phase6_graphrag.py --verbose
```

### API endpoints

```bash
# Health
curl http://localhost:8000/v1/graphrag/health

# Status
curl http://localhost:8000/v1/graphrag/status

# Retrieve
curl -X POST http://localhost:8000/v1/graphrag/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "question": "brake complaints for Ford F-150 2020",
    "top_k": 5,
    "include_graph": true,
    "make": "Ford",
    "model": "F-150",
    "model_year": 2020
  }'

# Index (admin only)
curl -X POST http://localhost:8000/v1/graphrag/index \
  -H "Content-Type: application/json" \
  -d '{"source_type": "all", "limit": 50}'
```

### Vector backend

| Backend | Trigger | Search method |
|---|---|---|
| pgvector | `embedding_vector` column exists | Native `vector(384)` + HNSW index + cosine distance `<=>` |
| jsonb_fallback | `embedding_vector` column absent | Brute-force JSONB cosine similarity |

`get_graphrag_status()` reports exactly `"pgvector"` or `"jsonb_fallback"` — no "pgvector-backed" when fallback active.

### Embedding providers

| Provider | Use case | Network | Dimension |
|---|---|---|---|
| DeterministicTestProvider (default) | Tests and dev | No | 384 |
| LocalSentenceTransformerProvider | Production semantic quality | Yes (model download) | configurable |

Set via `GRAPHRAG_EMBEDDING_PROVIDER` env var.

### Evaluation

```
python scripts/evaluate_phase6_graphrag.py
```

Metrics: Recall@1, Recall@3, MRR. Computed deterministically from evaluation fixture.

> **WARNING:** Deterministic lexical embeddings reflect token-overlap similarity, not semantic understanding. The 5-query evaluation fixture is statistically insignificant. Do not extrapolate to production quality.

### Safety caveats

- **Semantic similarity does not prove a safety defect or official causality.**
- **Shared component paths are potential associations, not causality.**
- **Official recall applicability is Recall → AFFECTS → ModelYear only.**
- **Retrieved complaint records are public reports and may be noisy.**

### Intentional gaps (Phase 7+)

- LLM answer generation and synthesis
- Unrestricted LLM Text-to-SQL or Text-to-Cypher
- Frontend / UI
- Background job / Celery for large-scale indexing
- JWT auth