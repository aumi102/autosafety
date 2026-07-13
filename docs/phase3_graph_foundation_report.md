# Phase 3: Neo4j Graph Foundation

## Status: Complete

Reliable graph projection from PostgreSQL into Neo4j implemented. Safe graph retrieval APIs exposed.

## Scope

Phase 3 is NOT full GraphRAG. Phase 3 builds the deterministic graph projection layer:
- PostgreSQL domain data → Neo4j via idempotent MERGE operations
- Safe predefined graph retrieval queries
- Graph schema with uniqueness constraints

This prepares for Phase 4 (hybrid SQL+Graph answers) and Phase 5 (GraphRAG retrieval), but does not implement embeddings, vector search, or LLM reasoning.

## Why graph after SQL analytics

Phase 2 established safe SQL analytics with template-based queries against PostgreSQL.
Phase 3 extends the data layer with a graph model that makes multi-hop relationships navigable:
- Vehicle → Component → Complaint paths
- Recall → affected vehicles and components
- Neighborhood retrieval and path traversal

This enables Phase 4 hybrid answers that cite both SQL aggregations and graph evidence.

## What was built

### `app/services/graph/neo4j_client.py`
- Neo4j driver creation from settings (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`)
- Connectivity verification
- `Neo4jClient` class with dependency injection for tests
- `execute()` and `execute_single()` helpers with bound parameters

### `app/services/graph/graph_schema.py`
- 6 uniqueness constraints (VehicleMake, VehicleModel, ModelYear, Component, Complaint, Recall)
- 4 performance indexes (dates, categories)
- Idempotent via `CREATE ... IF NOT EXISTS`
- Schema setup function returns counts and errors

### `app/services/graph/graph_models.py`
- `GraphBuildStats`: counts per entity type, errors, duration, dry_run flag
- `GraphStatus`: Neo4j node/rel counts, label distribution, PostgreSQL baseline
- `VehicleNeighborhood`: vehicle + complaints + recalls + components + edges
- `RecallPathResult`: recalls linked via AFFECTS + RELATED_TO_COMPONENT
- All models have `to_dict()` for serialization

### `app/services/graph/graph_builder.py`
- Reads PostgreSQL via SQLAlchemy session
- Projects: VehicleMake → VehicleModel → ModelYear → Complaint → Component
- Projects: Recall → AFFECTS → ModelYear, Recall → RELATED_TO_COMPONENT → Component
- MERGE-based — idempotent, safe to re-run
- Dry-run mode counts records without writing
- `limit_vehicles` support
- Per-vehicle error isolation — one bad row doesn't crash the build
- Stats: vehicle_makes/models/years_seen, components/complaints/recalls_seen, nodes_merged, relationships_merged, errors_count

### `app/services/graph/graph_queries.py`
- `get_vehicle_neighborhood()`: vehicle + complaints + recalls + components
- `get_recall_paths_for_vehicle()`: recalls via AFFECTS + RELATED_TO_COMPONENT
- `execute_arbitrary_cypher()`: BLOCKED — returns error, no user Cypher execution
- `_validate_identifier()`: sanitizes make/model/year values before Cypher params

### `app/services/graph/graph_service.py`
- `setup_graph_schema()`: idempotent schema creation
- `build_graph_from_postgres()`: orchestrates builder with sessions
- `get_graph_status()`: Neo4j + PostgreSQL comparison
- `get_vehicle_neighborhood(vehicle_id)`: lookup by PG UUID → graph retrieval
- `get_vehicle_recall_paths(vehicle_id)`: lookup by PG UUID → recall paths

### `app/api/v1/endpoints/graph.py`
- `GET /v1/graph/health` — Neo4j connectivity
- `POST /v1/graph/schema/setup` — create constraints/indexes
- `POST /v1/graph/build` — trigger graph projection
- `GET /v1/graph/status` — node/rel counts and PG baseline
- `GET /v1/graph/vehicles/{id}/neighborhood` — vehicle graph neighborhood
- `GET /v1/graph/vehicles/{id}/recall-paths` — recall paths with warning

### `scripts/build_phase3_graph.py`
```bash
python scripts/build_phase3_graph.py --setup-schema
python scripts/build_phase3_graph.py --build --limit-vehicles 5
python scripts/build_phase3_graph.py --dry-run
python scripts/build_phase3_graph.py --status
```

## Graph schema

### Node labels

| Label | Key property | Description |
|---|---|---|
| VehicleMake | `normalized_name` (unique) | Canonical vehicle make |
| VehicleModel | `key` (unique) | Composite: `{make}:{model}` |
| ModelYear | `key` (unique) | Composite: `{model}:{year}` |
| Component | `normalized_name` (unique) | Normalized component name |
| Complaint | `odi_number` (unique) | NHTSA complaint record |
| Recall | `campaign_number` (unique) | NHTSA recall campaign |

### Relationships

| Source | Relationship | Target | `relation_source` |
|---|---|---|---|
| VehicleMake | `HAS_MODEL` | VehicleModel | `source_record` |
| VehicleModel | `HAS_YEAR` | ModelYear | `source_record` |
| ModelYear | `HAS_COMPLAINT` | Complaint | `source_record` |
| Complaint | `MENTIONS_COMPONENT` | Component | `normalized_join` |
| Recall | `AFFECTS` | ModelYear | `source_record` |
| Recall | `RELATED_TO_COMPONENT` | Component | `normalized_join` |

## Safety caveats

- **Complaint volume alone does not prove a safety defect.** Complaint records are user-submitted public reports — may be noisy.
- **Recall links shown via AFFECTS are official only** when campaign records explicitly apply to this vehicle.
- **Recall links via RELATED_TO_COMPONENT are potential only.** A shared component between complaint and recall does not imply official causality.
- **Complaint-Recall paths through shared component are labeled "potentially related by shared vehicle/component".** This is NOT "caused by" or "officially linked."

## Testing strategy

- 27 Phase 3 tests, all passing
- Graph schema tests: constraints contain required labels, use IS UNIQUE
- Identifier validation tests: empty/invalid chars raise ValueError
- Arbitrary Cypher blocked: always returns error, never calls driver
- Vehicle neighborhood: returns None when not found, correct shape when found
- Recall paths: path_type is "potentially related" not causal
- Build stats: dry_run=true preserved, all fields serialized
- Service tests: mocked Neo4j client, connection errors handled
- Builder tests: mocked PostgreSQL, dry_run doesn't write, missing data doesn't crash
- No live Neo4j required for normal test runs

## Intentionally deferred

- Vector embeddings and semantic similarity matching
- GraphRAG retrieval (semantic search over complaint/recall text chunks)
- LLM Text-to-Cypher
- LLM-based path ranking
- Neo4j graph visualization frontend
- Chat session persistence
- Full NHTSA bulk ingestion (investigations, TSB, manufacturer communications)
- JWT auth
- Background job / Celery for large graph builds

## Next recommended Phase 4 tasks

1. **Hybrid SQL+Graph answers** — combine Phase 2 SQL analytics with Phase 3 graph evidence; inject graph neighborhood into answer contract
2. **Component neighborhood query** — given a component name, find all connected vehicles, complaints, recalls
3. **Complaint-to-recall graph traversal** — path finding between complaints and recalls through shared components/vehicles
4. **GraphRAG retrieval foundation** — semantic search over complaint/recall document chunks using vector similarity (deferred to Phase 4/5)
5. **Large graph build** — handle full NHTSA dataset with batching and background jobs
