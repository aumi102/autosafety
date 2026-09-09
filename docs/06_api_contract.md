# 06 — Backend API Contract

## API principles

1. Version all APIs under `/v1`.
2. Return stable IDs for app records.
3. Never expose internal database errors directly.
4. Persist every chat/agent run.
5. Include evidence payloads by default for analyst views.

## Health

```http
GET /healthz
GET /readyz
```

## Auth

```http
POST /v1/auth/register
POST /v1/auth/login
GET  /v1/me
```

MVP can start with local dev auth disabled behind `AUTH_ENABLED=false`, but the API shape should exist.

## Vehicle explorer

```http
GET /v1/vehicles/search?make=&model=&model_year=
GET /v1/vehicles/{vehicle_id}/overview
GET /v1/vehicles/{vehicle_id}/complaints
GET /v1/vehicles/{vehicle_id}/recalls
GET /v1/vehicles/{vehicle_id}/investigations
GET /v1/vehicles/{vehicle_id}/manufacturer-communications
```

### `GET /v1/vehicles/{vehicle_id}/overview`

```json
{
  "vehicle": {
    "id": "uuid",
    "make": "Ford",
    "model": "F-150",
    "model_year": 2022
  },
  "metrics": {
    "complaint_count": 0,
    "recall_count": 0,
    "investigation_count": 0,
    "manufacturer_communication_count": 0
  },
  "top_components": [
    {"component": "SERVICE BRAKES", "complaint_count": 0}
  ],
  "warnings": []
}
```

## Recall explorer

```http
GET /v1/recalls/search?campaign_number=&make=&model=&model_year=&component=
GET /v1/recalls/{recall_id}
GET /v1/recalls/{recall_id}/related-complaints
GET /v1/recalls/{recall_id}/graph
```

## Chat and agent runs

```http
POST /v1/chat/sessions                       (deprecated since Phase 9)
GET  /v1/chat/sessions
GET  /v1/chat/sessions/{session_id}
POST /v1/chat/sessions/{session_id}/messages (deprecated since Phase 9)
GET  /v1/agent-runs/{run_id}
GET  /v1/agent-runs/{run_id}/tool-calls
```

### Deprecation notice — `/v1/chat/*` (Phase 9)

The chat routes are **deprecated**. They previously bypassed
`GuardedAnswerService`; since Phase 9 they are a thin bridge over the guarded
conversation path. The response shape below is preserved, and responses carry:

```text
Deprecation: true
Link: </v1/conversations>; rel="successor-version"
```

Behavior notes: `session_id` must be a real conversation id (404 otherwise),
`POST /sessions` now persists a conversation, and `sql.query` / `sql.rows` are
always null because the guarded path never surfaces raw SQL.

Migration path: `/v1/chat/*` -> `/v1/conversations/*`.

`GET /v1/agent-runs/*` is **not implemented**. Phase 9 populates `agent_runs`
and `tool_calls` as a database-only audit trail with no public read surface;
exposing one requires authorization and redaction rules not yet defined.

## Guarded multi-turn conversations (Phase 8)

```http
POST   /v1/conversations
GET    /v1/conversations/{conversation_id}
GET    /v1/conversations/{conversation_id}/turns
POST   /v1/conversations/{conversation_id}/messages
DELETE /v1/conversations/{conversation_id}
GET    /v1/conversations/status/config
```

These return the `phase_8` guarded contract: validated claims, citations,
cross-turn citation provenance, deterministic confidence, and abstention.

### `POST /v1/chat/sessions/{session_id}/messages`

Request:

```json
{
  "content": "Top components with most complaints for Ford F-150 2022, then check related recalls.",
  "options": {
    "include_sql": true,
    "include_graph_paths": true,
    "max_rows": 20
  }
}
```

Response:

```json
{
  "message_id": "uuid",
  "run_id": "uuid",
  "intent": "hybrid",
  "answer": {
    "summary": "...",
    "sections": []
  },
  "sql": {
    "query": "SELECT ...",
    "rows": [],
    "columns": []
  },
  "evidence": {
    "citations": [],
    "graph_paths": []
  },
  "warnings": [],
  "confidence": {
    "label": "medium",
    "score": 0.71,
    "reasons": []
  }
}
```

## Ingestion APIs

These endpoints are admin-only and **fail closed** (Phase 9). They require an
`X-Admin-Token` header matching the operator-configured `ADMIN_API_TOKEN`:

```text
server token not configured  -> 503 ADMIN_PROTECTION_UNAVAILABLE
header missing or wrong      -> 401 ADMIN_TOKEN_REQUIRED
header correct               -> allowed
```

The same guard protects `POST /v1/graph/schema/setup`, `POST /v1/graph/build`,
and `POST /v1/graphrag/index`. The token is header-only, never a query
parameter.

```http
POST /v1/ingestion/runs
GET  /v1/ingestion/runs
GET  /v1/ingestion/runs/{run_id}
POST /v1/ingestion/nhtsa/complaints
POST /v1/ingestion/nhtsa/recalls
POST /v1/ingestion/nhtsa/investigations
POST /v1/ingestion/nhtsa/manufacturer-communications
```

### `POST /v1/ingestion/runs`

```json
{
  "sources": ["complaints", "recalls"],
  "scope": {
    "years": [2020, 2021, 2022, 2023, 2024, 2025, 2026],
    "makes": ["Ford", "Honda", "Toyota", "Tesla"]
  },
  "mode": "dry_run"
}
```

## Eval APIs

```http
GET  /v1/eval/questions
POST /v1/eval/runs
GET  /v1/eval/runs
GET  /v1/eval/runs/{run_id}
GET  /v1/eval/runs/{run_id}/failures
```

## Error shape

```json
{
  "error": {
    "code": "SQL_VALIDATION_FAILED",
    "message": "Only read-only SELECT/WITH queries are allowed.",
    "details": {}
  }
}
```
