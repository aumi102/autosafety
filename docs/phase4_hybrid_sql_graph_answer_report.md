# Phase 4: Hybrid SQL + Graph Evidence Answers

## Status: Complete

Hybrid SQL analytics + graph retrieval orchestration implemented. Deterministic composition of Phase 2 SQL results with Phase 3 graph evidence.

## Why Phase 4 follows SQL analytics and graph foundation

Phase 2 established safe, deterministic SQL analytics with 6 templates. Phase 3 built the Neo4j graph for multi-hop relationship traversal. Phase 4 combines both: SQL handles counts/rankings/trends while graph retrieval provides evidence paths through vehicle/component networks.

This enables richer answers that cite both SQL aggregations and graph traversal evidence — without requiring embeddings, vector search, or LLM reasoning.

## What was built

### `app/services/hybrid/hybrid_models.py`
- `HYBRID_INTENTS` set: supported hybrid question types
- `HybridIntent`: parsed hybrid question with sql_intent, hybrid_type, wants_graph, vehicle, confidence
- `GraphEvidenceItem`: single evidence item from graph retrieval with path_type, recall_campaigns, relation_basis
- `HybridAnswerResult`: SQL response + graph evidence + error state

### `app/services/hybrid/hybrid_parser.py`
- `parse_hybrid_question()`: deterministic parser — reuses Phase 2 parser for base intent + vehicle entity, then detects graph keywords
- `is_hybrid_question()`: quick routing check for chat endpoint
- Keyword patterns: "related recalls", "recall evidence", "complaints and recalls", "graph evidence"
- Hybrid types: top_complaint_component_with_related_recalls, complaint_count_with_related_recalls, vehicle_recall_evidence, component_complaints_with_graph_evidence, vehicles_complaint_count_with_recall_evidence
- Explicit opt-out: "without graph", "no recalls", "just sql"

### `app/services/hybrid/hybrid_service.py`
- `answer_hybrid_question()`: orchestrates parse → SQL analytics → graph retrieval → compose
- `_run_sql_analytics()`: calls Phase 2 service, returns dict
- `_run_graph_retrieval()`: uses make/model/year from parsed intent → vehicle_id lookup → graph recall paths
- `_find_vehicle_id()`: PostgreSQL lookup by normalized make/model/year
- Graceful Neo4j failure: returns SQL answer with warning when graph unavailable

### `app/services/hybrid/answer_composer.py`
- `compose_hybrid_answer()`: merges SQL + graph into AnswerResponse conforming to answer_contract.md
- Cautious confidence scoring: high (0.8) when SQL+graph, medium (0.6) SQL-only, low (0.3) no-data
- Automatic caveats:
  - "Complaint volume alone does not prove a safety defect."
  - "Graph relationships via shared vehicle/component are potential associations, not official causality."
  - "Graph evidence unavailable" when Neo4j not connected
- Answer sections: summary, SQL table, graph evidence, caveats

### `app/api/v1/endpoints/hybrid.py`
- `POST /v1/hybrid/query` with question + include_sql + include_graph + max_graph_paths
- Returns answer_contract dict with intent="hybrid"

### `app/api/v1/endpoints/chat.py` (updated)
- Routes hybrid questions (detect via `is_hybrid_question()`) to Phase 4 hybrid service
- Routes SQL-only questions to Phase 2 SQL analytics
- Returns `phase="phase_4"` or `phase="phase_2"` in response

## Supported hybrid question types

| Question | SQL intent | Graph evidence |
|---|---|---|
| "Top component + related recalls for Ford F-150 2020" | top_complaint_components | recall paths |
| "Complaints and recall evidence for Honda Accord 2021" | complaint_count_by_vehicle | recall paths |
| "Does Toyota Camry have complaints and recalls?" | vehicle_recall_count | recall paths |
| "Vehicles with most complaints + graph evidence" | vehicles_by_complaint_count | neighborhood |
| "complaints by component with recall evidence" | complaint_count_by_component | recall paths |

## Architecture flow

```
User question
    ↓
is_hybrid_question() → bool
    ↓ (True)
parse_hybrid_question()
    ↓
Phase 2: answer_sql_analytics_question() → SQL result dict
    ↓
if hybrid intent + vehicle extracted:
    _find_vehicle_id(make, model, year) → vehicle_id
    graph_vehicle_recall_paths(vehicle_id) → GraphEvidenceItem[]
    ↓
compose_hybrid_answer() → AnswerResponse(intent="hybrid")
    ↓
answer_contract dict (sql + graph_paths + warnings + confidence)
```

## Safety/correctness caveats

All Phase 4 answers include mandatory caveats:

1. **Complaint volume:** "Complaint volume alone does not prove a safety defect."
2. **Potential relation:** "Graph relationships via shared vehicle/component are potential associations, not official causality."
3. **Only AFFECTS is official:** Recall → AFFECTS → ModelYear links are official when campaign explicitly applies. RELATED_TO_COMPONENT and complaint-recall co-occurrence are potential only.
4. **Neo4j unavailable:** SQL answer returned with graph warning when Neo4j is down.

## Limitations

- Phase 4 uses Phase 3 graph — if graph is stale/empty, graph evidence will be absent
- Deterministic templates only — no LLM to handle novel question phrasings
- Year extraction context window limited (Phase 2 parser)
- No chat session persistence
- Graph neighborhood may not include MENTIONS_COMPONENT or RELATED_TO_COMPONENT if Phase 3 build didn't create those links (depends on component normalization coverage)

## Intentionally deferred

- Full GraphRAG with semantic chunk retrieval
- Vector embeddings and semantic similarity
- LLM Text-to-Cypher
- LLM Text-to-SQL
- Embedding-based complaint-recall similarity linking
- Graph visualization frontend
- Background job for large graph rebuilds
- JWT auth

## Next recommended Phase 5 tasks

1. **Full GraphRAG retrieval** — semantic search over complaint/recall text chunks using vector similarity
2. **LLM Text-to-Cypher** — wire LLM to generate safe Cypher from natural language, validate through sql_safety module
3. **LLM Text-to-SQL** — extend Phase 2 with LLM-generated SQL using schema registry as context
4. **Hybrid answer ranking** — score and rank SQL results + graph evidence by relevance using LLM
5. **Component neighborhood query** — given a component, find all vehicles/complaints/recalls connected to it
6. **Complaint-to-recall graph traversal** — path finding between complaints and recalls through shared components
