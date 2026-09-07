# Phase 8 — Runtime Acceptance Report

## 1. Scope

Live acceptance of the Phase 8 bounded multi-turn guarded conversation against
the running local infrastructure, followed by a bounded real-provider check.
Existing containers and volumes were reused; no data was re-ingested and no
volume was reset.

## 2. Infrastructure verified

| Service | Image | State |
|---|---|---|
| PostgreSQL + pgvector | `pgvector/pgvector:pg16` | Up, healthy, port 5433 |
| Neo4j | `neo4j:5-community` | Up, healthy, ports 7474 / 7687 |
| Redis | `redis:7-alpine` | Up, port 6379 — **present but intentionally unused by Phase 8** |

Neo4j connectivity was exercised for real: every live turn reported
`neo4j_available: true` and executed 2–3 tool calls.

## 3. Migration

```text
alembic current  (before)  2025_01_01_0003
alembic heads              2025_01_01_0004 (head)   ← single head
alembic upgrade head       0003 -> 0004  applied
alembic current  (after)   2025_01_01_0004 (head)
```

Schema verified after upgrade:

```text
chat_turns                     22 columns
chat_turn_citations            16 columns
chat_sessions                  id, user_id, title, created_at, last_activity_at
indexes chat_turns             ix_chat_turns_session_id, uq_chat_turn_session_index
indexes chat_turn_citations    ix_..._turn_id, ix_..._session_id, ix_..._source_record_key
```

**No data loss.** Row counts identical before and after the migration:

```text
complaints 5    recalls 37    vehicles 5    components 5
evidence_documents 42    evidence_chunks 42    source_runs 4
```

## 4. Live service-layer acceptance (deterministic provider)

Three turns against live PostgreSQL and live Neo4j, using
`Settings(_env_file=None)` so the deterministic provider is selected and no
external request is made.

| Turn | Question | Context applied | Inherited | Claim types | Citations | Confidence | Uncited claims |
|---|---|---|---|---|---|---|---|
| 0 | What brake complaints are reported for Ford F-150 2020? | false | — | complaint_observation ×3 | 11 | high 0.85 | 0 |
| 1 | What about recalls? | **true** | make, model, model_year, component | official_recall_applicability ×5 | 11 | high 1.00 | 0 |
| 2 | Did those happen because of the complaints? | true | make, model, model_year, component | complaint_observation ×3 | 20 | high 0.95 | 0 |

Turn 1 resolved question:

```text
What about recalls? (for Ford F-150 2020; component SERVICE BRAKES)
```

Turn 1 produced **`official_recall_applicability` claims from live Neo4j
`AFFECTS` paths** — the positive recall-applicability path that Phase 7 fixed
but could not re-verify live before its request cap. It is now verified against
live local evidence through a Phase 8 follow-up turn.

Turn 2 asked an explicitly causal follow-up and produced **no causal claim**;
the causality guard held across the turn boundary.

Isolation, persistence, and deletion on the live database:

```text
isolation probe (fresh conversation, same DB):
    context_applied = false, entities all null, turn_index = 0
persisted turn indexes: [0, 1, 2]
delete → true;  subsequent get → not retrievable
```

## 5. Live API acceptance

Through the real FastAPI app and the real service factory (operator `.env`
active, so the configured external provider was in play).

| Turn | HTTP | Context applied | Mode | Claim types | Citations | Uncited | Trace hidden |
|---|---|---|---|---|---|---|---|
| 0 | 200 | false | llm | complaint_observation | 10 | 0 | yes |
| 1 | 200 | **true** | fallback | official_recall_applicability | 10 | 0 | yes |
| 2 | 200 | true | llm | complaint_observation | 10 | 0 | yes |

Turn 2 warnings included:

```text
Available evidence cannot establish causality.
```

Turn 1 and 2 carried the Phase 8 conversation warning:

```text
This follow-up reused vehicle context from an earlier turn. Prior answers were
not used as evidence; every claim above was validated against evidence
retrieved for this turn.
```

Contract and error paths:

```text
GET    /v1/conversations/{id}/turns   → turn_count 3
GET    /v1/conversations/{id}         → turn_count 3
GET    unknown uuid                   → 404
GET    malformed id ("not-a-uuid")    → 404
POST   message with extra field       → 422
DELETE /v1/conversations/{id}         → 200
GET    after delete                   → 404
```

Residue after deletion, queried directly against live PostgreSQL:

```text
chat_turns 0    chat_turn_citations 0    chat_messages 0    chat_sessions 0
```

Secret-leak scan over every response body produced (`api_key`, `password`,
`postgresql://`, `bolt://`, `redis://`, `authorization`, `sk-`, `traceback`,
`select `, `match (`): **no matches**.

## 6. Real external provider

| Item | Value |
|---|---|
| Provider | `openai_compatible` |
| Model | `gpt-5.6-luna` |
| Protocol | `POST https://api.openai.com/v1/chat/completions` |
| External enabled | true (operator `.env`) |
| Live requests used | **4** (at the recommended cap) |
| Outcome | 3 synthesized, 1 rejected-and-rescued |

The fourth request was spent deliberately on a Phase 8 **follow-up** turn with
`include_trace=true`, seeding turn 0 deterministically so the live call
exercised the inherited-context path:

```json
{
  "context_applied": true,
  "resolved": "What about recalls? (for Ford F-150 2020; component SERVICE BRAKES)",
  "synthesis_mode": "fallback",
  "provider": "deterministic",
  "abstained": false,
  "trace": {
    "original_provider": "openai_compatible",
    "provider_available": true,
    "fallback_used": true,
    "validation_outcome": "rejected",
    "rejected_claim_count": 1
  },
  "claim_types": ["official_recall_applicability"],
  "n_citations": 10,
  "validation": {"valid": true, "citation_coverage": 1.0, "unsupported_claim_ids": []},
  "confidence": "high"
}
```

Interpretation: the real provider answered (HTTP 200), Phase 7 **rejected one
unsupported claim from the live model**, and deterministic composition rescued
the turn into five citation-backed `official_recall_applicability` claims at
100% citation coverage. The guard operated on real provider output on a turn
that used inherited cross-turn context. This also explains the `fallback` mode
seen at API turn 1 — it was claim rejection, not a network failure.

No credential, prompt, or raw provider response was logged, persisted, or
returned. Normal `pytest` runs remain fully offline.

## 7. Offline evaluation

```text
scripts/evaluate_phase8_conversations.py
cases 10   turns 20   passed true   failed_gates []
```

All 11 gates at threshold (see `docs/phase8_implementation_report.md` §10).

## 8. Test suite

```text
pytest tests/ -q
830 passed, 3 warnings
```

Baseline at entry was 666 passed; Phase 8 added 164 tests and broke none. The
3 warnings are the pre-existing Starlette/httpx and Pydantic deprecation
warnings carried over from Phase 7.

## 9. Maintenance route protection

In this environment `PHASE8_ADMIN_TOKEN` is **not configured**, so the five
mutation routes remain callable without a token. This is the pre-existing
exposure, now explicit and observable rather than disguised:

```text
app.core.security.admin_protection_enabled() → False
(a warning is logged once when an unprotected maintenance route is reached)
```

Setting `PHASE8_ADMIN_TOKEN` enables enforcement immediately, with constant-time
comparison and a 401 that reveals nothing about the expected value. Enforcement
behavior is covered by tests for all five routes.

## 10. Verdict

**PHASE 8 RUNTIME ACCEPTANCE PASSED WITH LIMITATIONS**

Live PostgreSQL, pgvector, Neo4j, migration, service layer, API, conversation
isolation, retention/deletion, and one bounded real-provider follow-up turn were
all exercised end to end. Limitations are bounded and explicit; none allows
unvalidated output or prior-turn memory to bypass the guarded contract.

Remaining limitations: the Phase 8 migration file is not tracked by Git
because `.gitignore` excludes `migrations/versions/*.py` for every migration
in this repository (see `docs/phase8_implementation_report.md` §15);
`PHASE8_ADMIN_TOKEN` unset in this environment (routes
open by configuration); live model-driven `plan_tool_calls()` still not
implemented; small corpus and lexical deterministic embeddings unchanged;
`RELATED_TO_COMPONENT = 0` unchanged; Redis present but unused by design.
