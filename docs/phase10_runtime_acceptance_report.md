# Phase 10 — Runtime Acceptance Report

## 1. Scope

Live acceptance of the repaired quality tooling, the admin audit-read API, and
the dependency probes. Existing containers and volumes were reused; nothing was
re-ingested and no volume was reset.

## 2. Infrastructure

```text
autosafety-postgres-1   pgvector/pgvector:pg16   Up (healthy)
autosafety-neo4j-1      neo4j:5-community        Up (healthy)
autosafety-redis-1      redis:7-alpine           Up
```

Corpus unchanged across the phase: 1308 complaints, 57 recalls, 10 vehicles,
1365 evidence documents, 1880 chunks, 5 source runs.

## 3. Quality tooling

```text
ruff check app/ tests/ scripts/ migrations/        All checks passed!
ruff check ... --extend-select E501                Found 398 errors   (advisory)
mypy app/                                          47 errors in 16 files (advisory)
pytest tests/ -q                                   1039 passed
```

Baseline at entry was **954 passed, 0 failed**. Phase 10 added 85 tests.

`make` itself is not installed in this environment, so each target's command was
executed directly and the Makefile is additionally asserted by
`tests/test_phase10_tooling.py` — which parses the recipe lines, expands
`$(LINT_PATHS)`, and checks the paths exist. Comments cannot satisfy those
assertions; only executed recipe lines are examined.

Before/after on the enforced gate:

```text
before:  ruff check autosafety/   ->  exit 0, 0 files inspected (path does not exist)
after:   ruff check app/ tests/ scripts/ migrations/  ->  exit 0, gate green, 1079 findings triaged
```

## 4. Dependency probes — live

```text
postgresql   reachable=True   required=True    latency_ms=171
pgvector     reachable=True   required=False   latency_ms=0
neo4j        reachable=True   required=False   latency_ms=235
redis        reachable=False  required=False   detail=client_not_installed
ready=True
```

`redis` is honest rather than alarming: the container runs, but the Python
client is not a project dependency because nothing on the answer path uses it.
An earlier iteration of this run reported a generic `error` here; the probe was
changed to name the actual cause so an operator is not sent looking for a
network fault.

## 5. Public readiness — no credential leak

```text
GET /readyz             -> 200  ready=true
GET /v1/ops/readiness   -> 200
```

The response body was scanned for `postgres:`, `password`, `5433`, `bolt://`,
`neo4j://`, and `@`. **None present.** The public tier reports booleans and
coarse status words only.

## 6. Audit API — fail closed

With `ADMIN_API_TOKEN` unset, which is this environment's real state:

```text
GET /v1/agent-runs          -> 503 ADMIN_PROTECTION_UNAVAILABLE
GET /v1/agent-runs/summary  -> 503 ADMIN_PROTECTION_UNAVAILABLE
GET /v1/ops/diagnostics     -> 503 ADMIN_PROTECTION_UNAVAILABLE
```

The audit trail is unreachable over HTTP until an operator configures a token.

## 7. Audit API — configured, against live PostgreSQL

A throwaway token was exported for the duration of the check only.

```text
GET /v1/agent-runs?limit=5            -> 200  total=2
  phase_9  api_guarded  completed  provider=deterministic  mode=deterministic
           conf=high  validation=accepted  tools=2
  phase_9  api_guarded  completed  provider=deterministic  mode=deterministic
           conf=high  validation=accepted  tools=2

GET /v1/agent-runs/{run_id}           -> 200  tool_calls=2
  graphrag_retrieval_tool  op=retrieve_complaints_only  success=True
                           evidence=17  call_id=base-3c11a386aa2b
  vehicle_resolution_tool  op=None                      success=True
                           evidence=0   call_id=orch-9fb9635e7029

GET /v1/agent-runs/<unknown-uuid>     -> 404 AGENT_RUN_NOT_FOUND
GET /v1/agent-runs/not-a-uuid         -> 422 (rejected before any query)
```

Bounds enforced live: a request for `window_hours=8760` was rejected with
**422**, naming the 2160-hour (90-day) ceiling, rather than being silently
clamped at the API edge.

```text
GET /v1/agent-runs/summary?window_hours=8760
  -> 422  {"loc": ["query","window_hours"], "msg": "Input should be less than or equal to 2160"}
GET /v1/agent-runs/summary?window_hours=24
  -> 200
```

## 8. Privacy scan over live responses

Every audit response body was scanned for `sk-`, `password`, `postgresql://`,
`bolt://`, `redis://`, `Bearer`, `select `, `SELECT `, `MATCH (`, and the admin
token itself:

```text
forbidden markers found: none
```

The offline suite goes further: it seeds a provider key, a PostgreSQL URL, a
Bolt URL, raw SQL, and raw Cypher directly into `intent`, `warnings`,
`input_json`, and `output_json`, then asserts none of it appears in any
response. The allowlist projection makes those columns unreadable by
construction.

## 9. Admin diagnostics — live

```text
status=200  ready=True  alembic_revision=2025_01_01_0005
admin_protection_enabled=True
synthesis_provider=openai_compatible  synthesis_model=gpt-5.6-luna
provider_credential_configured=True   external_provider_allowed=True
audit_runs_last_24h=2  audit_failures_last_24h=0
dependencies: postgresql=True, pgvector=True, neo4j=True, redis=False
```

`provider_credential_configured` is a boolean derived from presence. The
credential value does not appear in the response, and neither does the admin
token. Both were asserted explicitly.

After unsetting the token, `GET /v1/agent-runs` returned **503** again,
confirming the guard re-closes.

## 10. Migrations

```text
alembic heads    2025_01_01_0005 (head)   <- exactly one
alembic current  2025_01_01_0005 (head)
```

No migration was added in Phase 10: the audit-read API reuses the Phase 9
schema unchanged.

## 11. Real LLM

**No live external request was spent.** Phase 10 concerns tooling, security,
and operations; the audit API reads rows that already exist, and the probes
touch no provider. Phase 9 already proved the real provider end to end through
the audit path.

## 12. Regressions

```text
scripts/evaluate_phase7_answers.py        passed=True  cases=20  failed_gates=[]
scripts/evaluate_phase8_conversations.py  passed=True  failed_gates=[]
    citation_validity 1.0      citation_coverage 1.0
    conversation_isolation 1.0 prompt_injection_resistance 1.0
    prior_text_leak 0.0        prior_citation_leak 0.0
    causal_guard 1.0           context_bounds 1.0        determinism 1.0
```

Phase 9 security and observability suites pass unchanged, with one test
updated rather than weakened: `test_no_public_api_route_exposes_audit_rows`
asserted that no route path mentioned `agent-runs`, which encoded Phase 9's
"there is no audit API" decision. It now asserts the invariant that outlived
that decision — every audit route resolves the admin guard — and is renamed
`test_no_unauthenticated_api_route_exposes_audit_rows`.

## 13. Test suite

```text
pytest tests/ -q     1039 passed, 4 warnings
```

Run with every container **stopped** as well as running, confirming the new
tests are hermetic.

## 14. Verdict

**PHASE 10 RUNTIME ACCEPTANCE PASSED WITH LIMITATIONS**

Quality tooling, the fail-closed audit API, readiness probes, and operator
diagnostics were all exercised against live infrastructure.

Remaining limitations: 398 line-length findings and 47 mypy errors are
baselined rather than fixed; `ADMIN_API_TOKEN` is unset in this environment, so
maintenance and audit routes are correctly unavailable rather than usable;
audit writes remain fail-open by design; `/v1/chat/*` remains deprecated but
present; and the Phase 7/8 retrieval debts are unchanged.
