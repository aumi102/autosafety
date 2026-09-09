# Phase 9 — Runtime Acceptance Report

## 1. Scope

Live acceptance of the Phase 9 migration fix, fail-closed admin guard, legacy
chat bridge, and execution audit against the running local infrastructure.
Existing containers and volumes were reused; no data was re-ingested and no
volume was reset.

## 2. Infrastructure verified

| Service | Image | State |
|---|---|---|
| PostgreSQL + pgvector | `pgvector/pgvector:pg16` | Up 12 days, healthy |
| Neo4j | `neo4j:5-community` | Up 12 days, healthy |
| Redis | `redis:7-alpine` | Up 12 days — present, still unused by design |

Neo4j was exercised for real: every live guarded run reported
`neo4j_available: true` and executed 2–3 tool calls.

The local corpus has grown since the Phase 8 report (1308 complaints, 57
recalls, 1365 evidence documents, 1880 chunks, 10 vehicles), so retrieval in
these runs is materially richer than Phase 8's 5-complaint corpus.

## 3. Fresh-clone migration proof (mandatory gate)

A temporary database was created on the existing PostgreSQL container, migrated
from empty to head, inspected, then dropped. The user's database was not
touched by this step.

```text
created temp database: autosafety_phase9_migrationtest
alembic upgrade head  rc=0
  Running upgrade  -> 2025_01_01_0001, Initial schema - Phase 0
  Running upgrade 2025_01_01_0001 -> 2025_01_01_0002, Phase 6: GraphRAG ...
  Running upgrade 2025_01_01_0002 -> 2025_01_01_0003, Phase 6 pgvector column.
  Running upgrade 2025_01_01_0003 -> 2025_01_01_0004, Phase 8: conversation state.
tables: 22   missing required: []
pgvector column present: True
chat_sessions.last_activity_at present: True
stamped version: 2025_01_01_0004
temp database dropped
```

*(Run before revision `0005` was authored; `0005` was subsequently applied to
the live database, below, and the offline chain tests cover it.)*

**A fresh clone can now reconstruct the schema from tracked migrations.**

## 4. Existing database migration

```text
alembic current  (before)  2025_01_01_0004
alembic heads              2025_01_01_0005 (head)   ← exactly one
alembic upgrade head       0004 -> 0005 applied
alembic current  (after)   2025_01_01_0005 (head)
```

Schema after upgrade:

```text
agent_runs   25 columns   all 16 new audit columns present
tool_calls   16 columns   all 8 new audit columns present
unique constraint on tool_calls: uq_tool_call_run_call_id
```

**No data loss.** Row counts identical before and after:

```text
complaints 1308   recalls 57   vehicles 10
evidence_documents 1365   evidence_chunks 1880   source_runs 5
```

## 5. Admin protection — live, fail-closed

`ADMIN_API_TOKEN` is **not configured** in this environment, so the guard is in
its unavailable state. Every mutation route now refuses:

```text
admin_protection_enabled() -> False

POST /v1/ingestion/nhtsa/phase1/run  -> 503 ADMIN_PROTECTION_UNAVAILABLE
POST /v1/graph/build                 -> 503 ADMIN_PROTECTION_UNAVAILABLE
POST /v1/graphrag/index              -> 503 ADMIN_PROTECTION_UNAVAILABLE

GET  /v1/ingestion/source-runs       -> 200   (read-only, unaffected)
GET  /v1/conversations/status/config -> 200   (read-only, unaffected)
```

Log line emitted, naming the setting and never a value:

```text
Maintenance route refused: no usable ADMIN_API_TOKEN is configured.
```

This is the Phase 8 exposure closed: the same environment previously left these
routes publicly callable.

## 6. Legacy chat bridge — live

```text
POST /v1/chat/sessions/not-a-uuid/messages
  -> 404 CHAT_SESSION_NOT_FOUND
  -> Deprecation: true
  -> Link: </v1/conversations>; rel="successor-version"

OpenAPI deprecated flags:
  /v1/chat/sessions                        post.deprecated = True
  /v1/chat/sessions/{session_id}/messages  post.deprecated = True
```

The deprecation headers on the **error** path are the fix for the defect found
during this acceptance run (see `docs/phase9_implementation_report.md` §7).

## 7. Guarded answer and multi-turn conversation

Run against live PostgreSQL and live Neo4j with the deterministic provider
(`Settings(_env_file=None)`), so no external request was made.

Single-turn guarded answer through the audited wrapper:

```text
mode=deterministic  provider=deterministic  abstained=False
claims=3  citations=11  confidence=high 0.85
neo4j_available=True  tool_calls=2
```

Multi-turn conversation (3 turns):

| Turn | Context applied | Mode | Claims | Citations | Uncited claims |
|---|---|---|---|---|---|
| 0 | false | deterministic | 3 | 11 | **0** |
| 1 | **true** | deterministic | 5 | 11 | **0** |
| 2 | true | deterministic | 3 | 20 | **0** |

Phase 8 invariants held: context inherited on turns 1–2, and every accepted
factual claim cited evidence retrieved in its own turn.

## 8. Execution audit rows

`AgentRun` rows for the acceptance conversation, read back with read-only SQL:

```text
phase     surface            status     provider       mode           abstained  fallback  conf   validation   tools  linked  ms
phase_9   api_conversation   completed  deterministic  deterministic  false      false     high   accepted     2      true    166
phase_9   api_conversation   completed  deterministic  deterministic  false      false     high   rejected     2      true    109
phase_9   api_conversation   completed  deterministic  deterministic  false      false     high   accepted     3      true    208
```

Turn 1 recorded `validation_outcome = rejected` — the audit trail captured
Phase 7 rejecting a claim and rescuing deterministically, which is exactly the
kind of event this phase exists to make visible.

`ToolCall` rows (sample), from **real** tool executions:

```text
tool_name                 operation                    success  evidence_items  call_id             input_json  output_json
graphrag_retrieval_tool   retrieve_complaints_only     true     17              base-bcd6b825f4bb   {}          {}
graphrag_retrieval_tool   retrieve_recalls_only        true     18              base-e7b151b6f6ec   {}          {}
graph_evidence_tool       recall_paths_by_vehicle      true     20              orch-542d4c7bb7c5   {}          {}
vehicle_resolution_tool   (none)                       true      0              orch-419458bb64bc   {}          {}
```

Totals and integrity checks:

```text
runs linked to a turn:            3 / 3
distinct (agent_run_id, call_id): 11 of 11 tool_calls   → no duplicates
tool_calls with a non-empty payload column: 0
runs remaining after conversation delete:   0           → cascade works
```

## 9. Audit privacy scan on live data

Every persisted audit value was scanned directly in PostgreSQL:

```text
agent_runs rows matching (api_key|password|postgresql://|bolt://|redis://|sk-|Bearer): 0
tool_calls rows matching (api_key|password|postgresql://|bolt://|select |match (|sk-): 0
tool_calls with non-empty input_json/output_json: 0
```

No prompt, provider response, credential, raw SQL, or raw Cypher reached the
audit trail.

## 10. Real LLM

**No live external request was spent.**

Phase 7 and Phase 8 already proved the real provider
(`openai_compatible` / `gpt-5.6-luna`) end to end. Phase 9 concerns
infrastructure, security, and observability, and the audit path records
`provider` from the guarded contract through identical code regardless of which
provider produced it. A paid request would have demonstrated nothing the
deterministic run does not already demonstrate, so per the phase's own guidance
it was not spent.

## 11. Offline evaluations (regression)

```text
scripts/evaluate_phase7_answers.py   passed=True  cases=20  failed_gates=[]
scripts/evaluate_phase8_conversations.py
    passed=True  cases=10  turns=20  failed_gates=[]
    citation_validity 1.0   citation_coverage 1.0
    conversation_isolation 1.0   prompt_injection_resistance 1.0
    prior_text_leak 0.0   prior_citation_leak 0.0
    causal_guard 1.0   context_bounds 1.0   determinism 1.0
```

## 12. Test suite

```text
pytest tests/ -q
953 passed, 4 warnings
```

Baseline at entry was 830. Phase 9 added 141 tests and superseded 18 Phase 8
admin tests that encoded the fail-open contract. The 4 warnings are the
pre-existing Starlette/httpx and Pydantic deprecations plus one Alembic config
deprecation.

## 13. Verdict

**PHASE 9 RUNTIME ACCEPTANCE PASSED WITH LIMITATIONS**

Migration history, fresh-database migration, existing-database migration,
fail-closed admin protection, the deprecated guarded chat bridge, and the
execution audit trail were all exercised against live infrastructure.

Remaining limitations: `ADMIN_API_TOKEN` is unset in this environment, so
maintenance routes are correctly unavailable rather than usable — an operator
must set it before running ingestion or rebuilds; audit is fail-open by design;
no audit read API exists; and the Phase 7/8 debts (live model-driven tool
planning, lexical embeddings) are unchanged.
