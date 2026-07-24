# Phase 7B: Tool Contracts and Safe Adapters — Report

## Status: Complete

## Scope

Phase 7B built the controlled application-owned tool layer. No LLM synthesis. No provider abstraction. No API endpoints. No CLI.

## Docs Followed

docs/00_project_brief.md, docs/01_system_architecture.md, docs/05_agent_workflow.md, docs/06_api_contract.md, docs/08_security_safety_guardrails.md, docs/contracts/answer_contract.md, docs/phase2_text_to_sql_analytics_report.md, docs/phase3_graph_foundation_report.md, docs/phase4_hybrid_sql_graph_answer_report.md, docs/phase5_component_graph_links_report.md, docs/phase6_graphrag_retrieval_foundation_report.md, docs/phase7_guarded_answer_synthesis_design.md.

## Existing Services Reused

### SQL Analytics (Phase 2)
- `app.services.sql_analytics.service.SqlAnalyticsService.answer(question: str) -> AnswerResponse`
- Reused for `sql_analytics_tool`. Takes natural-language question (built from structured args), uses predefined templates only, returns structured rows.
- Six operations supported: `top_complaint_components_by_vehicle`, `complaint_count_by_vehicle`, `recalls_by_vehicle`, `recall_count_by_vehicle`, `vehicles_by_complaint_count`, `complaint_count_by_component_for_vehicle`.
- No raw SQL, no mutation, parameterized queries only.

### Graph Services (Phase 3–5)
- `app.services.graph.get_vehicle_neighborhood(vehicle_id)` → `VehicleNeighborhood`
- `app.services.graph.get_vehicle_recall_paths(vehicle_id)` → `RecallPathResult`
- `app.services.graph.get_vehicle_component_evidence(vehicle_id)` → `ComponentEvidence`
- `app.services.graph.get_vehicle_shared_component_recalls(vehicle_id)` → `ComponentEvidence`
- Reused for `graph_evidence_tool`. Each function owns its session lifecycle internally. Predefined Cypher only.
- Four operations: `vehicle_neighborhood`, `recall_paths_by_vehicle`, `component_evidence_by_vehicle`, `shared_component_recall_paths`.

### GraphRAG (Phase 6)
- `retrieve_graphrag_evidence(question, top_k, source_type, make, model, model_year, include_graph)` → `GraphRAGRetrievalResult`
- Reused for `graphrag_retrieval_tool`. Receives callable injected by factory. Three operations: `retrieve_complaints_and_recalls`, `retrieve_complaints_only`, `retrieve_recalls_only`.
- GraphRAG owns its session lifecycle. Read-only.

### Vehicle Resolution
- `app.db.models.domain.Vehicle` ORM model with `normalized_make`, `normalized_model`, `model_year`.
- Reused for `vehicle_resolution_tool`. Deterministic exact lookup via SQLAlchemy ORM.
- No fuzzy matching, no LLM-generated queries.

## Implemented Tool Contracts

### base.py
- `ToolDefinition`: immutable schema metadata for LLM consumption. Fields: name, description, input_schema, read_only, max_result_items, timeout_seconds. No callable objects, no credentials.
- `ToolCallRequest`: structured call with call_id, tool_name, arguments. `make()` factory generates deterministic IDs.
- `ToolCallResult`: result with success/data/warnings/error_code/error_message/duration_ms/truncated. No raw tracebacks. Stable error codes.
- `ToolInputField` / `ToolInputSchema`: typed schema definitions.
- `EvidenceItem`: evidence with deterministic ID (`ev-{source_type}-{key}`) and citation ID (`cite-{source_type}-{key}`).
- `EvidenceBundle`: bounded collection with citation table builder.
- `ToolExecutionPolicy`: execution budgets.

### argument_validator.py
Strict field-level validation. Rules enforced:
- Forbidden keys rejected: `sql`, `query_sql`, `cypher`, `raw_query`, `raw_sql`, `database_url`, `password`, `api_key`, `secret`, `token`, `credential`, `n4ey`, `auth`, `connection`, `driver`, `session`, `execute`, `eval`, `exec`, `__`.
- Unknown argument names rejected.
- Required field enforcement.
- Type checks (bool not accepted for integer).
- String length limits.
- Integer range limits.
- Enum value enforcement.
- Nested structures rejected.
- Whitespace stripped from valid strings.

### registry.py
- `ToolRegistry`: centralized allowlist. Only registered tools executable. Deterministic registration order.
- `register(definition, adapter)`: raises on duplicate name.
- `execute(request)`: name check → argument validation → adapter execution → safe result. No eval, no exec, no dynamic imports.
- `execute_batch(requests)`: sequential execution.
- `list_definitions()`: sorted list of sanitized ToolDefinitions.
- `build_default_tool_registry(...)`: factory with explicit dependency injection. Returns registry with tools available based on provided dependencies.

### sql_adapter.py
- Wraps `SqlAnalyticsService.answer()` through tool interface.
- Builds natural-language question from structured args to reuse Phase 2 parser/templates.
- Result: bounded rows (max 50), truncated flag, sanitized metadata.
- Supports six Phase 2 template intents.
- `SQL_ANALYTICS_DEFINITION`: read_only, max_result_items=50, timeout=15s.

### graph_adapter.py
- Wraps Phase 3–5 service functions.
- Vehicle ID as primary input (resolved by vehicle_resolution_tool first).
- Four operations, each returning structured dicts.
- Graph paths capped at 20.
- Relation basis preserved: `official_recall_affects_vehicle`, `complaint_mentions_component`, `potentially_related_by_shared_component`.
- Safety caveats injected per operation.
- Neo4j unavailable → safe error result.
- `GRAPH_EVIDENCE_DEFINITION`: read_only, max_result_items=20, timeout=15s.

### graphrag_adapter.py
- Wraps `retrieve_graphrag_evidence()` via injected callable.
- Operation maps to Phase 6 source_type filter.
- Chunks bounded to 20, text truncated to 2000 chars.
- Citations, graph paths, warnings preserved.
- No embeddings exposed.
- Confidence label/score from Phase 6 (retrieval confidence only).
- `GRAPHRAG_RETRIEVAL_DEFINITION`: read_only, max_result_items=20, timeout=15s.

### vehicle_adapter.py
- Deterministic exact lookup via SQLAlchemy ORM.
- Uses `normalize_make` / `normalize_model` from ingestion module.
- Status values: `resolved`, `year_not_found` (with available_years), `ambiguous`, `not_found`.
- Never fabricates a vehicle ID.
- No raw SQL.

### bundle_builder.py
- `EvidenceBundleBuilder`: processes ToolCallResults into EvidenceBundle.
- Deduplication by evidence_id within extraction methods.
- Bounds: max 20 items, max 16000 chars total, max 2000 chars per item.
- Truncation flag set when limits reached.
- Forbidden keys stripped recursively from all metadata.
- Citation table built from items with citation_id.
- Tool call warnings propagated.

## Registered Allowlist

| Tool | Status |
|---|---|
| `sql_analytics_tool` | Registered when `sql_session_factory` provided |
| `graph_evidence_tool` | Registered when `neo4j_available=True` |
| `graphrag_retrieval_tool` | Registered when `graphrag_retrieval_fn` provided |
| `vehicle_resolution_tool` | Always registered |

## Validation Rules

- Forbidden key patterns: `sql`, `cypher`, `raw_query`, `database_url`, `password`, `api_key`, `secret`, `token`, `credential`, `n4ey`, `auth`, `connection`, `driver`, `session`, `execute`, `eval`, `exec`, `__`
- Required fields enforced
- Boolean rejected for integer type
- String empty/whitespace rejected
- Nested dicts/lists rejected
- Range limits on integers
- Enum values enforced

## Result Bounds

- SQL rows: max 50
- Graph paths: max 20
- GraphRAG chunks: max 20
- Evidence items in bundle: max 20
- Evidence chars total: max 16000
- Evidence chars per item: max 2000

## SQL Adapter Operations

All supported operations map to Phase 2 predefined templates:

1. `top_complaint_components_by_vehicle` — `TemplateId.TOP_COMPLAINT_COMPONENTS_BY_VEHICLE`
2. `complaint_count_by_vehicle` — `TemplateId.COMPLAINT_COUNT_BY_VEHICLE`
3. `recalls_by_vehicle` — `TemplateId.RECALLS_BY_VEHICLE`
4. `recall_count_by_vehicle` — `TemplateId.RECALL_COUNT_BY_VEHICLE`
5. `vehicles_by_complaint_count` — `TemplateId.VEHICLES_BY_COMPLAINT_COUNT`
6. `complaint_count_by_component_for_vehicle` — `TemplateId.COMPLAINT_COUNT_BY_COMPONENT_FOR_VEHICLE`

Note: `vehicle_comparison` from design is not supported by Phase 2 — omitted from adapter.

## Graph Adapter Operations

1. `vehicle_neighborhood` → `get_vehicle_neighborhood()`
2. `recall_paths_by_vehicle` → `get_vehicle_recall_paths()`
3. `component_evidence_by_vehicle` → `get_vehicle_component_evidence()`
4. `shared_component_recall_paths` → `get_vehicle_shared_component_recalls()`

## GraphRAG Adapter Operations

1. `retrieve_complaints_and_recalls` → `source_type=None`
2. `retrieve_complaints_only` → `source_type="complaint"`
3. `retrieve_recalls_only` → `source_type="recall"`

## Vehicle Resolution Behavior

- Exact match on (normalized_make, normalized_model, model_year) → `resolved=True`, vehicle_id returned
- Model/year combo not found but model exists → `year_not_found`, available_years listed
- Model matches multiple years → `ambiguous`, available_years listed
- No match found → `not_found`

## Evidence Bundle Behavior

- Deduplication within extraction: items with same evidence_id skipped
- Bounds enforced: items, char total, char per item
- Forbidden keys stripped from all metadata recursively
- Citation table built from items with citation_id
- Tool call warnings propagated
- No embeddings, SQL, Cypher, or credentials in bundle

## Security Review

| Check | Result |
|---|---|
| eval() | 0 occurrences |
| exec() | 0 occurrences |
| subprocess | 0 occurrences |
| importlib/__import__ | 0 occurrences |
| NEO4J_PASSWORD access | 0 occurrences |
| api_key credential exposure | 0 occurrences |
| DATABASE_URL in output | 0 occurrences |
| Raw SQL in arguments | Blocked by FORBIDDEN_KEYS |
| Raw Cypher in arguments | Blocked by FORBIDDEN_KEYS |
| LLM provider code | 0 occurrences |
| OpenAI/Anthropic | 0 occurrences |

DATABASE_URL_SYNC accessed internally in vehicle_adapter.py via `settings.DATABASE_URL_SYNC` — internal config access pattern matching Phase 2–6. Never appears in output.

## Test Results

```
pytest tests/test_phase7b_tools.py -v
79 passed in 0.37s
```

Category breakdown:
- A. Base contracts: deterministic serialization, no tracebacks, factory functions
- B. Argument validation: forbidden keys, types, ranges, enums, nested structures, whitespace
- C. Tool registry: allowlist, duplicate rejection, validation-before-execution, exception conversion, batch, order
- D. SQL adapter: definition fields, question builder, readonly, max rows, Phase 2 template coverage
- E. Graph adapter: readonly, max paths, no raw Cypher, credential sanitization
- F. GraphRAG adapter: readonly, operation→source_type mapping, chunk text bound, definition fields
- G. Vehicle adapter: readonly, required fields, no raw SQL
- H. Evidence bundle: deduplication, bounds, truncation, warnings, extraction by type
- I. Sanitization: credential key removal, non-dict handling, table formatting
- J. Security: no eval/exec/dynamic import/provider code
- K. Factory: dependency injection, tool registration, full registry

## Full Suite Regression

```
pytest tests/ -v
329 passed in 7.07s
```

No regressions. All existing 250 tests pass.

## Limitations

1. `vehicle_comparison` operation not implemented — Phase 2 SQL templates do not support it. Deferred.
2. GraphRAG adapter requires Phase 6 `retrieve_graphrag_evidence()` callable. When `neo4j_available=False` at app startup, graph expansion is skipped gracefully.
3. `RELATED_TO_COMPONENT = 0` in local corpus — shared-component associations limited by missing recall component data.
4. No live smoke test included in this unit test file — live smoke verified separately.

## Deferred Phase 7C Work

Phase 7C will implement:
- Provider abstraction (`SynthesisProvider` interface)
- Deterministic fallback provider (`DeterministicProvider`)
- Fake provider for tests (`FakeProvider`)
- Real LLM provider adapter (stub, `available() = False` without credentials)
- `prompt_builder.py` for system prompt construction
- `service.py` with bounded tool-call orchestration

Phase 7C handoff interfaces:
- `build_default_tool_registry(...)` → `ToolRegistry` with all 4 tools registered
- `SynthesisProvider` abstract class in `providers.py`
- `DeterministicProvider.synthesize(evidence_bundle, citation_table, safety_rules, config) -> SynthesisResult`
- `RealLLMProvider` stub with `available()` returning False
