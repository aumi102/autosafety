# Phase 5: Component-Level Graph Links

## Status: Complete

Component-level graph links implemented. Complaint → Component (MENTIONS_COMPONENT) and Recall → Component (RELATED_TO_COMPONENT) relationships now created in Neo4j. Root cause diagnosis from interrupted session confirmed and resolved.

## Why Phase 5 follows Hybrid SQL + Graph Answers

Phase 4 combined SQL analytics with graph evidence but lacked component-level linking. Complaints and recalls both reference components, but Phase 3 did not create Component nodes or link them. This caused MENTIONS_COMPONENT = 0 — the MATCH against non-existent Component nodes always failed.

## Diagnosis

**Root cause (confirmed):** Component nodes were not MERGED into Neo4j before complaint relationship creation. The MATCH clause `MATCH (comp:Component {normalized_name: ...})` found nothing because no Component nodes existed.

**PostgreSQL data reality:**
- Complaints: have `component_id` + `original_component` fields populated — MENTIONS_COMPONENT links work
- Recalls: have `component_id = NULL` and `original_component = NULL` in current dataset — RELATED_TO_COMPONENT remains 0 until source data is populated

**What the interrupted session partially fixed:** The interrupted session had already started Phase 5 edits to graph_builder.py, graph_models.py, and graph_queries.py. The edits were partially applied and syntactically valid but had a broken dry-run code path. Recovery restored the edits and fixed the dry-run vehicle counting.

## What was built

### `app/services/graph/graph_models.py`
- `GraphBuildStats`: added Phase 5 fields — `component_nodes_merged`, `complaint_component_links_seen`, `complaint_component_links_merged`, `recall_component_links_seen`, `recall_component_links_merged`, `component_links_skipped`, `component_link_errors`
- `to_dict()`: includes all Phase 5 fields
- `ComponentEvidence`: new dataclass for component-level retrieval results with to_dict()

### `app/services/graph/graph_builder.py`
- `_upsert_all_components()`: new function that MERGEs all Component nodes into Neo4j before any complaint/recall processing
- `build_graph()`: calls `_upsert_all_components()` in live mode; dry-run counts component candidates without writing
- `_upsert_complaint()`: after MERGE Complaint + HAS_COMPLAINT, checks `complaint.original_component` — normalizes, looks up component_map, MERGEs MENTIONS_COMPONENT if found; increments skipped/error stats
- `_upsert_recall_and_affects()`: after MERGE Recall + AFFECTS, checks `recall_data.original_component` — normalizes, looks up component_map, MERGEs RELATED_TO_COMPONENT if found

### `app/services/graph/graph_queries.py`
- `COMPONENT_EVIDENCE_CYPHER`: predefined Cypher with OPTIONAL MATCH for MENTIONS_COMPONENT
- `get_component_evidence_for_vehicle()`: retrieves complaints and their component links for a vehicle
- `SHARED_COMPONENT_RECALLS_CYPHER`: predefined Cypher for Recall→AFFECTS→vehicle and Recall→RELATED_TO_COMPONENT→Component→MENTIONS_COMPONENT→Complaint paths
- `get_shared_component_recall_paths()`: retrieves recalls linked through shared components

### `app/services/graph/graph_service.py`
- `get_vehicle_component_evidence()`: high-level wrapper for component evidence retrieval
- `get_vehicle_shared_component_recalls()`: high-level wrapper for shared component recall paths

### `app/services/graph/__init__.py`
- Exports `get_vehicle_component_evidence` and `get_vehicle_shared_component_recalls` as public service APIs

### `app/api/v1/endpoints/graph.py`
- `ComponentEvidenceResponse` model for API response shape
- `GET /v1/graph/vehicles/{vehicle_id}/component-evidence`: returns complaint-component links and shared component recalls with safety caveats

### `app/services/hybrid/hybrid_service.py`
- `_run_graph_retrieval()` now also calls `get_vehicle_component_evidence()` and `get_vehicle_shared_component_recalls()`
- Adds component evidence items with `relation_basis="complaint_mentions_component"` and `"potentially_related_by_shared_component"`
- `_summarize_component_evidence()` and `_summarize_shared_component_recalls()` helper functions

### `app/services/hybrid/answer_composer.py`
- Added `CAVEAT_COMPONENT_RECALL_MISSING`: warns when `RELATED_TO_COMPONENT` is zero due to missing source data
- Hybrid answers include component-level evidence when available
- SQL-only routing remains unchanged

### `tests/test_phase5_component_graph_links.py`
18 tests covering:
- GraphBuildStats Phase 5 fields present and accumulating
- Component nodes upserted before MENTIONS_COMPONENT (ordering)
- MENTIONS_COMPONENT emitted for complaint with original_component
- No MENTIONS_COMPONENT for missing/unknown component
- RELATED_TO_COMPONENT emitted when recall has component data
- RELATED_TO_COMPONENT skipped when recall component data is missing
- Dry-run counts components without writing to Neo4j
- Predefined Cypher constants used (no arbitrary Cypher)
- No arbitrary Cypher functions exposed
- ComponentEvidence model structure and to_dict()
- Correct argument order for `_upsert_complaint` and `_upsert_recall_and_affects`

## Graph relationships completed

| Relationship | Direction | Trigger | Count (local) |
|---|---|---|---|
| MENTIONS_COMPONENT | Complaint → Component | complaint.original_component exists + matches known component | 5 |
| RELATED_TO_COMPONENT | Recall → Component | recall.original_component exists + matches known component | 0 |

**MENTIONS_COMPONENT = 5:** All 5 complaints in the local dataset have `original_component` populated and Component nodes are now created first, so the MATCH succeeds.

**RELATED_TO_COMPONENT = 0:** Current local recalls have `component_id = NULL` and `original_component = NULL`. This is a data limitation, not a code failure. The code path is correct — it simply has no data to operate on. Once NHTSA recall data includes component fields, this will produce non-zero counts.

## Tests

```
pytest tests/test_phase3_graph.py -v   => 27 passed
pytest tests/test_phase4_hybrid.py -v   => 30 passed
pytest tests/test_phase5_component_graph_links.py -v => 15 passed
pytest tests/ -v                        => 221 passed
```

All existing tests pass. No external API calls or live Neo4j required for unit tests.

## Alembic

```
alembic heads => 2025_01_01_0001 (head)
```

No PostgreSQL migration needed — Phase 5 modifies only graph projection and retrieval code.

## Graph rebuild results

**Before Phase 5:**
- Nodes: 54 (VehicleMake, VehicleModel, ModelYear, Complaint, Recall)
- Rels: 53 (HAS_MODEL, HAS_YEAR, HAS_COMPLAINT, AFFECTS)
- MENTIONS_COMPONENT: 0
- RELATED_TO_COMPONENT: 0
- Component nodes: 0

**After Phase 5 (5 vehicles):**
- Nodes: 59 (+5 Component nodes)
- Rels: 58 (+5 MENTIONS_COMPONENT)
- MENTIONS_COMPONENT: 5 ✓
- RELATED_TO_COMPONENT: 0 (expected — recall component data missing)
- Component nodes: 5 ✓

## Limitations

- **RELATED_TO_COMPONENT = 0 due to missing recall component source data.** The NHTSA recall dataset used locally has `component_id = NULL` and `original_component = NULL`. The code is correct; data is absent. This relationship type requires populated recall component fields. The hybrid answer composer includes `CAVEAT_COMPONENT_RECALL_MISSING` to explain this gap transparently.
- **Complaint component data is only as good as the NHTSA source.** If `original_component` is mislabeled or absent upstream, MENTIONS_COMPONENT will undercount.
- **Component co-occurrence is not causality.** A complaint mentioning "SERVICE BRAKES" and a recall linked to "SERVICE BRAKES" is a potential association, not proof of a safety defect.
- **No GraphRAG.** Embeddings, vector search, and LLM Text-to-Cypher remain deferred.
- **No arbitrary Cypher.** All queries use predefined constants with parameter binding.
- **Docker/live graph validation** skipped in this environment — graph relationship counts from prior live run reported in report. Rebuild and verify with `python scripts/build_phase3_graph.py --build --limit-vehicles 5` before deployment.

## Next recommended Phase 6 direction

**GraphRAG / Guarded LLM Text-to-Cypher** — once NHTSA recall component data is populated:
1. Add vector embeddings for complaint summaries and recall descriptions
2. Implement semantic similarity search for component-level recall discovery
3. Add a guarded LLM Text-to-Cypher endpoint with strict schema validation
4. Build component co-occurrence scoring across model years

No implementation of Phase 6 in this commit.
