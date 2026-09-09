# Phase 9 — Implementation Report

## 1. Summary

Phase 9 is an infrastructure phase: deployability, security, maintainability,
and observability. **No answer semantics changed.** The Phase 7 guarded path and
the Phase 8 conversation contract are untouched, and both evaluations still pass
at full gates.

Baseline at entry: `5e9320c`, branch `master`, clean tree, **830 passed**.
Final: **953 passed**, 0 failed, 0 skipped.

## 2. What shipped

| Priority | Deliverable |
|---|---|
| 1 | `.gitignore` ignore rule removed; all 5 Alembic revisions tracked; fresh-clone migration proven |
| 2 | `app/core/security.py` rewritten fail-**closed**; `ADMIN_API_TOKEN` canonical |
| 3 | `/v1/chat/*` re-implemented as a deprecated bridge over the guarded path |
| 4 | `agent_runs` / `tool_calls` populated via `app/services/observability/` |
| 4 | Migration `2025_01_01_0005` adds safe audit columns (additive) |
| 5 | Live runtime acceptance on PostgreSQL/pgvector/Neo4j |
| 6 | `phase9_design.md`, this report, runtime acceptance report, doc updates |

Tests added: **141** across four Phase 9 files.

## 3. Priority 1 — Migration history

`.gitignore:27` excluded `migrations/versions/*.py`, so **zero of four**
revisions were tracked. A fresh clone could not rebuild the schema at all.

Fixed by removing the rule and tracking the complete chain in one commit.
Tracking only the newest revision would have been worse than tracking none — a
clone would hold `0004` pointing at a missing `0003`.

```text
git ls-files migrations/versions
  .gitkeep
  2025_01_01_0001_initial.py
  2025_01_01_0002_phase6_graphrag.py
  2025_01_01_0003_phase6_pgvector_column.py
  2025_01_01_0004_phase8_conversation.py
  2025_01_01_0005_phase9_execution_audit.py

alembic heads -> 2025_01_01_0005 (head)     # exactly one
```

No historical revision content was rewritten, regenerated, or squashed.

Fresh empty database → `alembic upgrade head` succeeded, producing 22 tables
including the pgvector column and the conversation schema. Details in
`docs/phase9_runtime_acceptance_report.md` §3.

## 4. Priority 2 — Admin protection, now fail-closed

Phase 8's guard returned silently when no token was configured, leaving
ingestion, graph rebuild, and index rebuild public. Phase 9 inverts that
default.

| Server token | Header | Result |
|---|---|---|
| not configured | anything | 503 `ADMIN_PROTECTION_UNAVAILABLE` |
| configured | missing / wrong | 401 `ADMIN_TOKEN_REQUIRED` |
| configured | correct | allowed |

Additional hardening: tokens shorter than 16 characters and known placeholders
(`changeme`, `placeholder`, `secret`, `admin`, …) are treated as unconfigured,
so a leftover env placeholder cannot silently re-open the hole.
`ADMIN_API_TOKEN` is canonical; `PHASE8_ADMIN_TOKEN` still works as a deprecated
alias. Header-only, `SecretStr`, `hmac.compare_digest`, never logged or
returned.

Not admin-gated, by deliberate classification: `DELETE /v1/conversations/{id}`
(a privacy action scoped by an unguessable UUID) and the retention purge (no
HTTP route). Both asserted by test.

## 5. Priority 3 — Legacy chat

Previously `/v1/chat/sessions/{id}/messages` called Phase 2/Phase 4 services
directly, bypassing `GuardedAnswerService` completely, and `POST /sessions`
returned an id that was never persisted.

Now both routes are a thin deprecated bridge over `ConversationService`, so the
documented `docs/06_api_contract.md` shape is preserved while every request runs
the full guarded path. Marked `deprecated: true` in OpenAPI, with `Deprecation`,
`Link`, and `Warning` headers on success **and** error responses.

Client-visible changes: session ids must now be real conversation ids (404
otherwise); `POST /sessions` persists and requires the database; responses no
longer include SQL text or rows.

## 6. Priority 4 — Execution observability

`agent_runs` and `tool_calls` existed since `0001` and were never written to.
Phase 9 populates them; migration `0005` adds the missing safe columns.

Recording is application-owned and flows through a request-scoped `ContextVar`,
so no Phase 7 internal signature changed. `ToolRegistry.execute()` gained
exactly one call — `notify_tool_call(...)` — which is a no-op when no run is in
scope.

`AuditedGuardedAnswerService` is transparent: it forwards the question unchanged
and returns the guarded result unchanged (asserted by identity in test). Nested
double-recording is prevented by exposing both an audited and an unaudited
process-level service; the conversation service uses the unaudited one and owns
its own run so it can link conversation and turn.

**Never persisted:** prompts, provider raw requests/responses, credentials,
connection strings, raw SQL, raw Cypher, unrestricted tool arguments, evidence
text, tracebacks. Tool arguments are dropped entirely — only the allowlisted
`operation` name is kept — and the pre-existing `input_json` / `output_json`
columns are written as empty `{}`.

**Audit is not memory.** Nothing recorded is read back into an answer; the
recorder has no read path, `ConversationService` never references the audit
models, and no API route exposes audit rows. All asserted by test.

**Failure policy: fail open.** An audit write failure logs and continues. The
guarded answer is the product; losing an audit row must not lose an answer.
Rationale and the condition for revisiting it are in `docs/phase9_design.md` §7.

**Idempotency:** application-owned `call_id` plus
`UNIQUE (agent_run_id, call_id)` and an in-run `seen_call_ids` set.

## 7. Defects found and fixed

1. **Deprecation headers were lost on error responses.** Raising
   `HTTPException` discards the injected `Response`, so a 404 from the legacy
   bridge carried no `Deprecation`/`Link` header — clients would only ever see
   the deprecation signal on success. Found during live runtime acceptance, not
   by the offline tests, which only exercised the 2xx path. Fixed by passing
   explicit `headers=` on every `HTTPException` raised by that surface, plus a
   regression test.
2. **Phase 9 admin tests were slow and unsafe.** An early route-level
   valid-token test executed the real ingestion/graph/index handlers (71s and
   real side effects). Replaced with an OpenAPI + dependency-level assertion
   (1.5s, no side effects).
3. **Test-harness mismatches** (my own, in new code): the evaluator-style tool
   definition used a non-existent `ToolParameter`, and a source scan matched the
   module's own changelog docstring. Both corrected.

No Phase 7 or Phase 8 production code was modified beyond the single additive
`notify_tool_call` hook in `ToolRegistry` and the optional `audit_recorder`
parameter on `ConversationService`.

## 8. Phase 8 tests intentionally superseded

`TestMaintenanceRouteProtection` in `tests/test_phase8_api_cli.py` encoded the
fail-**open** contract (`test_token_absent_allows_the_route`) and a 6-character
token. Those assertions are incompatible with the Phase 9 security fix by
design, so the class was removed and its coverage replaced — and substantially
expanded — in `tests/test_phase9_admin_security.py` (53 tests).

This is a deliberate tightening, not a weakened gate: every removed assertion
has a stricter Phase 9 counterpart.

## 9. Tests

| File | Tests |
|---|---|
| `tests/test_phase9_migrations.py` | 17 |
| `tests/test_phase9_admin_security.py` | 53 |
| `tests/test_phase9_legacy_chat.py` | 25 |
| `tests/test_phase9_observability.py` | 46 |
| **Phase 9 total** | **141** |

All offline, deterministic, network-free, independent of the operator `.env`.
Persistence uses in-memory SQLite; tool recording runs through the real
`ToolRegistry`.

Full suite: **953 passed, 0 failed, 0 skipped** (baseline 830, minus 18
superseded Phase 8 admin tests, plus 141 Phase 9 tests).

Regressions: Phase 7 evaluation 20/20 all gates; Phase 8 evaluation 10 cases /
20 turns, all 11 gates at threshold (citation validity and coverage 1.00,
conversation isolation 1.00, prompt-injection resistance 1.00, leak rates 0.00).

## 10. Lint and static checks

`ruff check` passes cleanly on every new and modified Phase 9 file.
`py_compile` / `compileall` pass. The repository's pre-existing lint debt in
older modules is out of scope and untouched.

## 11. Security

- No secret committed; `.env` never staged.
- Admin token: header-only, `SecretStr`, constant-time compare, never logged,
  never returned, absent from the OpenAPI schema.
- Mutation routes fail closed when protection is unavailable.
- No unsafe legacy bypass remains.
- No prompt, provider response, credential, raw SQL, or raw Cypher is
  persisted — verified by source scan **and** by scanning every value in the
  live `agent_runs` / `tool_calls` tables (0 matches).
- No `eval`, `exec`, `subprocess`, or unbounded loop in Phase 9 code.
- Conversation isolation and cross-turn provenance unchanged (Phase 8
  evaluation still 1.00).

## 12. Limitations

- Admin protection is a shared operator secret, not real authentication; JWT and
  roles remain deferred.
- Audit is fail-open by design, so a database outage loses audit rows while
  answers continue.
- No audit read API exists; inspection is direct read-only SQL.
- `latency_ms` in the legacy bridge response is reported as `0`; the real timing
  is recorded on the `AgentRun`, not the legacy contract.
- `total_tokens` on `AgentRun` remains unpopulated — Phase 7 deliberately does
  not expose provider token usage.
- Live model-driven `plan_tool_calls()` is still absent (unchanged Phase 7/8
  debt).
- Deterministic embeddings remain lexical (unchanged Phase 6 debt).
