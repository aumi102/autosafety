# Phase 1.5 Ingestion Report

## Status: Complete

Reliable complaints ingestion foundation implemented via flat-file ingestion.

## Why Phase 1.5 exists

Phase 1 successfully ingested recalls via NHTSA recall API. Phase 1 complaint ingestion via NHTSA EIEARS API (`/complaints/complaintsByVehicle`) proved unreliable for certain model names — specifically Ford F-150 returns HTTP 400 or empty results for some years due to how the API handles model name normalization (dashes/spaces).

Flat-file ingestion solves this by:
1. Accepting pre-processed complaint CSV files from NHTSA flat-file exports or validated sources.
2. Filtering client-side for seed vehicle scope.
3. Normalizing F-150/F150/F 150 variants robustly.
4. Providing reliable, reproducible ingestion with full deduplication.

## What was built

### `app/services/ingestion/complaints_flat_file.py`

- `run_complaints_flat_file_ingestion()` — main ingestor
  - Streams CSV with defensive row parsing (one malformed row doesn't crash ingestion)
  - Filters rows by seeded vehicles (make/model/model_year)
  - F-150/F150/F 150 equivalence via `_strip_model_for_matching()` + uppercase
  - Upserts vehicles, components, complaints
  - Deduplication by ODI number (`nhtsa_complaint:{odi}`)
  - Stores raw source rows with `source_name = nhtsa_complaints_flat_file_phase1_5`
  - Creates `source_run` record
  - Returns `ComplaintsFlatFileStats` with counts: `vehicles_seen`, `complaint_rows_seen`, `complaint_rows_matched`, `complaints_inserted`, `complaints_skipped_duplicates`, `rows_skipped`, `errors_count`

### `app/services/ingestion/normalization.py`

- `models_equivalent(a, b)` — case-insensitive F-150/F150/F 150 matching

### `app/services/ingestion/data_quality.py`

- Extended `DataQualitySummary` with:
  - `complaints_missing_vehicle_link`
  - `duplicate_odi_candidates`
  - `complaints_flat_file_runs` (list of flat-file source runs with status, row_count)

### CLI `scripts/ingest_phase1_nhtsa.py`

New flags:
- `--complaints-flat-file PATH` — path to local complaints CSV
- `--complaints-source api|flat-file|auto` — select complaint source

Behavior:
- `flat-file`: always use local file ingestion
- `api`: always use Phase 1 API ingestion
- `auto` (default): use flat-file if `--complaints-flat-file` provided, else API

### API endpoint

`POST /v1/ingestion/nhtsa/phase1-5/complaints-flat-file/run`

Request:
```json
{
  "seed_path": "data/seeds/phase1_vehicles.csv",
  "complaints_flat_file_path": "/path/to/complaints.csv",
  "dry_run": false,
  "limit_vehicles": null
}
```

For developer/local use only. Production upload/storage deferred.

### `GET /v1/ingestion/data-quality/summary`

Extended with Phase 1.5 complaint quality metrics.

## Exact command examples

```bash
# Dry run with fixture CSV
python scripts/ingest_phase1_nhtsa.py \
  --seed data/seeds/phase1_vehicles.csv \
  --complaints-only \
  --complaints-source flat-file \
  --complaints-flat-file tests/fixtures/nhtsa_complaints_sample.csv \
  --dry-run

# Live ingestion with fixture CSV
python scripts/ingest_phase1_nhtsa.py \
  --seed data/seeds/phase1_vehicles.csv \
  --complaints-only \
  --complaints-source flat-file \
  --complaints-flat-file tests/fixtures/nhtsa_complaints_sample.csv

# Ingestion with real NHTSA flat-file (replace path with actual file)
python scripts/ingest_phase1_nhtsa.py \
  --seed data/seeds/phase1_vehicles.csv \
  --complaints-only \
  --complaints-source flat-file \
  --complaints-flat-file /path/to/real/nhtsa_complaints_export.csv
```

## CSV format expected

```csv
make,model,model_year,odi_number,component,summary,crash,fire,injury,death,received_date,incident_date,source_url
Ford,F-150,2020,11420001,SERVICE BRAKES,Brake issue,N,N,N,N,20230415,20230320,https://api.nhtsa.gov/complaints/complaint?odi=11420001
```

Required columns: `make`, `model`, `model_year`
Optional but recommended: `odi_number`, `component`, `summary`, `received_date`, `incident_date`, `source_url`, `crash`, `fire`, `injury`, `death`

Date format: `YYYYMMDD` (NHTSA standard).

## Limitations

1. **Real NHTSA flat-file not included.** The fixture CSV is synthetic test data only. Developers must obtain real complaint exports from NHTSA flat-file downloads.
2. **Complaints only.** Flat-file path currently handles complaints only. Recalls still use Phase 1 API ingestion.
3. **File path must be server-local.** No HTTP upload or cloud storage yet. Production upload deferred.
4. **No bulk automation.** Running flat-file ingestion requires manual file preparation.

## Safety caveats (maintained from Phase 1)

- **Complaint volume does not prove a defect.** Every complaint count display includes this caveat.
- **Complaint records are user-submitted public reports and may be noisy.**
- **Recall links are official only when campaign records explicitly apply to the vehicle.**
- **Semantic/component matches must be labeled as potential, not official causality.**
- **Do not claim all NHTSA complaints are ingested.** Phase 1.5 handles seed-scope flat-file ingestion only.

## Deferred to Phase 2+

- Real NHTSA flat-file bulk downloads automation
- Investigations ingestion
- Manufacturer communications ingestion
- Production file upload API
- Bulk flat-file recall ingestion
- vPIC vehicle normalization
- Celery background jobs for ingestion
- Neo4j graph population
- Text-to-SQL and GraphRAG
- Frontend/UI, JWT auth, eval dashboard

## Next recommended Phase 2 tasks

1. **Automate NHTSA flat-file download** — script to pull complaints CSV from NHTSA downloads site.
2. **Flat-file recall ingestion** — extend pattern to recalls CSV.
3. **Investigations ingestion** — NHTSA investigations flat file.
4. **Manufacturer communications ingestion** — TSB flat file.
5. **Production upload API** — multipart file upload with validation pipeline.
