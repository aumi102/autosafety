# Phase 10 — Maintainability and Operational Hardening

## 1. How this scope was chosen

The repository does not define Phase 10. A search across `docs/` for
`Phase 10` / `phase10` returns exactly one hit — `docs/phase9_design.md:52`,
which lists "any Phase 10 functionality" among Phase 9's *non-goals*. There is
no Phase 10 objective, deliverable list, or gate set to follow.

Scope therefore comes from two places that *are* authoritative:

1. **Phase 9's recorded debts**, in `docs/phase9_runtime_acceptance_report.md`
   §13 — a broken lint target, no audit read API, and an unset admin token with
   no operator documentation.
2. **`docs/06_api_contract.md:99`**, which is the one place the docs describe
   future work concretely:

   > `GET /v1/agent-runs/*` is **not implemented**. Phase 9 populates
   > `agent_runs` and `tool_calls` as a database-only audit trail with no
   > public read surface; exposing one requires authorization and redaction
   > rules not yet defined.

   That is a deferral with a stated precondition, and Phase 9 satisfied both
   halves of it: authorization in `docs/08_security_safety_guardrails.md`
   §"Maintenance/admin protection (Phase 9, fail-closed)", and redaction in
   §"Execution audit privacy (Phase 9)". The blocker is gone, and the docs even
   pre-name the route. Implementing it is following the contract, not
   overriding it.

Phase 10 is therefore **maintainability and operational hardening**: make the
quality tooling real, fix what it finds, and give operators the configuration,
verification, and visibility they currently lack.

## 2. Non-goals

Unchanged from Phase 9, and none of them are touched here: long-term memory,
personalization, frontend, new retrieval architecture, broad corpus ingestion,
new provider families, autonomous agent loops, full JWT identity. Also
explicitly out of scope: removing the deprecated `/v1/chat/*` routes (no doc
requires a retirement milestone), and changing the audit fail-open policy (no
compliance obligation requires fail-closed — see §7).

## 3. Priority 1 — Quality tooling

### Problem

`Makefile:13` ran:

```make
lint:
	ruff check autosafety/
```

`autosafety/` has never existed in this repository; the package is `app/`.
Ruff exits 0 on a missing path, so **the quality gate silently inspected
nothing and passed for every phase from 0 through 9.**

mypy was configured in `pyproject.toml` and shipped in the `dev` extras, but no
target ever invoked it, so it had never run either.

### Baseline

Pointing ruff at the real trees reported **1079 findings**:

| Tree | Findings |
|---|---|
| `app/` | 633 |
| `tests/` | 332 |
| `migrations/` | 89 |
| `scripts/` | 25 |

### Decision — triage, do not mass-format

Reformatting the repository would produce an enormous diff through the guarded
answer path, the tool registry, and the conversation services to fix nothing
that can fail at runtime. The findings were classified instead:

| Class | Count | Treatment |
|---|---|---|
| Safe autofix (import order, unused imports, PEP 604, f-strings) | 626 | fixed by `ruff --fix`; behavior-preserving by construction |
| Correctness (`F811`, `F841`, `F601`, `F401`, `E741`, `E731`, `F405`) | 24 | inspected individually; see §4 |
| Style — `E701`, `E402` | 18 | fixed by hand |
| Cosmetic — `E501` line length | 400 | excluded from the enforced gate, reported by `make lint-all` |
| Idiom-driven — `N806`, `UP042` | 20 | ignored with rationale, below |
| Template-driven, in immutable revisions | ~30 | narrow per-file ignores |

Result: **`make lint` is green and covers every first-party tree.**

### The two-tier gate

```make
lint:      ruff check app/ tests/ scripts/ migrations/     # enforced, green
lint-all:  ... --extend-select E501                        # advisory, 398 findings
typecheck: mypy app/                                       # advisory, 47 errors
check:     lint test                                       # what CI should run
```

`E501` is the only rule excluded globally, and it stays visible through
`lint-all`. `N806` and `UP042` are ignored with reasons recorded in
`pyproject.toml`:

- **N806** fires on `Session = sessionmaker(...)`. That binds a *class*, and
  PascalCase is SQLAlchemy's own documented convention. All 17 occurrences are
  this pattern; renaming them would make the code less conventional.
- **UP042** wants `class X(str, Enum)` rewritten as `StrEnum`. The two are not
  interchangeable — `StrEnum` changes what `str()` and f-string formatting
  produce — and these enums are serialized into tool names, template ids, and
  source types on the guarded answer path. That is a behavior change, not a
  style fix.

Per-file ignores are limited to three entries, each justified in place:
`migrations/versions/*.py` (template-driven findings in applied revisions),
`migrations/env.py` (imports every model so autogenerate sees it), and
`tests/test_phase2_sql_analytics.py` (star-imports models so `create_all` sees
every table). **No exemption disables a correctness rule** — a test asserts
this, so a future "make it green" change has to break a test to land.

mypy stays out of `check` until its 47 pre-existing errors are addressed. The
settings were not weakened to manufacture a pass.

## 4. Defects the repaired gate exposed

### 4.1 A dead retrieval path (`F811`)

`app/services/graph/graph_service.py` defined a public
`get_vehicle_neighborhood(vehicle_id)` **and** imported
`graph_queries.get_vehicle_neighborhood(client, make, model, year, …)` under
the same name. The module-level `def` won, so the internal call at line 221
invoked *itself* with the query-layer signature:

```python
return get_vehicle_neighborhood(client, make=…, model=…, year=…, vehicle_id=…)
#      ^ resolves to the local def, which takes only `vehicle_id`
```

That raised `TypeError`, the surrounding broad `except Exception` swallowed it,
and the function returned `None` for **every** vehicle. The graph neighborhood
lookup was dead in production while appearing to work.

Fixed by aliasing the import to `get_neighborhood_for_vehicle`, which is the
convention its three sibling functions already follow — they call
`get_recall_paths_for_vehicle`, `get_component_evidence_for_vehicle`, and
`get_shared_component_recall_paths`, none of which collide. Only this one did.

### 4.2 A gap in the evidence redaction denylist (`B033`)

`_FORBIDDEN_KEYS` in `bundle_builder.py` listed `"api_key"` twice. The set
absorbed the duplicate, so nothing was broken — but matching is *substring*
based against a lowercased key, and `"api_key"` is not a substring of
`"apikey"`. A metadata key spelled `apiKey` was therefore never redacted. The
duplicate was removed and `"apikey"` added, which strictly tightens the
denylist. A parameterized test now covers `apiKey`, `APIKEY`, and `apikey`.

### 4.3 A destructive target that destroyed nothing

`make db-reset` ran:

```make
db-reset: db-down
	rm -rf postgres_data redis_data neo4j_data
```

`docker-compose.yml` declares **named volumes**, not bind mounts. Those
directories do not exist, so the command removed nothing and reported success.
An operator who ran `make db-reset` got the stack back with every row still in
place, and would then debug against data they believed had been wiped.

It now runs `docker compose down -v` and — because it is genuinely destructive —
requires `CONFIRM=yes`.

### 4.4 Remaining correctness findings

Seven dead locals (`prompt_builder`, `providers`, `bundle_builder`,
`graph_builder`, `vector_store`, `answer_composer`, `question_parser`,
`sql_safety`), two same-valued duplicate dict keys, an ambiguous `l`, and a
lambda assignment. All removed or renamed; none changed behavior.

`sql_safety.validate_sql` deserves a note: it computed a `normalized`
whitespace-collapsed query and never used it, which looked like a validator
checking the wrong string. It is not — no entry in `BLOCKED_PATTERNS` contains a
literal space (they use `\s*`), and the keyword check uses `\b` word boundaries,
so normalization was never needed. The variable was dead code, and the
docstring's claim to return "the normalized query" was corrected to match what
the function actually returns.

## 5. Priority 2 — Operator runbook

`docs/phase10_operator_runbook.md` covers token generation, configuration,
rotation, emergency disable, verification commands, readiness semantics,
migrations, audit reading, and a troubleshooting table. Placeholders only; no
secret appears in it.

The gap it closes is specific: the Phase 9 guard fails closed, so a fresh
deployment has **no working maintenance routes** until `ADMIN_API_TOKEN` is set,
and nothing told an operator that or how to set it.

## 6. Priority 3 — Audit read access

Implemented as `docs/06_api_contract.md` describes it:

```text
GET /v1/agent-runs            list, filterable, bounded
GET /v1/agent-runs/summary    aggregate counters over a window
GET /v1/agent-runs/{run_id}   one run with its tool calls
```

**Authorization.** Every route resolves `verify_admin_token` — the same
fail-closed guard as ingestion and rebuilds. No second mechanism is introduced,
so an unconfigured deployment leaves the audit trail unreachable over HTTP,
which is the correct default for an audit log.

**Redaction by allowlist.** `ExecutionAuditReader` projects rows through
explicit `AGENT_RUN_SAFE_FIELDS` and `TOOL_CALL_SAFE_FIELDS` tuples. This is
deliberately an allowlist and not an exclusion list: a column added to
`AgentRun` later cannot appear in an API response until someone adds it here on
purpose.

Four columns are excluded on purpose:

| Column | Why |
|---|---|
| `input_json`, `output_json` | always-empty Phase 9 legacy columns; reading them at all would create a path for future data to escape |
| `intent`, `warnings` | copied from the guarded result; this layer will not assume they are free of user text |

Tests seed a provider key, a PostgreSQL URL, a Bolt URL, raw SQL, and raw Cypher
into exactly those columns and assert none of it reaches any response.

**Bounds.** `limit ≤ 100`, `since_hours ≤ 90 days`, `≤ 50` tool calls per run
detail. The API layer rejects out-of-range values with 422, and `AuditQuery.bounded()`
clamps them again inside the reader, so bypassing the API cannot produce an
unbounded scan.

## 7. Priority 4 — Operational diagnostics

`/healthz` and `/readyz` were static stubs returning `"ok"` and `"ready"`
regardless of whether any dependency was reachable — so an orchestrator would
keep routing traffic to an instance whose database was down.

Two tiers now:

| Route | Access | Contents |
|---|---|---|
| `/readyz`, `/v1/ops/readiness` | public | per-dependency booleans, coarse status words; 503 when a *required* dependency is down |
| `/v1/ops/diagnostics` | admin | the above plus Alembic revision, provider configuration shape, admin-protection state, 24h audit counters |

Only PostgreSQL is required. Neo4j, pgvector, and Redis degrade answer quality
rather than removing the ability to answer, so they are reported without failing
the probe.

The public tier reports a *shape*, never a detail. Driver exceptions are reduced
to `timeout` / `unreachable` / `auth_failed` / `client_not_installed` / `error`
and the original message is logged only — SQLAlchemy and the Neo4j driver both
put host, port, and user into their exception text, and `/readyz` is
unauthenticated. Diagnostics reports `provider_credential_configured` as a
boolean; the value is never read into a response.

**Audit fail-open is unchanged.** Phase 9 documented the rationale — the guarded
answer is the product, and losing an audit row must not lose an answer — and no
compliance requirement has appeared to override it. Changing it was considered
and deliberately rejected.

## 8. Test strategy

| File | Focus |
|---|---|
| `tests/test_phase10_tooling.py` | Make targets point at real paths, `db-reset` is honest and guarded, lint exclusions stay narrow, enforced gate is actually green |
| `tests/test_phase10_defects.py` | regressions for the shadowed function, the denylist gap, the duplicate literals |
| `tests/test_phase10_ops_api.py` | audit authorization, reads, filters, bounds, privacy, readiness, diagnostics |

All offline, deterministic, network-free, and independent of the operator
`.env`. The audit tests run against in-memory SQLite via `StaticPool`, so none
of them needs Docker or a real secret.

## 9. Deferred

- **398 `E501` line-length findings** — visible via `make lint-all`.
- **47 mypy errors across 16 files** — `make typecheck` exists; the debt does
  not gate `check`.
- **`/v1/chat/*` removal** — no doc defines a retirement milestone.
- Phase 7/8 debts are untouched: live model-driven `plan_tool_calls()`,
  lexical embeddings, corpus breadth.
