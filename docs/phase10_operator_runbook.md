# Operator Runbook

Everything an operator needs to configure, verify, and recover this service.
No secret appears anywhere in this document; every token shown is a placeholder.

---

## 1. Administrator token

### What it protects

`ADMIN_API_TOKEN` gates every mutation and audit route:

```text
POST /v1/ingestion/nhtsa/phase1/run
POST /v1/ingestion/nhtsa/phase1-5/complaints-flat-file/run
POST /v1/graph/schema/setup
POST /v1/graph/build
POST /v1/graphrag/index
GET  /v1/agent-runs            (and /summary, /{run_id})
GET  /v1/ops/diagnostics
```

Read-only product routes are never gated. Neither is `GET /v1/ops/readiness`
or `/readyz`, which a load balancer must be able to call.

### The guard fails closed

**With no token configured, those routes return 503 and cannot be used.** This
is deliberate. Phase 8 shipped the guard fail-*open*, so a deployment that
forgot the token silently exposed ingestion and index rebuilds to anyone.

```text
server token not configured  -> 503 ADMIN_PROTECTION_UNAVAILABLE
header missing or wrong      -> 401 ADMIN_TOKEN_REQUIRED
header correct               -> allowed
```

A fresh deployment therefore has **no working maintenance routes until you set
the token**. That is expected, not a fault.

### Generate one

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Requirements enforced in `app/core/security.py`:

- at least 16 characters
- not an obvious placeholder (`changeme`, `admin`, `secret`, `placeholder`)

A value failing either check is treated as **unset**, so the routes stay closed
rather than being protected by a guessable string.

### Configure it

Local development — put it in `.env`, which is gitignored:

```bash
ADMIN_API_TOKEN=<paste the generated value>
```

Deployment — inject it as an environment variable from your platform's secret
store. Never bake it into an image, a compose file, or a commit.

`PHASE8_ADMIN_TOKEN` is still accepted as a deprecated alias so existing
deployments keep working. Prefer `ADMIN_API_TOKEN` for anything new.

### Use it

Header only. Never a query string — a token in a URL lands in access logs,
proxy logs, and browser history, and the guard rejects it there on purpose.

```bash
curl -H "X-Admin-Token: $ADMIN_API_TOKEN" http://localhost:8000/v1/agent-runs
```

### Rotate it

1. Generate a new value.
2. Update the secret store / `.env`.
3. Restart the service (settings are cached per process).
4. Verify with the check in §3.

There is no token list and no overlap window: rotation is a single swap, so
schedule it when a brief maintenance-route outage is acceptable. In-flight
answer requests are unaffected — the guard covers only maintenance and audit
routes.

### Emergency disable

To take every maintenance and audit route out of service immediately, **unset
the token** and restart. The guard fails closed, so unsetting is the kill
switch. Product routes keep answering.

---

## 2. Start and stop

```bash
make db-up        # postgres, redis, neo4j
make migrate      # alembic upgrade head
make dev          # uvicorn on :8000
```

`make db-reset` **destroys all data** — it removes the `postgres_data`,
`redis_data`, and `neo4j_data` volumes. It requires explicit confirmation:

```bash
make db-reset CONFIRM=yes
```

Before Phase 10 this target ran `rm -rf postgres_data …` against directories
that do not exist, because compose uses *named volumes*. It deleted nothing and
reported success. If you have ever relied on it to give you a clean database,
you were probably debugging against old data.

---

## 3. Verification

Run these after any configuration change. None of them mutates anything.

```bash
# Is the service ready to take traffic? 200 = yes, 503 = a required dep is down.
curl -i http://localhost:8000/readyz

# Which dependencies are up? (public, safe, no connection detail)
curl -s http://localhost:8000/v1/ops/readiness | python -m json.tool

# Is admin protection active, and what is the schema revision? (admin only)
curl -s -H "X-Admin-Token: $ADMIN_API_TOKEN" \
     http://localhost:8000/v1/ops/diagnostics | python -m json.tool

# Recent guarded executions (admin only)
curl -s -H "X-Admin-Token: $ADMIN_API_TOKEN" \
     "http://localhost:8000/v1/agent-runs/summary?window_hours=24" | python -m json.tool
```

Expected when correctly configured:

```text
/readyz                       200, ready=true
diagnostics.admin_protection_enabled   true
diagnostics.alembic_revision           matches `alembic heads`
```

Confirm the guard itself, without running any maintenance work — an invalid
body means a `422` proves the token cleared the guard while the handler never
ran:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "X-Admin-Token: $ADMIN_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"limit_vehicles":"not-an-integer"}' \
  http://localhost:8000/v1/ingestion/nhtsa/phase1/run     # expect 422

curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "X-Admin-Token: wrong-token-value-here" -H 'Content-Type: application/json' \
  -d '{}' http://localhost:8000/v1/graph/build            # expect 401
```

---

## 4. Readiness semantics

| Dependency | Required | Effect when down |
|---|---|---|
| PostgreSQL | **yes** | `/readyz` → 503; the service cannot answer |
| pgvector | no | vector retrieval degrades; still ready |
| Neo4j | no | graph evidence unavailable; still ready |
| Redis | no | provisioned but unused by the answer path |

`detail` is a coarse class only — `timeout`, `unreachable`, `auth_failed`,
`client_not_installed`, `error`. Driver messages are logged, never returned:
SQLAlchemy and the Neo4j driver both embed host, port, and user in their
exception text, and `/readyz` is unauthenticated.

`redis` reporting `client_not_installed` is normal: the container runs, but the
Python client is not a project dependency because nothing uses it yet.

---

## 5. Migrations

```bash
alembic current    # applied revision
alembic heads      # must print exactly one head
alembic upgrade head
```

A fresh clone can rebuild the entire schema from tracked revisions
(`2025_01_01_0001` → `2025_01_01_0005`). If `alembic heads` ever prints more
than one line, stop and reconcile before deploying.

---

## 6. Reading the audit trail

`agent_runs` and `tool_calls` record execution metadata for every guarded
answer. They are **observability, never memory**: nothing reads them back into
an answer.

```bash
# Filter recent runs
curl -s -H "X-Admin-Token: $ADMIN_API_TOKEN" \
  "http://localhost:8000/v1/agent-runs?status=failed&since_hours=24&limit=20"

# One run with its tool calls
curl -s -H "X-Admin-Token: $ADMIN_API_TOKEN" \
  "http://localhost:8000/v1/agent-runs/<run_id>"
```

Useful signals:

| Field | Meaning |
|---|---|
| `status=failed` | the guarded run raised |
| `fallback_used=true` | the provider failed and the deterministic path answered |
| `abstained=true` | the guard refused to answer on the available evidence |
| `validation_outcome` | `accepted`, `repaired`, or `rejected` by Phase 7 validation |
| `tool_call_count` | how many allowlisted tools contributed |

What these responses will **never** contain, by construction: prompts, provider
responses, tool arguments, raw SQL, raw Cypher, credentials, or connection
strings. Responses are built from an explicit safe-field allowlist in
`app/services/ops/audit_reader.py`, so a new database column cannot appear in
an API response by accident.

Audit writes **fail open**: if the audit store is unavailable the answer still
returns. Losing an audit row must not lose an answer. Revisit this only if a
compliance obligation requires refusing service without an audit trail.

---

## 7. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| Maintenance routes return 503 | no usable `ADMIN_API_TOKEN` | set one (§1), restart |
| Maintenance routes return 401 | wrong or missing header | check header name `X-Admin-Token` |
| Token set but still 503 | shorter than 16 chars, or a placeholder | regenerate (§1) |
| `/readyz` returns 503 | PostgreSQL unreachable | `docker compose ps`, check the postgres container |
| `neo4j.reachable=false` | graph container down | `docker compose start neo4j`; answers continue, graph evidence is lost |
| `alembic heads` shows two heads | branched revisions | reconcile before deploying |
| Audit rows all missing | audit writes failing open | check logs for "Execution audit"; answers are unaffected |

---

## 8. Quality gates

```bash
make check       # the canonical gate: lint + typecheck + test + evaluate
make lint        # enforced; expected green
make typecheck   # enforced since Phase 11; expected zero errors
make test        # full suite
make evaluate    # Phase 7 and Phase 8 safety evaluations
make lint-all    # advisory; adds line-length findings (398)
```

Run `make check` before committing. Every part is offline: no Docker, no
credential, no external provider. GitHub Actions runs the same commands on every
push and pull request, plus a real fresh-database migration
(`.github/workflows/ci.yml`).

`make lint` covers `app/`, `tests/`, `scripts/`, and `migrations/`. Before
Phase 10 it ran `ruff check autosafety/` — a directory that has never existed —
so it silently checked nothing and always exited 0.
