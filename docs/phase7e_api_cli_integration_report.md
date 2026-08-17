# Phase 7E: Guarded Answer API and CLI Integration

## Status

Implementation complete; formal validation results recorded below.

## Scope

Phase 7E exposes the committed Phase 7D `GuardedAnswerService` through FastAPI
and a JSON CLI. It adds transport schemas, application-owned dependency wiring,
safe status reporting, error mapping, and offline integration tests.

This phase does not implement Phase 7F evaluation fixtures, metrics, final Phase
7 closeout, frontend work, authentication/RBAC, unrestricted provider selection,
raw SQL/Cypher, new retrieval semantics, or changes to Phase 7D business logic.

## Baseline

- Branch: `master`
- Initial HEAD: `4240900` (`feat(phase7D): add guarded answer validation and synthesis`)
- Working tree: clean
- Partial Phase 7E artifacts: none
- `pytest tests/test_phase7d_guarded_synthesis.py -q`: 108 passed
- `pytest tests/ -q`: 576 passed

## Docs Followed

- `README.md`
- `docs/00_project_brief.md`
- `docs/01_system_architecture.md`
- `docs/03_database_schema.md`
- `docs/04_graph_schema.md`
- `docs/05_agent_workflow.md`
- `docs/06_api_contract.md`
- `docs/07_evaluation_plan.md`
- `docs/08_security_safety_guardrails.md`
- `docs/contracts/answer_contract.md`
- Phase 2, 3, 4, 5, and 6 implementation reports
- `docs/phase7_guarded_answer_synthesis_design.md`
- Phase 7B, 7C, and 7D implementation reports

Actual committed code contracts were treated as authoritative where older design
examples differed from Phase 7D.

## Actual Phase 7D Interface Consumed

`GuardedAnswerService` is synchronous:

```python
GuardedAnswerService(orchestrator: SynthesisOrchestrator)
GuardedAnswerService.answer(question: str) -> GuardedAnswerResult
```

It accepts only `question`. It does not accept make/model/year filters, `top_k`,
source type, graph toggles, provider selection, tool budgets, raw SQL, or raw
Cypher. Phase 7E therefore does not invent those controls.

`GuardedAnswerResult.to_dict()` is the public answer source:

- `query`
- `answer`
- `claims: list[GuardedClaim]`
- `citations: list[GuardedCitation]`
- `warnings`
- `confidence: ConfidenceResult`
- `abstained`
- `abstention_reason`
- `synthesis_mode`
- `provider`
- `retrieval_summary: RetrievalSummary`
- `validation: CitationValidationResult | None`
- `trace: GuardedTrace | None`
- `phase = "phase_7"`

`EvidenceSufficiencyResult` remains an internal Phase 7D policy result and is not
silently added to the public response.

## API

### POST `/v1/graphrag/answer`

Request:

```json
{
  "question": "What brake complaints are reported for Ford F-150 2020?",
  "include_trace": false
}
```

- `question` is stripped, required, non-empty, and limited to 1000 characters.
- `include_trace` is transport-only and defaults to false.
- Unknown fields are forbidden and return HTTP 422.
- Sanitized validation handling removes rejected input values from 422 payloads,
  preventing attempted API keys or query text from being reflected.

Explicit Pydantic response schemas mirror every Phase 7D public field and nested
model. No Phase 7C `OrchestrationResult` or `ProviderSynthesisResult` is exposed.
When `include_trace=false`, the stable `trace` field is returned as `null`; when
true, only the already-sanitized `GuardedTrace` fields are returned.

### GET `/v1/graphrag/answer/status`

Safe fields:

- `synthesis_available`
- `configured_provider`
- `active_provider`
- `provider_available`
- `real_llm_enabled`
- `real_llm_configured`
- `deterministic_fallback_available`
- `tool_calling_enabled`
- `max_tool_rounds`
- `max_tool_calls`
- `graphrag_base_required`
- `guarded_validation_enabled`
- `phase`

`configured_provider` remains distinct from `active_provider` and
`provider_available`. An incompletely configured `openai_compatible` provider is
reported as configured but unavailable, with deterministic as the active provider.
The endpoint performs no external provider request. It exposes no API key, key
prefix, authorization header, DB URL, Neo4j credential, or raw exception.

## Dependency Wiring

`app/services/answer_synthesis/factory.py` owns the dependency chain:

```text
Settings
  -> build_config_from_settings()
  -> process-level SQLAlchemy sync engine / Session factory
  -> Neo4j singleton-driver connectivity state
  -> build_default_tool_registry()
  -> build_synthesis_provider()
  -> DeterministicProvider fallback
  -> SynthesisOrchestrator
  -> GuardedAnswerService
```

FastAPI resolves a cached process-level service graph through `Depends`. The API
request cannot create a provider, tool, engine, driver, or registry. SQL sessions
are short-lived and adapter-owned; Phase 7E reuses the existing process-level DB
engine. Neo4j unavailability omits the optional graph tool while mandatory
GraphRAG retrieval and Phase 7D degradation/abstention semantics remain active.

## Provider Configuration Behavior

Provider configuration is read only from trusted `Settings`:

- `deterministic`: available offline.
- `openai_compatible`: active only when external access is enabled and model,
  base URL, and non-placeholder API key are configured.
- Missing/disabled external configuration: deterministic remains active.
- Runtime provider failure after real-provider activation: Phase 7C fallback and
  Phase 7D warning/trace semantics remain authoritative.

Current environment:

- configured provider: `deterministic`
- external provider enabled: false
- model configured: false
- API key configured: false
- real provider live smoke: not run

No status check or test sends a real provider request.

## HTTP Semantics

- Invalid schema, blank question, oversized question, or forbidden field: 422.
- Guarded abstention: 200 with `abstained=true`.
- Partial evidence: 200 with warnings/confidence.
- Graph unavailable with usable evidence: 200 under Phase 7D semantics.
- External provider unavailable with fallback: 200 with fallback semantics.
- Irrecoverable service construction failure: safe 503.
- Unexpected service/transport failure: safe 500.
- Exception text, tracebacks, secrets, and connection strings are not returned.

## CLI

```bash
python scripts/query_phase7_answer.py \
  --question "What brake complaints are reported for Ford F-150 2020?"
```

Options:

- `--include-trace`: include sanitized `GuardedTrace`.
- `--pretty`: indented JSON; default is compact valid JSON.

The CLI calls the same factory and `GuardedAnswerService.answer()` path as the API.
It has no provider/filter/tool/raw-query override flags. Guarded abstention exits 0.
Configuration failure exits 2; unexpected synthesis failure exits 1. Raw
tracebacks and exception/secret values are not printed.

## Existing Endpoint Compatibility

The Phase 6 routes remain registered unchanged, including:

- `GET /v1/graphrag/health`
- `GET /v1/graphrag/status`
- `POST /v1/graphrag/retrieve`
- `POST /v1/graphrag/index`

Phase 7E adds `/v1/graphrag/answer` and `/v1/graphrag/answer/status`; retrieval
and guarded synthesis remain distinct contracts.

## Security Review

- Request accepts only `question` and `include_trace`.
- `extra="forbid"` blocks credentials, provider URLs, tools, raw SQL/Cypher,
  table/column names, graph relationship names, and system prompts.
- Sanitized 422 handler does not echo rejected values.
- Endpoint calls only `GuardedAnswerService.answer()`.
- CLI calls the same guarded service path.
- No endpoint directly calls SQLAlchemy, Neo4j, Phase 7C orchestration, or provider HTTP.
- No raw provider request/response or prompt endpoint exists.
- No DB/graph mutation endpoint was added.
- No Phase 7F fixture, evaluator, or metrics were added.

## Tests

Formal final results:

- Python compile checks for every new/modified Python file: passed.
- `pytest tests/test_phase7e_api_cli.py -v`: 63 passed.
- `pytest tests/test_phase7d_guarded_synthesis.py -v`: 108 passed.
- `pytest tests/test_phase7c_providers.py -v`: 92 passed.
- `pytest tests/test_phase7c_orchestration.py -v`: 47 passed.
- `pytest tests/test_phase7b_tools.py -v`: 79 passed.
- `pytest tests/test_phase6_graphrag.py -v`: 29 passed.
- `pytest tests/test_phase3_graph.py -v`: 27 passed.
- `pytest tests/test_phase4_hybrid.py -v`: 30 passed.
- `pytest tests/test_phase5_component_graph_links.py -v`: 15 passed.
- `pytest tests/ -v`: 639 passed, 3 warnings.

The warnings are an environment-level Starlette `TestClient` deprecation and
two pre-existing Pydantic v2 class-config deprecations in ingestion/vehicle
schemas. No test uses an API key, Internet, NHTSA API, external provider, or
hosted service.

Phase 7E coverage groups:

- request validation and forbidden-field rejection
- complete response contract and trace suppression/inclusion
- accepted/deterministic/fallback/repaired/abstention/partial/graph-warning transport
- safe provider/status reporting without network
- 422/200/500/503 semantics
- factory and dependency wiring
- CLI JSON, pretty mode, exit codes, abstention, safe failures
- security boundaries and Phase 6 route preservation

## Controlled Local Runtime Smoke

No Docker services were running, so the smoke used FastAPI `TestClient`, the
real Phase 7B-7D orchestration/guarding path, deterministic provider, and an
injected in-process fake GraphRAG retriever. Results:

- Status: HTTP 200, `phase_7`, configured deterministic provider available,
  deterministic fallback reported available, and no secret fields.
- Complaint question: HTTP 200, deterministic non-abstained guarded result,
  three citations, and complete final response contract.
- Recall question: HTTP 200, deterministic non-abstained guarded result; the
  applicability claim remained backed by one
  `official_recall_affects_vehicle` citation.
- Causality question: HTTP 200, no unsupported causal conclusion, and the
  mandatory causality limitation warning was present.
- CLI: exit 0, valid compact JSON, `phase_7`, and trace suppressed by default.

This is not claimed as live PostgreSQL, pgvector, Neo4j, external LLM, or
Internet validation.

## Documentation Changes

- Added this Phase 7E report.
- Corrected only the Phase 7 design's API/CLI contract examples that conflicted
  with the committed Phase 7D method signature and application-owned provider policy.
- README remains unchanged; final integration belongs to Phase 7F.

## Limitations

1. Real external provider remains not live-verified.
2. Local evidence corpus remains tiny (previously recorded as 5 complaints and 37 recalls).
3. Deterministic Phase 6 embeddings remain lexical/token-overlap retrieval, not
   production semantic quality.
4. `RELATED_TO_COMPONENT = 0` remains a source-data limitation.
5. Existing Phase 6 graph/data services own some legacy engine/session lifecycle
   behavior; Phase 7E adds no new per-request engine construction.
6. No frontend is included in this backend-only phase.
7. No Phase 7F final evaluation fixture, metrics, or closeout exists yet.

## Phase 7F Handoff

Phase 7F must evaluate and runtime-validate the completed Phase 7 stack without
changing Phase 7E transport semantics:

1. Add the planned Phase 7 answer evaluation fixture and runner.
2. Compute citation coverage/validity, grounded-claim rate, abstention accuracy,
   warning coverage, invalid-output rejection, and deterministic stability.
3. Run the combined Phase 7 and full regression suites.
4. Perform live local PostgreSQL/pgvector/Neo4j API and CLI smoke when infrastructure
   is available; clearly separate mocked from live evidence.
5. Perform a real external-provider smoke only when explicit credentials and
   enablement exist, without printing secrets.
6. Complete final Phase 7 documentation/README and closeout review.

No Phase 7F work is implemented in this commit.
