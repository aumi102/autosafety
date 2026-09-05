# Phase 7 External LLM Runtime Acceptance Checkpoint

## 1. Initial State

- Date: 2026-09-05.
- Branch: `master`.
- Initial HEAD: `a927730da659ecee3de8d0b43d2c53321624b11d`.
- Working tree: clean; the only prior checkpoint artifact was this report in its
  earlier blocked state.
- Committed repository baseline: 663 passing tests and 3 existing deprecation
  warnings.

## 2. Docs and Contracts Followed

The checkpoint followed the Phase 7 design and Phase 7C/7D/7E reports, final
evaluation/runtime/closeout reports, API contract, security guardrails, answer
contract, and README. The current provider, orchestrator, guarded-service,
factory, API, and CLI contracts were treated as authoritative.

Current OpenAI compatibility was checked against the official
[`gpt-5.6-luna` model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
and [Chat Completions create reference](https://developers.openai.com/api/reference/cli/resources/chat/subresources/completions/methods/create).

## 3. External Configuration

Settings were loaded through the normal `Settings`/`.env` path. Only safe
metadata was observed:

| Setting | Resolved state |
|---|---|
| `PHASE7_SYNTHESIS_PROVIDER` | `openai_compatible` |
| `PHASE7_SYNTHESIS_ALLOW_EXTERNAL` | `true` |
| `PHASE7_SYNTHESIS_MODEL` | `gpt-5.6-luna` |
| Base URL | configured; HTTPS host `api.openai.com`, path `/v1` |
| API key | configured (`true`; value never read out or printed) |
| Provider timeout | 30 seconds |
| Tool rounds/calls | 2 / 4 |

The application factory selected `OpenAICompatibleProvider` rather than
silently selecting the deterministic provider. The status endpoint later
confirmed configured/active provider `openai_compatible`, enabled/configured
flags `true/true`, and provider availability `true`.

## 4. Provider and Protocol

The live path used synchronous HTTPS through `httpx`:

`POST https://api.openai.com/v1/chat/completions`

The request used operator-owned configuration, system/user messages, model
`gpt-5.6-luna`, a bounded completion budget, and JSON text parsed into
`ProviderSynthesisResult`. No DB/Neo4j client, key, header, raw provider body, or
request prompt was logged or committed.

## 5. Live Request Budget

Six bounded external attempts were made, exactly within the checkpoint budget:

- two diagnostic requests returned HTTP 400 before the compatibility fix;
- four requests returned HTTP 200 after the fix: one synthetic provider call
  and three full guarded-service calls;
- no authentication or rate-limit failure was manufactured and no fifth
  successful request was sent for API/CLI transport duplication.

## 6. Synthetic Provider Smoke

**Protocol and structured-parse smoke passed.** OpenAI returned HTTP 200 from
`gpt-5.6-luna`; request ID
`chatcmpl-EKavCdFL0mlR6CntMEUnbrQfBZqnB` was captured safely. The assistant JSON
parsed into one claim using only `cite-test-001`; no invented citation appeared.
The model conservatively set `abstain=true` for the deliberately synthetic
entity, so this proves live transport/model/parsing/citation-ID behavior, not a
positive domain assertion.

Usage returned by the provider:

- input: 649 tokens;
- output: 429 tokens;
- total: 1,078 tokens.

No dollar cost was estimated.

## 7. Tool-Planning Status

**Network-based `plan_tool_calls()`: NO.**

`OpenAICompatibleProvider.plan_tool_calls()` deterministically returns an empty
list. The normal orchestrator still executes mandatory GraphRAG base retrieval.
Existing offline integration tests prove that calls from planning-capable test
providers cross `ProviderToolCall -> ToolCallRequest -> ToolRegistry` and are
allowlisted, schema-validated, bounded, and executed only by the application.
The provider receives neither PostgreSQL nor Neo4j clients. Live model-driven
planning remains a documented architectural limitation, not a hidden claim.

## 8. GuardedAnswerService Live Smoke

The normal `build_guarded_answer_service()` dependency used healthy local
PostgreSQL/pgvector and Neo4j services with the real provider.

### Complaint

Question: `What brake complaints are reported for Ford F-150 2020?`

- `phase=phase_7`, `synthesis_mode=llm`, provider `openai_compatible`;
- 2 accepted complaint claims, 10 final citations;
- every claim citation known; validation valid; citation coverage 1.00;
- application confidence 0.8446/high;
- complaint-data and missing recall-component warnings preserved;
- `fallback_used=false`; elapsed 4,319.3 ms.

This is the positive end-to-end real-provider acceptance case.

### Recall applicability

Question: `Are there official recalls affecting Ford F-150 2020?`

- the provider returned a structured abstention rather than inventing
  applicability;
- final result remained safe (`phase_7`, provider `openai_compatible`, no
  fallback, no unsupported claim); elapsed 14,267.9 ms;
- investigation proved that five live `official_recall_affects_vehicle` graph
  paths existed, but their `EvidenceItem.citation_id` values were absent and
  Phase 7C omitted them from the provider citation table.

The serialization defect was fixed after this bounded call. Post-fix local
prompt construction contains 11 citation rows, including all 5 AFFECTS paths,
and every row has a stable citation ID. The positive recall answer was not sent
again because four HTTP-200 requests had already consumed the live budget.

### Causality

Question: `Did the brake complaints cause the recall?`

- the real provider response reached Phase 7D, which rejected one unsupported
  claim and invoked deterministic rescue;
- final mode `fallback`, provider `deterministic`, 7 safe claims/10 citations;
- validation valid, citation coverage 1.00, confidence 0.7416/medium;
- mandatory `Available evidence cannot establish causality` and visible
  fallback warnings present;
- no unsupported causal or “definitely unsafe” conclusion survived; elapsed
  10,473.1 ms.

This is expected guard activation, not silent provider fallback.

## 9. Recall and Citation Contract Fix

`EvidenceItem.resolved_citation_id()` is now the shared Phase 7C/7D source for
explicit or derived citation IDs. Provider evidence text, provider citation
tables, and the Phase 7D adapter therefore use identical IDs for graph paths,
SQL facts, shared-component evidence, and other items lacking an adapter-supplied
ID. The older Phase 7B `EvidenceBundle.citation_table()` explicit-ID contract is
unchanged.

The provider prompt taxonomy now distinguishes:

- `official_recall`: evidence-backed recall existence;
- `official_recall_applicability`: recall plus a matching citable AFFECTS path;
- `potential_shared_component_association`: explicitly tentative association.

Phase 7D remains the final authority and still independently validates matching
record keys and relation semantics.

## 10. API Coverage

The normal FastAPI application returned HTTP 200 from
`GET /v1/graphrag/answer/status` with `phase_7`, configured/active provider
`openai_compatible`, provider available, real LLM enabled/configured, and
deterministic fallback available. The serialized response contained no secret
field.

The answer endpoint resolves the same cached application factory and
`GuardedAnswerService` exercised live above. A further paid POST was deliberately
not sent after the four-success cap; existing network-free API tests verify final
Phase 7 schema, guarded semantics, safe failures, and absence of raw Phase 7C
output.

## 11. CLI Coverage

`scripts/query_phase7_answer.py` imports the same
`build_guarded_answer_service()` factory and cannot select a provider, URL,
credential, tool, SQL, or Cypher from arguments. Existing process/format tests
cover JSON, pretty output, abstention exit 0, safe non-zero failures, and secret
absence. A fifth paid request solely through the CLI wrapper was not made; its
external-provider coverage is therefore wiring/transport coverage backed by the
live service call, not an additional live CLI model call.

## 12. Usage and Performance

The isolated provider call exposed usage and is recorded above. Usage is not a
field on `GuardedAnswerResult`, so per-call tokens for the three guarded-service
requests were not retained beyond the provider boundary. Measured local
end-to-end times were 4.32 s (complaint), 14.27 s (safe recall abstention), and
10.47 s (causal guard/fallback). These are observations, not SLA claims.

## 13. Fallback and Error Coverage

Network-free suites cover timeout, 401/403 authentication mapping, 429 rate
limit mapping, transport failure, malformed/empty/oversized responses,
unavailable providers, invented citations, visible deterministic fallback, safe
abstention, and sanitized errors. Real credentials were never altered to induce
failure.

## 14. Defects Found and Fixed

1. **HTTP 400 with the configured reasoning model.** The adapter sent
   `temperature=0.1` and legacy `max_tokens`. Current reasoning-model requests
   now omit sampling parameters and use `max_completion_tokens`; legacy chat
   models retain their prior compatible payload. The identical bounded live
   smoke changed from HTTP 400 to HTTP 200, and a regression test asserts the
   request shape.
2. **AFFECTS paths not citable by the provider.** Missing adapter-level citation
   IDs caused Phase 7C prompt construction to skip graph paths even though Phase
   7D later derived IDs for them. Citation-ID resolution is now shared and the
   actual local recall prompt includes all five AFFECTS paths. Regression tests
   assert ID/table/text consistency.
3. **Provider recall taxonomy lagged Phase 7D.** The prompt described only one
   recall claim type. It now exposes existence and applicability separately and
   states the matching-AFFECTS requirement; a regression test locks the contract.
4. **API status test depended on operator `.env`.** Enabling the real provider
   made a test that hard-coded deterministic configuration fail. The API fixture
   now supplies an explicit network-free status object; production status
   behavior remains unchanged.

No safety gate was lowered.

An initial full-regression attempt also caught an over-broad change to the
Phase 7B `EvidenceBundle.citation_table()` behavior. That change was reverted;
the external-provider fix remains isolated to the shared ID resolver and the
Phase 7C/7D consumers that require derived IDs.

## 15. Security Review

- Provider owns no DB/Neo4j client and cannot execute SQL/Cypher.
- Base URL/model/key are operator configuration, never request or CLI fields.
- No credential, authorization header, `.env`, prompt, raw response, traceback,
  database URL, or graph credential was emitted or committed.
- No user-configurable registry, public tool endpoint, raw Phase 7C endpoint,
  unbounded loop, database/graph mutation, or Phase 8 functionality was added.
- Final public prose is reconstructed from application-validated claims; the
  causal live result demonstrated this boundary.

## 16. Tests

New coverage targets reasoning-model request compatibility, shared citation-ID
resolution, the current guarded recall claim taxonomy, and environment-isolated
status tests. All regular tests remain offline.

Final targeted Phase 7 run:

- providers: 95 passed;
- orchestration: 47 passed;
- guarded synthesis: 108 passed;
- API/CLI: 63 passed;
- final integration: 24 passed;
- total: **337 passed, 0 failed, 3 existing warnings in 1.53 seconds**.

Phase 7B plus provider regression after restoring the Phase 7B contract:
**174 passed in 0.68 seconds**.

Final repository suite: **666 passed, 0 failed, 0 skipped, 3 existing
deprecation warnings in 2.30 seconds**. Compile checks passed for every modified
Python module.

## 17. Documentation Updates

This report replaces the earlier blocked checkpoint. README and the Phase 7
closeout report were updated only where external-verification facts changed.

## 18. Remaining Limitations

- Live model-driven `plan_tool_calls()` is not implemented.
- Positive recall applicability was fixed and verified against live local
  evidence serialization, but not re-sent to OpenAI after the request cap.
- API/CLI wrappers were not each charged an additional live model request; their
  shared factory/wiring and offline transport behavior were verified.
- Guarded results intentionally do not expose provider token usage.
- The tiny corpus, lexical deterministic embeddings, limited graph coverage,
  and `RELATED_TO_COMPONENT = 0` remain unchanged.
- No frontend or Phase 8 memory/multi-turn behavior exists.

## 19. Final Verdict

**EXTERNAL LLM ACCEPTANCE PASSED WITH LIMITATIONS**

The real configured provider, response parser, local retrieval, orchestration,
Phase 7D validation, citation coverage, confidence, warnings, and fallback path
were exercised end to end. Remaining limitations are bounded and explicit; none
permits unvalidated provider output to bypass the guarded contract.

## 20. Phase 8 Entry

From the external-provider perspective, Phase 8 may begin only while preserving
the Phase 7 guarded-service boundary and offline fallback. Recommended follow-up
is one bounded positive recall request using the fixed citation table, plus a
separate product decision on whether network-based model planning is required.
Neither is implemented as Phase 8 work in this checkpoint.
