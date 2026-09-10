# AutoSafety GraphSQL Copilot

Evidence-first analyst copilot for exploring public vehicle-safety data from NHTSA.

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
| `docs/phase7_final_evaluation_report.md` | Final guarded-answer evaluation and gates |
| `docs/phase7_runtime_acceptance_report.md` | Live PostgreSQL/pgvector/Neo4j/API/CLI acceptance |
| `docs/phase7_closeout_report.md` | Phase 7 architecture, security, limitations, handoff |
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
# {"message":"AutoSafety GraphQL Copilot","phase":"phase_10","version":"0.1.0"}

curl http://localhost:8000/healthz
# {"status":"ok"}                     liveness only

# Readiness actually probes dependencies since Phase 10, and returns 503 when a
# required one is down. Only PostgreSQL is required.
curl -i http://localhost:8000/readyz
# 200 {"status":"ready","ready":true,"dependencies":[
#       {"name":"postgresql","reachable":true,"required":true,...},
#       {"name":"neo4j","reachable":true,"required":false,...}, ...]}

curl http://localhost:8000/v1/health
# {"status":"ok","version":"0.1.0"}
```

Operator detail — Alembic revision, provider configuration shape, audit
counters — lives behind the admin guard at `GET /v1/ops/diagnostics`. See
`docs/phase10_operator_runbook.md`.

### API stubs (Phase 0)

All endpoints return stub responses. Real implementation in Phase 1.

- `GET /v1/vehicles/search` — returns empty list, notes Phase 0 stub
- `POST /v1/chat/sessions` — deprecated since Phase 9; now persists a real
  conversation and bridges to the guarded path. Use `POST /v1/conversations`.
- `POST /v1/chat/sessions/{id}/messages` — deprecated since Phase 9; routes
  through GuardedAnswerService. Use `POST /v1/conversations/{id}/messages`.
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
python scripts/evaluate_phase6_graphrag.py --verbose
```

Metrics: Recall@1, Recall@3, MRR. Computed deterministically from evaluation fixture.

**Actual results (local fixture, deterministic lexical embeddings):**
- Recall@1: 0.625 (5/8 expected hits in top-1)
- Recall@3: 0.625 (5/8 expected hits in top-3)
- MRR: 0.750

> **WARNING:** Deterministic lexical embeddings reflect token-overlap similarity, not semantic understanding. The 5-query evaluation fixture is statistically insignificant. Do not extrapolate to production quality.

### Safety caveats

- **Semantic similarity does not prove a safety defect or official causality.**
- **Shared component paths are potential associations, not causality.**
- **Official recall applicability is Recall → AFFECTS → ModelYear only.**
- **Retrieved complaint records are public reports and may be noisy.**

### Current Phase 6 limitations

- Deterministic lexical embeddings do not demonstrate production semantic quality.
- Corpus and evaluation fixture are small.
- Unrestricted LLM Text-to-SQL or Text-to-Cypher remains intentionally forbidden.
- Frontend, background indexing jobs, and deployment auth remain outside this phase.

---

## Phase 7: Guarded Answer Synthesis

### What was built

- Real-provider abstraction plus deterministic offline provider/fallback.
- Mandatory GraphRAG base retrieval and four application-owned, allowlisted,
  read-only tools: vehicle resolution, SQL analytics, graph evidence, and
  GraphRAG retrieval.
- Bounded orchestration: at most 2 tool rounds and 4 total calls by default.
- Application-owned evidence sufficiency, claim/citation validation, official
  recall semantics, causality guard, repair, deterministic composition,
  confidence, warnings, and abstention.
- Prompt-injection and unsafe-provider-output rejection; public citation text with
  instruction-like content is redacted.
- Final guarded-answer API and CLI. Raw Phase 7C orchestration/provider output is
  not a public answer contract.

### Architecture

```text
FastAPI / CLI
  -> GuardedAnswerService
  -> bounded SynthesisOrchestrator
  -> mandatory GraphRAG + optional allowlisted tools
  -> configured provider or deterministic fallback
  -> evidence/citation adaptation
  -> sufficiency + claim/citation/recall/causality validation
  -> repair or deterministic composition
  -> application-computed confidence + warnings/abstention
  -> GuardedAnswerResult (phase_7)
```

Complaint evidence remains observational. Official applicability requires an
explicit `Recall -[:AFFECTS]-> ModelYear` path. Shared-component evidence is
potential only and never proves causality.

### API

```bash
# Safe provider/tool status; performs no external provider request
curl http://localhost:8000/v1/graphrag/answer/status

# Final Phase 7 guarded answer
curl -X POST http://localhost:8000/v1/graphrag/answer \
  -H "Content-Type: application/json" \
  -d '{"question":"What brake complaints are reported for Ford F-150 2020?"}'
```

Request fields are only `question` and optional `include_trace`. Credentials,
provider URLs, arbitrary tools, raw SQL, and raw Cypher are rejected by schema.
Normal guarded outcomes—including abstention, partial evidence, graph degradation,
and deterministic fallback—return HTTP 200 with explicit semantics.

Phase 6 retrieval remains separate and compatible at
`POST /v1/graphrag/retrieve`.

### CLI

```bash
python scripts/query_phase7_answer.py \
  --question "What brake complaints are reported for Ford F-150 2020?"

python scripts/query_phase7_answer.py \
  --question "Are there official recalls affecting Ford F-150 2020?" \
  --pretty
```

Default output is valid JSON. A valid guarded abstention exits 0. Configuration or
irrecoverable internal failure exits non-zero. Trace is omitted unless requested.

### External provider configuration

Deterministic mode is default and requires no network:

```text
PHASE7_SYNTHESIS_PROVIDER=deterministic
```

An OpenAI-compatible provider is selected only from trusted application settings:

```text
PHASE7_SYNTHESIS_PROVIDER=openai_compatible
PHASE7_SYNTHESIS_MODEL=<configured-model>
PHASE7_SYNTHESIS_ALLOW_EXTERNAL=true
PHASE7_PROVIDER_API_KEY=<secret>
PHASE7_PROVIDER_BASE_URL=https://api.openai.com/v1
```

Missing/disabled external configuration leaves deterministic operation available.
Runtime provider failure is visible through `synthesis_mode`, `provider`, warnings,
and sanitized trace/status fields. On 2026-09-05, the configured OpenAI-compatible
`gpt-5.6-luna` path was live-verified through provider parsing and the local
guarded service. The bounded run also confirmed application rejection/rescue of
an unsupported causal claim; see the external acceptance report for limitations.

### Evaluation and validation

```bash
python scripts/evaluate_phase7_answers.py
pytest tests/test_phase7_guarded_answer_synthesis.py -v
pytest tests/ -v
```

Phase 7F results:

- 20/20 deterministic evaluation cases passed.
- 15/15 acceptance metric gates passed; citation validity, accepted-claim
  grounding/coverage, invalid-output rejection, causal guard, prompt-injection
  resistance, tool rejection, and deterministic stability were all 1.00.
- 24/24 final Phase 7 integration tests passed.
- 663/663 repository tests passed at Phase 7F; the external-provider checkpoint
  subsequently passed 666/666 with the same 3 existing deprecation warnings.
- Live local PostgreSQL/pgvector/Neo4j, all four tools, guarded service, API, CLI,
  and Phase 6 route compatibility passed.

### Current limitations

- Real external-provider acceptance was bounded; live model-driven tool planning
  is not implemented, and the fixed positive recall-applicability path still
  needs one future bounded recheck.
- Local corpus is only 42 documents: 5 complaints and 37 recalls.
- Default deterministic embeddings are lexical/token-overlap based.
- Live graph has 59 nodes/58 relationships; `RELATED_TO_COMPONENT = 0` because
  current recall source component fields are missing.
- Evaluation is compact and deterministic, not a production-quality benchmark.
- No frontend exists.

See `docs/phase7_final_evaluation_report.md`,
`docs/phase7_runtime_acceptance_report.md`, and
`docs/phase7_closeout_report.md` for Phase 7 evidence. See
`docs/phase7_external_llm_acceptance_report.md` for the bounded real-provider
checkpoint.

## Phase 8: Bounded Multi-Turn Guarded Conversation

Phase 8 adds session-scoped multi-turn conversation **on top of** the Phase 7
guarded contract. `GuardedAnswerService` remains the sole authority on claims,
citations, warnings, confidence, and abstention; no Phase 7 boundary was
weakened.

### What it does

```text
Conversation
-> bounded prior-turn entity state (last 5 turns)
-> deterministic context resolution (make / model / model_year / component only)
-> GuardedAnswerService            <- unchanged Phase 7 path
-> cross-turn provenance gate      <- Phase 8 defense in depth
-> bounded persisted turn + citation lineage
```

A follow-up like `"What about recalls?"` inherits vehicle context from an
earlier turn and is resolved to
`"What about recalls? (for Ford F-150 2020; component SERVICE BRAKES)"`, then
answered from **newly retrieved, newly validated** evidence.

### Safety properties

- **Prior assistant text is never evidence.** Only four allowlisted entity slots
  cross a turn boundary; no prior conversation text is ever forwarded to
  retrieval or a provider.
- **Prompt injection cannot persist.** An instruction planted in turn 1 has no
  channel into turn 2.
- **Every accepted factual claim in every turn** still validates against the
  citations that turn itself retrieved; a violation becomes an abstention with
  reason `cross_turn_provenance_violation`.
- **Conversation isolation.** All reads and writes are scoped by `session_id`.
- **Hard deletion.** `DELETE` removes turns, messages, citations, and the
  session row, leaving zero residue.

### Endpoints

```http
POST   /v1/conversations
GET    /v1/conversations/{conversation_id}
GET    /v1/conversations/{conversation_id}/turns
POST   /v1/conversations/{conversation_id}/messages
DELETE /v1/conversations/{conversation_id}
GET    /v1/conversations/status/config
```

The legacy `/v1/chat/*` Phase 2/4 single-turn surface is unchanged.

### Commands

```bash
# Apply the Phase 8 migration (additive; single head)
alembic upgrade head

# Multi-turn conversation from the CLI
python scripts/query_phase8_conversation.py     -q "What brake complaints are reported for Ford F-150 2020?"     -q "What about recalls?" --pretty

# Safe conversation bounds and policy posture; no credentials, no network
curl http://localhost:8000/v1/conversations/status/config

# Offline multi-turn evaluation (10 cases, 20 turns, 11 gates)
python scripts/evaluate_phase8_conversations.py
```

### Maintenance route protection

Setting `PHASE8_ADMIN_TOKEN` requires a matching `X-Admin-Token` header on
`POST /v1/ingestion/nhtsa/phase1/run`,
`POST /v1/ingestion/nhtsa/phase1-5/complaints-flat-file/run`,
`POST /v1/graph/schema/setup`, `POST /v1/graph/build`, and
`POST /v1/graphrag/index`. When it is unset those routes stay open — that is the
pre-existing exposure, documented rather than disguised. Full JWT auth and roles
remain deferred.

### Phase 8 results

- 830/830 repository tests pass (666 baseline + 164 new), with the same 3
  pre-existing deprecation warnings.
- 10-case / 20-turn multi-turn evaluation passes all 11 gates: citation validity
  and coverage 1.00, conversation isolation 1.00, prompt-injection resistance
  1.00, prior-text and prior-citation leak rates 0.00, causality guard 1.00,
  context bounds 1.00, deterministic stability 1.00.
- Live PostgreSQL/pgvector/Neo4j runtime acceptance passed; the migration
  applied with a single head and no data loss.
- A live follow-up turn produced `official_recall_applicability` claims from
  real Neo4j `AFFECTS` paths, verifying the positive recall path Phase 7 left
  unrechecked.
- 4 bounded real-provider requests (`openai_compatible` / `gpt-5.6-luna`); one
  live model claim was rejected by Phase 7 and deterministically rescued at 100%
  citation coverage.

### Current limitations

- Context resolution is slot-based, not general anaphora resolution.
- No conversation summarization, long-term memory, or personalization exists
  (deliberately deferred).
- Live model-driven `plan_tool_calls()` network planning is still not
  implemented.
- Redis is running but intentionally unused by Phase 8; PostgreSQL is the single
  source of truth for conversation state.
- `agent_runs` and `tool_calls` remain unpopulated Phase 0 scaffolding.
- Maintenance protection is a shared-secret header, not real authentication.
- ~~Migration files are not tracked by Git~~ — fixed in Phase 9; the complete
  Alembic chain is now committed and a fresh clone can migrate to head.
- The small corpus, lexical deterministic embeddings, and
  `RELATED_TO_COMPONENT = 0` limitations are unchanged from Phase 6/7.

See `docs/phase8_design.md`, `docs/phase8_implementation_report.md`, and
`docs/phase8_runtime_acceptance_report.md` for Phase 8 evidence.

## Phase 9: Deployability, Security, and Observability

Phase 9 hardens the platform before adding more product intelligence. **No
answer semantics changed** — the Phase 7 guarded path and the Phase 8
conversation contract are untouched, and both evaluations still pass at full
gates.

### 1. Migration history is now tracked

`.gitignore` previously excluded `migrations/versions/*.py`, so **none** of the
Alembic revisions were committed and a fresh clone could not rebuild the schema
at all. The ignore rule is gone and the complete chain is tracked together:

```text
<base> -> 0001 initial -> 0002 graphrag -> 0003 pgvector
       -> 0004 conversation -> 0005 execution audit   (single head)
```

A fresh empty database now migrates to head. Verified against a temporary
database and guarded by `tests/test_phase9_migrations.py`.

### 2. Maintenance routes fail closed

Phase 8's admin guard was fail-**open**: with no token configured, ingestion and
rebuild routes stayed public. Phase 9 inverts that default.

```text
server token not configured  -> 503 ADMIN_PROTECTION_UNAVAILABLE
header missing or wrong      -> 401 ADMIN_TOKEN_REQUIRED
header correct               -> allowed
```

Set `ADMIN_API_TOKEN` (minimum 16 characters; placeholders like `changeme` are
rejected) and send it as `X-Admin-Token`:

```bash
export ADMIN_API_TOKEN="a-long-random-operator-secret"

curl -X POST http://localhost:8000/v1/graph/build \
     -H "X-Admin-Token: $ADMIN_API_TOKEN" -H 'Content-Type: application/json' -d '{}'
```

Protected: ingestion runs, graph schema setup, graph build, graphrag index.
Read-only routes are unaffected. Conversation deletion is **not** admin-gated —
it is a privacy action scoped by an unguessable UUID.

### 3. Legacy `/v1/chat/*` no longer bypasses the guard

The chat routes previously called the Phase 2/Phase 4 services directly,
skipping citation validation, the causality guard, deterministic confidence, and
abstention — and `POST /sessions` returned an id that was never persisted.

They are now a **deprecated bridge** over the guarded conversation path. The
documented answer-contract shape is preserved; responses carry
`Deprecation: true` and `Link: </v1/conversations>`, and the routes are marked
deprecated in OpenAPI.

Migration path: `/v1/chat/*` → `/v1/conversations/*`.

### 4. Execution audit (`agent_runs` / `tool_calls`)

Both tables existed since the initial migration and were never populated. Phase
9 fills them with safe execution metadata: provider, synthesis mode, fallback,
abstention, confidence, validation outcome, tool call count, latency, and the
conversation/turn each run belongs to, plus one row per real tool execution.

```sql
SELECT surface, status, synthesis_mode, validation_outcome, tool_call_count
FROM agent_runs ORDER BY created_at DESC LIMIT 5;
```

Audit is **observability, not memory**: nothing recorded is ever read back into
an answer, and no API route exposes audit rows. Never stored: prompts, provider
raw responses, credentials, connection strings, raw SQL, raw Cypher, or tool
arguments — only the allowlisted operation name is kept.

Audit writes **fail open**: an audit outage never fails a user request.

### Phase 9 results

- 953/953 repository tests pass (830 baseline, minus 18 superseded fail-open
  admin tests, plus 141 new Phase 9 tests).
- Fresh empty database migrates to head; existing database upgraded 0004 → 0005
  with no data loss.
- Phase 7 evaluation 20/20 all gates; Phase 8 evaluation 10 cases / 20 turns,
  all 11 gates (citation validity and coverage 1.00, conversation isolation
  1.00, prompt-injection resistance 1.00, leak rates 0.00).
- Live multi-turn run recorded 3 audit runs, all linked to their turn, 11 tool
  calls with no duplicates and zero persisted payloads.
- Scanning every value in the live audit tables for secrets, SQL, or Cypher
  returned 0 matches.
- No live external LLM request was spent; Phase 7/8 already proved the real
  provider.

### Current limitations

- Admin protection is a shared operator secret, not real authentication; JWT and
  roles remain deferred.
- Audit is fail-open by design, so a database outage loses audit rows while
  answers continue.
- `agent_runs.total_tokens` stays unpopulated — provider token usage is
  deliberately not exposed.
- Live model-driven `plan_tool_calls()` is still absent, and deterministic
  embeddings remain lexical (unchanged Phase 6/7/8 debt).

See `docs/phase9_design.md`, `docs/phase9_implementation_report.md`, and
`docs/phase9_runtime_acceptance_report.md` for Phase 9 evidence.

---

## Phase 10: Maintainability and Operational Hardening

Phase 10 makes the quality tooling real, fixes what it found, and gives
operators the configuration and visibility they lacked. **No product feature
was added.**

### The gate that checked nothing

`make lint` ran `ruff check autosafety/` — a directory that has never existed in
this repository; the package is `app/`. Ruff exits 0 on a missing path, so the
quality gate silently inspected nothing and passed for every phase from 0
through 9. mypy was configured and installed, but no target ever invoked it.

Pointing ruff at the real trees reported **1079 findings**, triaged rather than
mass-formatted:

```text
626  fixed by ruff's safe autofix (behavior-preserving)
 42  correctness and style findings fixed by hand
400  line-length findings -> `make lint-all` (advisory), not the enforced gate
 20  SQLAlchemy/enum idioms -> ignored with rationale in pyproject.toml
~30  Alembic template artifacts -> narrow per-file ignores
```

`make lint` is now green across `app/`, `tests/`, `scripts/`, and `migrations/`.
No exemption disables a correctness rule, and a test asserts that.

### Three real defects it had been hiding

- **A dead retrieval path.** `graph_service.get_vehicle_neighborhood` shadowed
  the same-named import from `graph_queries`, so its internal call invoked
  *itself* with the wrong signature. The `TypeError` was swallowed by a broad
  `except`, and every vehicle-neighborhood lookup returned `None` while
  appearing to work.
- **A redaction gap.** The evidence-metadata denylist listed `"api_key"` twice.
  Matching is substring-based, and `"api_key"` is not a substring of
  `"apikey"` — so a key spelled `apiKey` was never redacted.
- **A destructive target that destroyed nothing.** `make db-reset` ran
  `rm -rf postgres_data …` against directories that do not exist, because
  compose uses named volumes. It deleted nothing and reported success. It now
  runs `docker compose down -v` and requires `CONFIRM=yes`.

### Audit read access

`docs/06_api_contract.md` had deferred `GET /v1/agent-runs/*` pending
"authorization and redaction rules not yet defined". Phase 9 defined both, so
Phase 10 implements it:

```http
GET /v1/agent-runs            list, filterable, bounded
GET /v1/agent-runs/summary    aggregate counters
GET /v1/agent-runs/{run_id}   one run with its tool calls
```

Admin-only, reusing the existing fail-closed guard — no second auth mechanism.
Responses come from an explicit safe-field allowlist, so `input_json`,
`output_json`, `intent`, and `warnings` can never be projected. Tests seed
credentials, raw SQL, and raw Cypher into exactly those columns and assert none
of it escapes.

### Readiness that means something

`/readyz` returned a hardcoded `{"status": "ready"}` regardless of whether any
dependency was reachable, so an orchestrator kept routing traffic to an
instance whose database was down. It now probes its dependencies and returns
503 when a required one is unreachable. The public payload is booleans and
coarse status words only; driver exceptions are reduced to a class name because
SQLAlchemy and the Neo4j driver both embed host, port, and user in their
messages.

### Phase 10 results

- 1039 tests pass (954 at entry, plus 85 new Phase 10 tests).
- `make lint` green; `make lint-all` reports 398 line-length findings;
  `make typecheck` reports 47 pre-existing mypy errors. Both are baselined, not
  suppressed.
- Live acceptance: audit routes 503 with no token, 200 with one, 404/422 on bad
  input, and a privacy scan over live responses found zero forbidden markers.
- Phase 7 and Phase 8 evaluations pass unchanged.

### Current limitations

- 398 line-length findings and 47 mypy errors remain, baselined and visible via
  `make lint-all` / `make typecheck`.
- `ADMIN_API_TOKEN` is unset in the development environment, so maintenance and
  audit routes correctly fail closed until an operator sets one.
- Audit writes remain fail-open by design.
- `/v1/chat/*` is still deprecated but present; no doc defines a retirement
  milestone.

Operators start at `docs/phase10_operator_runbook.md`. Design rationale is in
`docs/phase10_design.md`; evidence in
`docs/phase10_runtime_acceptance_report.md`.

---

## Phase 11: Type Safety, CI, and Developer Quality Gates

Phase 11 turns static analysis on, fixes what it finds, and adds the CI that
`docs/09_roadmap_phase0_phase1.md` listed as a **Phase 0 deliverable** and that
was never built. For eleven phases, nothing verified this repository
automatically.

### mypy: 47 errors to zero

mypy was configured from the start and shipped in the dev extras, but no target
invoked it until Phase 10 gave it one. It reported 47 errors across 16 files.
All are resolved, with no blanket ignore and no weakening of the settings:

```text
mypy app/    Success: no issues found in 106 source files
```

Exactly two suppressions remain in `app/`, both error-code scoped, both for
optional packages that are not project dependencies and ship no stubs
(`sentence_transformers`, `redis`). One pre-existing ignore was removed.

### Four real defects it found

- **A route that always raised.** `GET /v1/graph/vehicles/{id}/recall-paths`
  builds its response with `[r.to_dict() for r in result.recalls]`, but
  `RecallNode` had no `to_dict` — unlike its sibling node types. Every call
  returning a recall raised `AttributeError`. No test covered the route, only the
  query beneath it. `RecallPathResult.to_dict` had been quietly working around
  the gap with a duplicated inline copy of the same mapping.
- **An answer-contract violation.** The Phase 4 hybrid composer passed the
  internal `relation_basis` straight into `relation_source`, emitting values
  like `official_recall_affects_vehicle` when the documented contract defines
  exactly three. Internal bases are now mapped onto that vocabulary.
- **A supported intent outside its own type.**
  `complaint_count_by_component_for_vehicle` has a template, a parser branch, two
  service branches, and a tool operation, but was missing from the
  `ParsedQuestion.intent` Literal.
- **Two classes, one name.** `VectorStore.upsert_document` was annotated with the
  ORM `EvidenceDocument` while every caller passes the dataclass of the same
  name, so `document.metadata` resolved to SQLAlchemy's `MetaData`.

The rest were annotations that did not describe the code: 33 nullable ORM
columns declared non-Optional, two JSON array columns declared `dict`, an
`__exit__` annotated `-> bool` (which told mypy the context manager might
swallow exceptions, turning six correct methods into "missing return"), and an
audit recorder typed `object`, now a Protocol.

### One command before you commit

```bash
make check       # lint + typecheck + test + evaluate
```

`evaluate` is new to the gate. `docs/phase7_closeout_report.md` already called
the Phase 7 and Phase 8 safety evaluations mandatory, but nothing ran them
automatically — a regression in citation validity or conversation isolation
would surface only if someone remembered. Every part is offline: no Docker, no
credential, no paid provider.

### CI

`.github/workflows/ci.yml`, on every push and pull request, Python 3.11:

| Job | Services | Runs |
|---|---|---|
| `quality` | none | lint, typecheck, tests, both safety evaluations, offline migration-chain check |
| `migrations` | `pgvector/pgvector:pg16` | one head, empty DB → head, schema verification, idempotent re-run |

`quality` provisions nothing because the suite is hermetic. `migrations` needs a
real database to prove the Phase 9 fresh-clone gate, and needs pgvector
specifically — revision `0003` adds a `vector(384)` column.

**The workflow requires no secrets.** No provider key, no `ADMIN_API_TOKEN`, no
operator `.env`, no external LLM call; `permissions: contents: read`. Tests
assert this. GitHub Actions cannot run locally, so validation was structural
plus running each `quality` command directly against the checkout — stated as
such rather than claimed as a green run.

### One command before you commit

```bash
python scripts/check.py     # works without `make`, on any platform
make check                  # delegates to the same script
```

`scripts/check.py` is the canonical gate and the single source of truth:

```text
lint -> format -> typecheck -> tests -> Phase 7 eval -> Phase 8 eval
```

CI invokes it and pre-push runs it, so a developer, the Makefile, and GitHub
Actions cannot drift — a test fails if the Makefile starts naming `ruff`,
`mypy`, or `pytest` directly.

Install the hooks once with `pre-commit install` and
`pre-commit install --hook-type pre-push`. Commit hooks stay fast; the full
gate runs before a push.

### Line length: enforced

E501 was advisory through Phase 10 and Phase 11 on the measurement that
formatting could not eliminate it. That measurement counted the immutable
Alembic revisions. Excluding them, the residue after `ruff format` was **56**,
not 398 — so it was formatted (in a dedicated commit) and the rest hand-wrapped.

**Zero E501 findings remain in the gate.** Four files keep a named exemption
because they embed Cypher sent to Neo4j or literal prompt text sent to the
model, where a line break changes what is transmitted rather than how it reads.

### Phase 11 results

- **1152 tests pass**, with every container stopped.
- `mypy app/ scripts/ tests/` — **148 files, zero errors**, under
  `disallow_untyped_defs` and seven further measured strict flags.
- Ruff lint and format gates clean; Phase 7 and Phase 8 evaluations pass.
- Every route in `graph.py` now has direct HTTP coverage — the gap that let a
  500 ship for eight phases.
- **Real CI verified:** run `34436853068` on `github.com/aumi102/autosafety`,
  both jobs green.
- **`master` is protected:** both CI jobs required, `enforce_admins` on, force
  pushes and deletions blocked.
- No safety boundary changed. Where mypy and an invariant disagreed, the
  annotation was corrected — never the guard.

### Phase 11 technical debt

**None.** All nine deferred items are closed; the register with evidence is in
`docs/phase11_design.md` §11.

Design rationale is in `docs/phase11_design.md`; evidence in
`docs/phase11_ci_quality_report.md`.

---

## Phase 12: Bounded Model-Driven Tool Planning

`OpenAICompatibleProvider.plan_tool_calls()` returned `[]` unconditionally.
Five phases recorded it in the same words — the Phase 7 external acceptance
report put it plainly: **"Network-based `plan_tool_calls()`: NO."** The real
provider never asked for a tool, so the orchestrator's planning loop was
exercised only by deterministic heuristics. It is now a real network round.

### What did not need building

The orchestration loop already enforced every guarantee: mandatory GraphRAG
before planning, a `for` over `range(max_tool_rounds)` with no `while`,
per-call budget checks, deduplication, application-owned call ids, and
`ToolRegistry` schema validation before dispatch. Phase 12 implements one
method and the validation around it.

```text
the model REQUESTS a tool_name + operation + arguments
the application VALIDATES against the registry schema
ToolRegistry EXECUTES
```

### The model never gets a capability

The planning prompt carries the question, the tool schemas, and a citation
*label* summary — never evidence prose, rows, credentials, SQL, or Cypher. A
test seeds a marker into the evidence text and asserts it never reaches the
wire.

Unknown tools, unknown operations, and undeclared argument keys matching
`sql`/`cypher`/`command`/`path`/`url`/`secret` are refused. The tool schema is
the allowlist. Raw SQL and Cypher are impossible at three independent layers,
and shell, filesystem, and HTTP tools cannot be requested because they are not
in the registry.

Every failure returns an empty plan, which is safe: mandatory GraphRAG evidence
is already gathered. Budgets are unchanged at 2 rounds / 4 calls.
`PHASE12_MODEL_PLANNING_ENABLED=false` is the kill switch.

### Proven with a real model

```text
LIVE PLANNING: latency=3956ms  proposed=2  rejections=[]
  graph_evidence_tool  operation=recall_paths_by_vehicle
  graph_evidence_tool  operation=component_evidence_by_vehicle
REGISTRY EXECUTION: success=True, success=True
```

Live acceptance also caught a defect no offline test could: the forbidden
markers are substrings, and `graph_evidence_tool` declares a legitimate
property called `max_paths` — which contains `path`. A correct model plan was
being thrown away. The schema is now the allowlist.

### Phase 12 results

- **1234 tests pass**, with every container stopped.
- New mandatory gate `eval-phase12`: 20 agentic safety cases, `unsafe_acceptances = 0`.
- `scripts/check.py` now runs 7 gates; Phase 7 and Phase 8 evaluations unchanged.
- 4 live provider requests spent, all planning rounds.

Design rationale is in `docs/phase12_design.md`; evidence in
`docs/phase12_runtime_acceptance_report.md`.
