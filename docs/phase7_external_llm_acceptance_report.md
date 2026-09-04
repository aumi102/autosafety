# Phase 7 External LLM Runtime Acceptance Checkpoint

## 1. Initial State

- Date: 2026-09-04.
- Branch: `master`.
- Initial HEAD: `fd4c1e11e836f2dca2f07bf8271b99f0dbad1ae4`.
- Working tree: clean; no partial post-Phase-7 work existed.
- High-value baseline: 92 provider, 108 guarded-synthesis, 63 API/CLI,
  and 24 final integration tests passed (287 total).

## 2. Docs and Contracts Followed

The audit followed the Phase 7 design, Phase 7C/7D/7E reports, final
evaluation, runtime acceptance and closeout reports, API contract, security
guardrails, answer contract, and README. Actual committed constructors and
serialization contracts remained authoritative.

## 3. Actual External Configuration Status

Configuration was loaded through `Settings`, including its `.env` loading
behavior. Only safe metadata was inspected:

| Setting | Resolved state |
|---|---|
| `PHASE7_SYNTHESIS_PROVIDER` | `deterministic` |
| `PHASE7_SYNTHESIS_ALLOW_EXTERNAL` | `false` |
| `PHASE7_SYNTHESIS_MODEL` | not configured |
| `PHASE7_PROVIDER_BASE_URL` | configured |
| `PHASE7_PROVIDER_API_KEY` | not configured |
| Provider timeout | 30 seconds |
| Tool rounds/calls | 2 / 4 |
| Evidence/output/claim bounds | 20 items / 16,000 chars / 8,000 chars / 8 claims |

The factory therefore resolved `DeterministicProvider`; status reported
configured and active provider `deterministic`, provider available `true`, real
LLM enabled/configured `false/false`, and deterministic fallback available.
No key value, prefix, suffix, authorization header, or `.env` contents were
printed or stored.

## 4. Provider and Protocol

The implemented real adapter is `OpenAICompatibleProvider`. Its protocol is:

- synchronous HTTPS via `httpx`;
- `POST https://api.openai.com/v1/chat/completions` under the current safe base
  URL configuration;
- Bearer authorization from operator-only configuration;
- OpenAI-compatible chat-completions request shape;
- structured JSON returned as assistant message content, then parsed into
  `ProviderSynthesisResult`.

No native `response_format` or provider-native tool-calling API is used. The
configured model is empty, so no model was eligible for a live request.

## 5. Live Smoke Prerequisites

The required conjunction was not satisfied: real provider selection, explicit
external enablement, model, and API key were absent. The base URL alone is not
sufficient. Repository policy therefore prohibited a real request, including
the synthetic smoke. Zero external LLM requests were issued and no cost was
incurred.

## 6. Synthetic Provider Smoke

Not run. A mocked provider response is not reported as live verification.
Consequently there is no live request ID, finish reason, token usage, response
parse, or citation result to record.

## 7. Tool-Planning Status

**Network-based `plan_tool_calls()`: NO.**

`OpenAICompatibleProvider.plan_tool_calls()` returns an empty list without an
external planning request. This is an explicit Phase 7C documented limitation,
not newly invented behavior. Deterministic/fake planning tests verify the
application boundary:

`ProviderToolCall -> application-owned call ID and argument sanitization ->
ToolCallRequest -> ToolRegistry allowlist/schema validation -> bounded adapter`.

Providers receive tool definitions and bounded evidence only; no PostgreSQL or
Neo4j client reaches a provider. Terminal `requested_tool_calls` parsed from
`synthesize()` are not executed because planning has already ended.

## 8. GuardedAnswerService Coverage

A real-provider guarded call could not run. A local deterministic sanity call
used the normal application factory with the healthy local PostgreSQL/pgvector
and Neo4j services:

- question: brake complaints for Ford F-150 2020;
- result: `phase_7`, `synthesis_mode=deterministic`, provider `deterministic`;
- 3 claims, 11 citations, validation `true`;
- no external fallback was claimed.

This confirms current local wiring only; it does not satisfy real-provider
acceptance. Real-provider complaint, recall-applicability, and causal-guard
smokes remain unexecuted.

## 9. Recall and Causal Semantics

Existing offline suites still enforce that official applicability requires a
matching `official_recall_affects_vehicle`/`AFFECTS` citation, invented or
wrong-type citations are rejected, and unsupported causality is removed or
abstained. These tests passed, but no new real-model output was available to
exercise these guards live.

## 10. API Coverage

The actual FastAPI dependency path was exercised against current configuration:

- `GET /v1/graphrag/answer/status`: HTTP 200, `phase_7`, configured/active
  provider `deterministic`, real LLM disabled and unconfigured.
- `POST /v1/graphrag/answer`: HTTP 200, final guarded contract, deterministic
  mode, 3 claims, 11 citations, validation true, trace suppressed by default.

The API resolves `GuardedAnswerService` through the cached application factory;
it cannot select providers, credentials, URLs, tools, SQL, or Cypher per request.
This is transport/configuration coverage, not a real-provider API call.

## 11. CLI Coverage

The actual CLI process used the same application factory and exited 0 with
valid JSON: `phase_7`, deterministic provider/mode, 3 claims, 11 citations,
validation true, and no default trace. Source inspection confirms no separate
CLI provider path. This is transport/configuration coverage, not a
real-provider CLI call.

## 12. Token and Cost Observation

No external call occurred, so input, output, and total token counts are
unavailable. No price or dollar cost was estimated.

## 13. Fallback and Error Coverage

The existing network-free suites passed coverage for provider timeout, 401/403
authentication errors, 429 rate limiting, transport error, malformed/empty/
oversized output, unavailable provider, invented citations, deterministic
fallback visibility, safe abstention, sanitized exceptions, and correct
`synthesis_mode`. Real credentials were not damaged to manufacture failures.

## 14. Defects Found or Fixed

No new reproducible production defect was found within the configuration and
offline paths available in this checkpoint. No implementation code or test was
changed. The lack of live model-driven planning remains documented technical
debt; Phase 7C explicitly accepted it, so this checkpoint did not add an
autonomous planning loop.

## 15. Security Review

- Provider owns no DB session, PostgreSQL engine, Neo4j driver, or graph client.
- Base URL and API key are operator configuration, never request fields.
- Provider tools are an application-owned allowlist with strict arguments and
  call budgets.
- No unrestricted SQL/Cypher or user-controlled HTTP endpoint exists.
- No API key, bearer header, raw provider response, raw prompt, `.env`, DB URL,
  or traceback was emitted or committed.
- No public raw Phase 7C or tool-execution endpoint exists.
- No unbounded provider loop or Phase 8 behavior was added.

The only `Authorization` construction found is the expected private outbound
provider header. The only DB/credential terms in tool code are forbidden-key
filters and application configuration wiring.

## 16. Tests

Final targeted Phase 7 results:

- `test_phase7c_providers.py`: 92 passed.
- `test_phase7c_orchestration.py`: 47 passed.
- `test_phase7d_guarded_synthesis.py`: 108 passed.
- `test_phase7e_api_cli.py`: 63 passed, 3 existing warnings.
- `test_phase7_guarded_answer_synthesis.py`: 24 passed, 3 existing warnings.
- Targeted total: 334 passed.

Final repository suite: **663 passed, 0 failed, 0 skipped, 3 existing
deprecation warnings in 2.44 seconds**.

## 17. Local Infrastructure

Existing Docker services were already healthy: PostgreSQL/pgvector, Redis, and
Neo4j. No image pull, rebuild, reset, volume deletion, migration, or re-ingestion
was performed. Infrastructure readiness does not replace missing provider
authorization.

## 18. Documentation Updates

This checkpoint report is the only required documentation change. README and
`docs/phase7_closeout_report.md` already state that the external provider is not
live-verified, so they were intentionally left unchanged rather than rewriting
history with the same fact.

## 19. Remaining Limitations

- No live external request, structured response, usage, request ID, or model
  validation occurred.
- Real-provider GuardedAnswerService/API/CLI behavior remains unaccepted.
- Real provider tool planning does not make a planning-round network call.
- The prior corpus, deterministic retrieval, graph coverage, and
  `RELATED_TO_COMPONENT = 0` limitations remain unchanged.
- No Phase 8 behavior exists.

## 20. Final Verdict

**EXTERNAL LLM ACCEPTANCE BLOCKED — OPERATOR CONFIGURATION REQUIRED**

## 21. Minimum Operator Action

In the operator-controlled environment, configure a valid bounded provider:

```text
PHASE7_SYNTHESIS_PROVIDER=openai_compatible
PHASE7_SYNTHESIS_ALLOW_EXTERNAL=true
PHASE7_SYNTHESIS_MODEL=<supported model>
PHASE7_PROVIDER_API_KEY=<valid secret>
```

Keep the existing `PHASE7_PROVIDER_BASE_URL=https://api.openai.com/v1`, or set
it to the intended OpenAI-compatible endpoint. Then restart the application (or
start a fresh process so cached settings/service objects reload) and rerun this
checkpoint. Do not commit the credential.
