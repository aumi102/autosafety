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

*(That run predated revision `0005`. It was therefore repeated after `0005`
was authored, so the gate is proven against the true head:)*

```text
created temp database: autosafety_phase9_freshclone_0005
alembic upgrade head  rc=0
  Running upgrade  -> 2025_01_01_0001, Initial schema - Phase 0
  Running upgrade 2025_01_01_0001 -> 2025_01_01_0002, Phase 6: GraphRAG ...
  Running upgrade 2025_01_01_0002 -> 2025_01_01_0003, Phase 6 pgvector column.
  Running upgrade 2025_01_01_0003 -> 2025_01_01_0004, Phase 8: conversation state.
  Running upgrade 2025_01_01_0004 -> 2025_01_01_0005, Phase 9: execution audit ...
tables: 22   missing required: []
pgvector column on evidence_chunks: True
agent_runs columns: 25      tool_calls columns: 16
tool_calls unique constraints: ['uq_tool_call_run_call_id']
stamped version: 2025_01_01_0005
temp database dropped
```

**A fresh clone can now reconstruct the full schema, through head `0005`, from
tracked migrations.**

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

The *configured* states were then proven live, with a throwaway token exported
for the duration of the check only. An intentionally invalid request body was
used so that a `422` proves the guard cleared **without the maintenance handler
ever running**:

```text
admin_protection_enabled()          True

POST /v1/ingestion/nhtsa/phase1/run
  no token                  -> 401 ADMIN_TOKEN_REQUIRED
  wrong token               -> 401 ADMIN_TOKEN_REQUIRED
  valid X-Admin-Token       -> 422   (guard cleared, handler not run)
  valid token, query string -> 401   (header-only, as designed)
GET  /v1/conversations/status/config -> 200   (read-only, unaffected)

token present in any response body: False
token present in the OpenAPI schema: False
after unsetting, admin_protection_enabled(): False
```

Operator note: because the guard fails closed, a fresh deployment has **no**
working maintenance routes until `ADMIN_API_TOKEN` is set. Phase 9 therefore
documents the setting in `.env.example`, including how to generate one.

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

The re-verification run **did** reach the real provider, and the acceptance
report is corrected here to say so.

`Settings(_env_file=None)` was expected to select the deterministic provider,
but `PHASE7_PROVIDER_*` is exported in this machine's process environment, which
pydantic-settings reads regardless of `_env_file`. Two live
`openai_compatible` / `gpt-5.6-luna` requests were therefore made. They were not
wasted: they close the one invariant a deterministic run cannot close, proving
the audit trail records a **real** external provider end to end.

```text
turn 0  provider=openai_compatible  mode=llm        claims=3  citations=10  conf=high 0.85
turn 1  provider=openai_compatible  mode=repaired   claims=7  citations=10  conf=high 0.94
        (context inherited on turn 1; 0 accepted claims left uncited in either turn)

agent_runs
  phase_9  api_conversation  completed  openai_compatible  llm       accepted  tools=1  turn-linked  7778ms
  phase_9  api_conversation  completed  openai_compatible  repaired  repaired  tools=1  turn-linked  12141ms

tool_calls
  graphrag_retrieval_tool  retrieve_complaints_only  success  17 evidence  call_id=base-3725d6bd6f77  input_json={} output_json={}
  graphrag_retrieval_tool  retrieve_recalls_only     success  18 evidence  call_id=base-8da6370a161a  input_json={} output_json={}

duplicate (agent_run_id, call_id) groups: 0
privacy scan over the run's own rows:  agent_runs leaks=0   tool_calls leaks=0
after conversation hard delete:        agent_runs=0  tool_calls=0   (cascade holds)
```

Real provider → `GuardedAnswerService` → `AgentRun` → `ToolCall` is therefore
proven, not inferred. Turn 1 recorded `validation_outcome = repaired`, a second
audit outcome beyond the `accepted`/`rejected` pair seen in §8.

Corpus row counts were identical before and after the run, and the acceptance
conversations were hard-deleted afterwards.

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
954 passed, 4 warnings
```

Baseline at entry was 830. Phase 9 added 142 tests and superseded 18 Phase 8
admin tests that encoded the fail-open contract. The 4 warnings are the
pre-existing Starlette/httpx and Pydantic deprecations plus one Alembic config
deprecation.

### Hermeticity defect found during re-verification

The suite was first re-run with the Docker services **stopped**, which exposed a
defect the original run could not see:

```text
FAILED tests/test_phase9_admin_security.py::TestRouteEnforcement
       ::test_read_only_routes_are_unaffected[/v1/ingestion/source-runs]
```

`GET /v1/ingestion/source-runs` opens a PostgreSQL session inside its handler,
so asserting `200` on it silently made an offline security test depend on a live
database. The route's guard-related property does not need the handler to run,
so it is now asserted structurally — the route must not declare
`X-Admin-Token` — and only the genuinely dependency-free read-only route is
still executed for a real `200`. The suite now passes with every container
stopped.

## 13. Verdict

**PHASE 9 RUNTIME ACCEPTANCE PASSED WITH LIMITATIONS**

Migration history, fresh-database migration, existing-database migration,
fail-closed admin protection, the deprecated guarded chat bridge, and the
execution audit trail were all exercised against live infrastructure.

Remaining limitations: `ADMIN_API_TOKEN` is unset in this environment, so
maintenance routes are correctly unavailable rather than usable — an operator
must set it before running ingestion or rebuilds (now documented in
`.env.example`); audit is fail-open by design; no audit read API exists;
`make lint` still targets a non-existent `autosafety/` directory while the
package is `app/`, and pointing it at `app/` surfaces 633 pre-existing findings,
so that cleanup is deferred rather than folded into this phase; and the Phase
7/8 debts (live model-driven tool planning, lexical embeddings) are unchanged.
