# Phase 9 — Deployability, Security, and Observability (Design)

## 1. Objective

Phase 9 hardens the platform **before** any further product intelligence is
added. It changes no answer semantics: the Phase 7 guarded path and the Phase 8
conversation contract are untouched.

Authoritative path, unchanged:

```text
User / Conversation
→ application-owned context
→ GuardedAnswerService
→ SynthesisOrchestrator
→ mandatory GraphRAG
→ optional allowlisted tools
→ LLM or deterministic fallback
→ Phase 7 evidence/citation validation
→ Phase 8 cross-turn provenance checks
→ final guarded answer
```

Phase 9 adds a **side channel** around that path (audit), closes two real
exposures (untracked migrations, fail-open admin guard), and removes one unsafe
bypass (legacy chat).

## 2. Scope decision

The repository does not define Phase 9. The only forward-looking statements are
two Phase 8 handoff notes:

- `docs/phase8_implementation_report.md` §15 — remove the
  `migrations/versions/*.py` ignore rule and commit all migrations together.
- `docs/phase8_design.md` §12 — "Retiring the legacy surface is a Phase 9
  decision."

So Phase 9 implements the documented debt plus the minimal coherent
infrastructure slice around it, in priority order:

1. migration history correctness
2. maintenance/admin route protection
3. legacy chat decision
4. execution observability via `agent_runs` / `tool_calls`
5. runtime acceptance
6. documentation

## 3. Non-goals

Long-term memory, personalization, frontend, new retrieval architecture, broad
corpus ingestion, new provider families, autonomous agent loops, full JWT
identity, and any Phase 10 functionality.

## 4. Priority 1 — Migration history correctness

### Problem

`.gitignore:27` excluded `migrations/versions/*.py`. **None** of the four
revisions were tracked, so a fresh clone contained an empty `versions/`
directory and `alembic upgrade head` was a no-op — the schema could not be
rebuilt from the repository at all.

### Decision

Remove the ignore rule and track the **complete** chain in one change.

Tracking only the newest revision would have been worse than tracking none: a
clone would hold `0004` whose `down_revision` is `0003`, and Alembic would fail
with an unresolvable revision. This is exactly why Phase 8 deferred the fix
rather than half-applying it.

Historical revision contents are **not** rewritten. No revision is regenerated,
squashed, or back-dated; the real history is simply made visible.

### Resulting chain

```text
<base> -> 2025_01_01_0001  Initial schema (Phase 0)
       -> 2025_01_01_0002  Phase 6 GraphRAG documents/chunks
       -> 2025_01_01_0003  Phase 6 pgvector column
       -> 2025_01_01_0004  Phase 8 conversation state
       -> 2025_01_01_0005  Phase 9 execution audit   (head)
```

### Gate

A fresh, empty database must reach head. Proven in
`docs/phase9_runtime_acceptance_report.md` §3 against a temporary database, and
guarded offline by `tests/test_phase9_migrations.py`, which verifies tracking,
uniqueness, a single base, a single head, resolvable `down_revision` links,
linearity, and that the chain reaches every revision.

## 5. Priority 2 — Maintenance/admin protection (fail-closed)

### Problem

Phase 8's guard was fail-**open**: with `PHASE8_ADMIN_TOKEN` unset, ingestion,
graph rebuild, and index rebuild stayed publicly callable. A deployment that
simply forgot the setting was silently unprotected.

### Decision

Fail **closed**, in `app/core/security.py`:

| Server token | Request header | Result |
|---|---|---|
| not configured | anything | **503 `ADMIN_PROTECTION_UNAVAILABLE`** |
| configured | missing | 401 `ADMIN_TOKEN_REQUIRED` |
| configured | wrong | 401 `ADMIN_TOKEN_REQUIRED` |
| configured | correct | allowed |

503 rather than 401 when unconfigured is deliberate: the route is not *refusing
this caller*, it is *unavailable* because the server cannot enforce its own
policy. That distinction is what makes the misconfiguration visible instead of
looking like a credential problem.

A token is rejected as unusable when it is empty, shorter than 16 characters, or
a known placeholder (`changeme`, `placeholder`, `secret`, `admin`, …). A
placeholder left in an env file is not protection, and treating it as such would
recreate the fail-open hole under a different name.

`ADMIN_API_TOKEN` is canonical; `PHASE8_ADMIN_TOKEN` remains an accepted
deprecated alias so existing deployments keep working.

### Token handling

- Header only (`X-Admin-Token`). Never a query parameter, where it would land in
  access logs, proxy logs, and browser history.
- `SecretStr` in settings.
- `hmac.compare_digest` comparison.
- Never logged, never returned, never in an OpenAPI example.

### Protected routes

```text
POST /v1/ingestion/nhtsa/phase1/run
POST /v1/ingestion/nhtsa/phase1-5/complaints-flat-file/run
POST /v1/graph/schema/setup
POST /v1/graph/build
POST /v1/graphrag/index
```

### Route classification

`DELETE /v1/conversations/{id}` and the retention purge are **not** admin-gated.
Deleting your own conversation is a privacy action scoped by an unguessable
UUID, not an administrative operation; requiring an operator token would make
the documented deletion right unusable. The retention purge has no HTTP route at
all — it is service-only. Both are asserted by test.

Full JWT authentication and roles (`docs/06_api_contract.md`) remain deferred.

## 6. Priority 3 — Legacy `/v1/chat/*`

### Prior behavior

`POST /v1/chat/sessions` returned a UUID that was never persisted, and
`POST /v1/chat/sessions/{id}/messages` called `SqlAnalyticsService` (Phase 2) or
`answer_hybrid_question` (Phase 4) directly. That path **bypassed
`GuardedAnswerService` entirely**: no citation validation, no causality guard,
no deterministic confidence, no abstention, no provenance.

### Decision — deprecate and bridge

The routes stay, with their documented `docs/06_api_contract.md` shape, but are
re-implemented as a thin bridge over the Phase 8 `ConversationService`:

```text
/v1/chat/sessions/{id}/messages
→ ConversationService.answer()
→ GuardedAnswerService (mandatory GraphRAG, tools, validation, abstention)
→ Phase 8 cross-turn provenance gate
→ mapped into the documented answer_contract shape
```

This was chosen over removal because `docs/06_api_contract.md` and
`docs/01_system_architecture.md` still advertise the routes, and over "leave as
is" because an unsafe bypass must not remain callable.

### Contract mapping

| Contract field | Source |
|---|---|
| `message_id` / `run_id` | persisted turn id |
| `intent` | `safety` when abstained, `hybrid` when the SQL tool contributed, else `graph_rag` |
| `answer.summary` | guarded answer text |
| `answer.sections` | validated claims, then warnings as `caveat`, then a deprecation note |
| `sql.used` | whether `sql_analytics_tool` supplied evidence |
| `sql.query` / `rows` | always `null` — the guarded path never surfaces raw SQL |
| `evidence.citations` | guarded citations, excluding graph paths |
| `evidence.graph_paths` | guarded citations of type `graph_path` |
| `warnings` | guarded warnings + conversation warnings + deprecation notice |
| `confidence` | deterministic Phase 7 confidence |
| `phase` | `phase_9_legacy_bridge` |

The bridge only ever *narrows* what is exposed.

### Behavior changes clients must know

- `POST /sessions` now persists a real conversation and requires the database.
- A session id must be a real conversation id; unknown or malformed ids return
  404 rather than silently answering.
- Responses no longer contain SQL text or result rows.
- Routes are marked `deprecated: true` in OpenAPI and return `Deprecation`,
  `Link`, and `Warning` headers — on error responses as well as success, since
  raising `HTTPException` discards the injected `Response`.

Migration path: `/v1/chat/*` → `/v1/conversations/*`.

## 7. Priority 4 — Execution observability

### Reuse, not new tables

`agent_runs` and `tool_calls` have existed since `2025_01_01_0001` and were
never populated. Phase 9 populates them and adds the missing safe columns in one
additive revision (`2025_01_01_0005`). No duplicate table is created.

### Architecture

Application-owned. The LLM never writes an audit row.

```text
request
→ recorder.start_run()            AgentRun (status=running)
→ audit_run(context)              ContextVar binds the run
   → GuardedAnswerService
      → ToolRegistry.execute()    notify_tool_call() → ToolCall row
→ recorder.finish_run()           AgentRun (status=completed)
→ recorder.link_conversation_turn()
```

A `ContextVar` carries the active run instead of threading a run id through the
orchestrator, registry, and adapters. Rewiring stable Phase 7 internals for an
observability feature would be a far larger and riskier change than the feature
itself; a `ContextVar` is request-scoped and async-safe, so concurrent requests
never share a run.

`ToolRegistry.execute()` gains exactly one call — `notify_tool_call(request,
result)` — which is a no-op when no run is in scope. Offline tests and unaudited
callers are entirely unaffected.

`AuditedGuardedAnswerService` wraps the guarded service transparently: it
forwards the question unchanged and returns the `GuardedAnswerResult` unchanged.
It cannot influence retrieval, tools, synthesis, validation, confidence,
warnings, or abstention.

To prevent nested double-recording, `get_guarded_answer_service()` returns the
audited wrapper (used by `/v1/graphrag/answer`), while
`get_unaudited_guarded_answer_service()` returns the same underlying graph
without it — the conversation service uses that one and opens its own run so it
can link the persisted conversation and turn.

### Recorded fields

`AgentRun`: `conversation_id`, `turn_id`, `phase`, `surface`, `provider`,
`model`, `synthesis_mode`, `status`, `started_at`, `finished_at`, `latency_ms`,
`tool_call_count`, `fallback_used`, `abstained`, `abstention_reason`,
`confidence_score` (×10000), `confidence_level`, `validation_outcome`,
`error_code`, `warnings`.

`ToolCall`: `agent_run_id`, `tool_name`, `call_id` (application-owned),
`operation` (allowlisted name only), `started_at`, `completed_at`,
`latency_ms`, `status`, `success`, `error_code`, `evidence_item_count`,
`truncated`.

### Never persisted

Prompts, provider raw requests/responses, API keys, provider credentials,
connection strings, raw SQL, raw Cypher, unrestricted tool arguments, evidence
text, or tracebacks.

Tool **arguments are not stored**. Only the allowlisted `operation` name is
retained, so no question text, filter value, or model-supplied argument reaches
the audit trail. The pre-existing `input_json` / `output_json` columns are
deliberately written as empty `{}`.

### Audit is not memory

Nothing recorded here is ever read back into a later answer. The recorder
exposes no read path, `ConversationService` never references `AgentRun` or
`ToolCall`, and no public API route exposes audit rows. All four properties are
asserted by test.

### Failure policy — fail open, deliberately

An audit write failure logs and continues; the request still returns its
answer.

The guarded answer is the product, and losing an audit row must not lose an
answer. `docs/08_security_safety_guardrails.md` lists observability
*requirements* — what to store — but imposes no compliance obligation to refuse
service when storage is unavailable. If such an obligation is ever introduced,
this is the single decision to revisit.

### Idempotency

`call_id` is application-owned (the orchestrator generates `base-…` / `orch-…`).
Two defenses prevent duplicate rows on replay or retry: the in-scope run tracks
`seen_call_ids`, and the database enforces
`UNIQUE (agent_run_id, call_id)`.

### No audit API

No public or admin endpoint exposes audit rows. Observability stays DB-only for
this phase; adding a read surface would require deciding authorization and
redaction rules that the docs do not yet define. Inspection is by direct
read-only SQL, shown in the runtime acceptance report.

## 8. Migration strategy

One new additive revision. No existing column is altered or dropped, no
historical revision is rewritten, and the only data touched is backfilling
`started_at` from `created_at` on pre-existing rows.

## 9. Test strategy

| File | Focus |
|---|---|
| `tests/test_phase9_migrations.py` | tracking, chain integrity, single head, migration safety |
| `tests/test_phase9_admin_security.py` | fail-closed guard, token handling, route classification |
| `tests/test_phase9_legacy_chat.py` | bypass removal, contract compatibility, deprecation |
| `tests/test_phase9_observability.py` | run lifecycle, tool recording, idempotency, privacy, audit-is-not-memory |

All offline, deterministic, network-free, and independent of the operator
`.env`. Tool recording is exercised through the **real** `ToolRegistry`.
