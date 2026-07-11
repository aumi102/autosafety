# Phase 1 Ingestion Report

## Status: Complete

Phase 1 NHTSA ingestion foundation implemented.

## Scope implemented

### Vehicles
- Ford F-150 (2020, 2021, 2022)
- Honda Accord (2020, 2021, 2022)
- Toyota Camry (2020, 2021, 2022)
- 9 vehicles total

### Data types
- Complaints (via NHTSA EIEARS API)
- Recalls (via NHTSA recall API)
- Investigations: **deferred to Phase 1.5**
- Manufacturer communications: **deferred to Phase 1.5**

## What was built

### NHTSA client (`app/services/nhtsa_client.py`)
- `fetch_complaints_by_vehicle(NhtsaVehicle)` — GET /complaints/complaintsByVehicle
- `fetch_recalls_by_vehicle(NhtsaVehicle)` — GET /recalls/recallsByVehicle
- Timeout: 30 seconds per request
- Typed dataclasses: `NhtsaVehicle`, `NhtsaComplaintRecord`, `NhtsaRecallRecord`
- Error handling: `NhtsaApiError` on HTTP errors or timeouts

### Ingestion service (`app/services/ingestion/`)
- `nhtsa_ingestion.py`: `run_nhtsa_phase1_ingestion()` — main ingestor
  - Loads seed CSV
  - Upserts vehicles (idempotent by normalized_make/model/year)
  - Upserts components
  - Upserts complaints by ODI number (idempotent)
  - Upserts recalls by campaign number (idempotent)
  - Creates `recall_vehicle_links`
  - Stores `raw_source_rows`
  - Tracks `source_run` status
  - Per-vehicle error isolation (one vehicle failure does not stop batch)
- `normalization.py`: text normalization utilities
- `data_quality.py`: `DataQualitySummary` with counts and quality metrics

### CLI script (`scripts/ingest_phase1_nhtsa.py`)
```bash
# Dry run (no commits)
python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv --dry-run --limit-vehicles 2

# Live ingestion (all 9 vehicles)
python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv

# Complaints only
python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv --complaints-only --limit-vehicles 2
```

### API endpoints
| Endpoint | Method | Description |
|---|---|---|
| `/v1/ingestion/nhtsa/phase1/run` | POST | Run Phase 1 ingestion |
| `/v1/ingestion/source-runs` | GET | List source runs |
| `/v1/ingestion/source-runs/{id}` | GET | Get source run |
| `/v1/ingestion/data-quality/summary` | GET | Data quality metrics |
| `/v1/vehicles/search` | GET | Search vehicles (DB-backed) |
| `/v1/vehicles/{id}/overview` | GET | Vehicle metrics + top components |
| `/v1/vehicles/{id}/complaints` | GET | Vehicle complaints |
| `/v1/vehicles/{id}/recalls` | GET | Vehicle recalls |

## How to run locally

### Prerequisites
- Docker Compose running: `docker compose up postgres redis neo4j -d`
- Database migrated: `alembic upgrade head`
- Python dependencies: `pip install -e ".[dev]"`

### Run ingestion
```bash
# 1. Dry run first (2 vehicles, no commits)
python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv --dry-run --limit-vehicles 2

# 2. Live ingestion (all 9 vehicles)
python scripts/ingest_phase1_nhtsa.py --seed data/seeds/phase1_vehicles.csv

# 3. Verify counts
curl http://localhost:8000/v1/ingestion/data-quality/summary
```

### Run via API
```bash
# Start server
uvicorn app.main:app --reload --port 8000

# Trigger ingestion via API
curl -X POST http://localhost:8000/v1/ingestion/nhtsa/phase1/run \
  -H "Content-Type: application/json" \
  -d '{"seed_csv": "data/seeds/phase1_vehicles.csv", "limit_vehicles": 2, "dry_run": true}'

# Check data quality
curl http://localhost:8000/v1/ingestion/data-quality/summary

# Search vehicles
curl "http://localhost:8000/v1/vehicles/search?make=Ford&model_year=2022"

# Get vehicle complaints
curl "http://localhost:8000/v1/vehicles/{vehicle_id}/complaints"
```

## Intentionally deferred

- Investigations ingestion (Phase 1.5)
- Manufacturer communications ingestion (Phase 1.5)
- Bulk NHTSA flat file ingestion (Phase 2)
- Neo4j graph population
- Text-to-SQL natural language query engine
- GraphRAG retrieval
- JWT authentication
- Celery/Redis background jobs
- Frontend/UI
- Eval dashboard

## Known limitations

1. **API-based (not bulk flat files)**: Phase 1 uses NHTSA API which may have rate limits. Bulk flat file ingestion in Phase 2 will handle larger datasets.
2. **No vehicle normalization beyond text**: Phase 1 uses simple upper/trim normalization. Phase 2 may use vPIC API for authoritative vehicle data.
3. **Complaints without ODI number**: Some complaints may lack ODI numbers. These are deduplicated by hash of source URL, which is fragile. Phase 2 should use a more stable key.
4. **No component classification**: Component names come directly from NHTSA. Phase 2 may use LLM-based classification for canonical component names.
5. **Sync-only ingestion**: Ingestion runs synchronously. Phase 2 will use Celery for background jobs.
6. **Small seeded scope**: Only 9 vehicles. Real deployment needs full NHTSA coverage.

## Safety caveats

- **Complaint volume does not prove a defect.** Every complaint count display includes this caveat.
- **Recall relation is official only when campaign explicitly applies.** The system tracks which recalls apply to which vehicles via `recall_vehicle_links`.
- **Future semantic matching must be labeled potentially related, not official causality.**

## Next recommended tasks

1. **NHTSA bulk flat file ingestion** — replace API with bulk downloads for complete coverage
2. **Investigations and manufacturer communications** — extend ingestion to remaining data types
3. **vPIC vehicle normalization** — authoritative vehicle make/model/year lookup
4. **Component classification** — LLM-based canonical component categorization
5. **Celery background jobs** — async ingestion with Redis queue
6. **Neo4j graph population** — vehicle → component → evidence graph
7. **Text-to-SQL engine** — natural language → safe SQL queries
8. **GraphRAG retrieval** — semantic search over document chunks
