# Phase 2: SQL Analytics Foundation

## Status: Complete

Safe, deterministic, template-based SQL analytics engine implemented.

## Scope

Phase 2 is NOT full LLM Text-to-SQL. Phase 2 creates a production-grade SQL analytics layer using approved templates, validated SQL execution, and answer contract formatting. This prepares the project for future LLM Text-to-SQL by establishing the schema registry, template system, and safety model.

## What was built

### `app/services/sql_analytics/schema_registry.py`
- `TABLES` dict defining allowed tables: `vehicles`, `components`, `complaints`, `recalls`, `recall_vehicle_links`, `source_runs`
- `ColumnDef` and `TableDef` dataclasses with descriptions
- `validate_table_access()` guard function
- `JOIN_PATHS` and `METRICS` constants for template authors

### `app/services/sql_analytics/templates.py`
- 6 SQL templates with named parameters and safe bound-parameter usage
- Templates: `top_complaint_components_by_vehicle`, `complaint_count_by_vehicle`, `recalls_by_vehicle`, `recall_count_by_vehicle`, `vehicles_by_complaint_count`, `complaint_count_by_component_for_vehicle`
- Each template uses `:param_name` placeholders — never interpolates raw user input

### `app/services/sql_analytics/question_parser.py`
- Deterministic entity extraction: make, model, model_year, limit
- F-150/F150/F 150 equivalence via `_strip_model_for_matching()`
- 7 intent classifications including `clarification_needed` and `unknown`
- No LLM calls — keyword/pattern-based

### `app/services/sql_analytics/executor.py`
- `execute_readonly_sql(session, sql, params, max_rows)` — validates, enforces LIMIT, binds params
- Uses `sql_safety.validate_sql()` before execution
- Fetches `max_rows + 1` rows to detect truncation
- Returns `ExecutionResult` with columns, rows, row_count, execution_ms, truncated, validated

### `app/services/sql_analytics/service.py`
- `SqlAnalyticsService` orchestration
- Flow: parse → classify → template select → SQL validate → execute → summarize → answer contract
- Deterministic natural-language summaries per intent
- Safety caveats injected automatically for complaint/recall queries
- `answer_sql_analytics_question()` convenience function

### API integration
- `POST /v1/chat/sessions/{id}/messages` — chat endpoint now routes SQL analytics questions through Phase 2 engine, returns `AnswerResponse`
- `POST /v1/sql-analytics/query` — direct SQL analytics endpoint accepting natural language questions

## Supported question types

| Question example | Intent |
|---|---|
| "Top complaint components for Ford F-150 2020" | `top_complaint_components_by_vehicle` |
| "How many complaints does Honda Accord 2021 have?" | `complaint_count_by_vehicle` |
| "List recalls for Ford F-150 2020" | `recalls_by_vehicle` |
| "How many recalls does Toyota Camry 2022 have?" | `recall_count_by_vehicle` |
| "Which vehicles have the most complaints?" | `vehicles_by_complaint_count` |
| "Complaint count by component for Toyota Camry 2022" | `complaint_count_by_component_for_vehicle` |

## SQL safety model

- All queries validated through `app/services/sql_safety.py` before execution
- Only SELECT/WITH allowed; INSERT, DROP, DELETE, UPDATE blocked
- Parameterized queries via SQLAlchemy `text()` with bound params
- LIMIT enforced (hard cap 500 rows)
- Read-only session (no autocommit write)

## Answer contract

All responses conform to `docs/contracts/answer_contract.md`:
- `intent: "sql"`
- `sql.used: true` with generated query, columns, rows, execution_ms, validated
- `warnings` includes safety caveats for complaint/recall analytics
- `confidence` assessed from result count and validation status

## How to run

```bash
# Run tests
pytest tests/ -v
pytest tests/test_phase2_sql_analytics.py -v

# Start API server
uvicorn app.main:app --reload --port 8000

# Try SQL analytics via curl
curl -X POST http://localhost:8000/v1/sql-analytics/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Top complaint components for Ford F-150 2020"}'

# Or via chat endpoint
curl -X POST http://localhost:8000/v1/chat/sessions/test-session/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Which vehicles have the most complaints?"}'
```

## Intentionally deferred

- Full LLM Text-to-SQL (natural language to arbitrary validated SQL)
- GraphRAG retrieval
- Neo4j graph population
- Vector embeddings
- Frontend/UI
- JWT auth
- Production file upload
- Full NHTSA bulk ingestion
- Investigations/TSB ingestion
- Eval dashboard

## Limitations

- Deterministic templates only — no LLM to handle novel question phrasings
- Year extraction context window limited — "2021 Honda" works, "Honda with issues in 2021" may not
- SQLite test fixtures use REPLACE(CAST()) for UUID comparison — production PostgreSQL uses native UUIDs
- No chat session persistence — responses return but are not stored
- No GraphRAG fallback when SQL returns no results

## Safety caveats maintained

- Complaint volume alone does not prove a safety defect
- Complaint records are user-submitted public reports — may be noisy
- Recall links are official only when campaign explicitly applies
- Semantic/component matches labeled as potential, not official causality

## Next recommended Phase 3 tasks

1. **LLM Text-to-SQL foundation** — wire in LLM to generate SQL from natural language using schema registry as context; validate generated SQL through safety module before execution
2. **GraphRAG retrieval** — semantic search over document chunks when SQL returns no results
3. **Chat session persistence** — store messages in chat_messages table
4. **Neo4j graph population** — vehicle → component → complaint/recall edges
5. **Complaint severity ranking** — injury/death flag weighted analytics
