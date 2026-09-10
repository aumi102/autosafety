# Phase 12 — Bounded Model-Driven Tool Planning

## 1. How this scope was chosen

The repository does not define Phase 12: a search for `Phase 12` / `phase12`
returns no matches. But the debt this phase closes is documented precisely, and
in the same words, by five prior phases:

| Source | Statement |
|---|---|
| `docs/phase7c_llm_provider_orchestration_report.md:145` | "`OpenAICompatibleProvider.plan_tool_calls()` always returns an empty list — the real LLM provider does not itself drive additional-tool planning over the network" |
| `docs/phase7_external_llm_acceptance_report.md:85` | "**Network-based `plan_tool_calls()`: NO.**" |
| `docs/phase8_design.md:354` | "Phase 8 does **not** implement live `plan_tool_calls()` network planning." |
| `docs/phase9_implementation_report.md:209` | "Live model-driven `plan_tool_calls()` is still absent" |
| `docs/phase11_design.md:344` | listed as remaining Phase 7 debt |

`docs/phase7_guarded_answer_synthesis_design.md:522` already specifies the
interface:

```python
def plan_tool_calls(self, ...) -> list[ToolCallRequest] | None:
    """Return structured tool calls requested by LLM, or None to proceed to synthesis."""
```

Phase 12 makes the OpenAI-compatible provider actually implement it.

## 2. What already exists

This is the important finding, and it shapes the whole phase: **the
orchestration loop is already built, bounded, and safe.**
`SynthesisOrchestrator.answer()` already does all of this:

```text
mandatory GraphRAG base retrieval        application-driven, not model-chosen
for round in range(max_tool_rounds)      hard-bounded loop, no `while`
  provider.plan_tool_calls(...)          <-- the only stub
  for call in requested_calls
    budget check                         remaining_budget <= 0 -> break
    deduplicate                          tool + sorted(arguments) key
    _sanitize_tool_call(call)            application-owned call_id, forbidden
                                         keys stripped, strings bounded
    _validate_and_execute(...)           ToolRegistry validates and executes
final synthesis                          bounded evidence, citation table
```

So Phase 12 is **not** an orchestration rewrite. Every guarantee the acceptance
gates ask for — bounded rounds, bounded calls, allowlist-only execution, no
model-executed tools, mandatory GraphRAG — is already enforced by code that
ships today. The single missing piece is that the real provider never asks for
anything, so those paths are exercised only by `DeterministicProvider`'s
heuristics.

**Phase 12 therefore implements one method and the validation around it**, and
proves that the existing guarantees hold when a real model is on the other end.

## 3. Non-goals

Semantic embeddings, corpus expansion, `/v1/chat/*` retirement, audit fail-open
policy, and auth redesign are all out of scope. No doc groups them with tool
planning, and the prompt's own guidance is to keep them separate.

## 4. Protocol decision — structured JSON, not native tool calling

Two options were available.

**Native OpenAI tool calling** (`tools` / `tool_calls` in the payload) is the
vendor-idiomatic route. Rejected, for three reasons:

1. It is not universal across "OpenAI-compatible" endpoints. The provider is
   documented as working against vLLM, LM Studio, Ollama's `/v1`, and
   OpenRouter; native tool-calling support varies among them, and a planning
   path that silently degrades on some backends is worse than one that works
   everywhere.
2. `synthesize()` **already** uses a structured-JSON protocol with a
   `requested_tool_calls` field, documented in its system prompt and parsed by
   `_parse_response`. Adding native tool calling would mean two planning
   protocols in one provider.
3. Native tool calling encourages passing the model a tool *result* channel.
   The application already owns execution; the model only needs to name a tool
   and its arguments.

**Decision: a dedicated structured-JSON planning round**, consistent with the
existing synthesis protocol.

### Wire format

Request: one `POST {base_url}/chat/completions` with a planning-specific system
prompt, `response_format: {"type": "json_object"}` where the model supports it.

Response the application will accept:

```json
{
  "tool_calls": [
    {
      "tool_name": "graph_evidence_tool",
      "operation": "recall_paths_by_vehicle",
      "arguments": {"vehicle_id": "..."}
    }
  ],
  "reasoning": "one short sentence, ignored by the application"
}
```

`operation` is accepted at the top level for the model's convenience and folded
into `arguments`, because every tool in the registry takes `operation` as a
schema property. An empty `tool_calls` list is a valid, expected answer: it
means the mandatory GraphRAG evidence is sufficient.

## 5. What the model receives — and does not

The planning prompt contains **only**:

- the resolved question
- tool definitions from `ToolRegistry.list_definitions()` — name, description,
  and JSON schema including each `operation` enum
- a bounded **citation-label summary** of evidence already gathered (labels and
  source types, not evidence text)
- the remaining tool-call budget
- the planning rules and output schema

It does **not** contain evidence prose, database rows, connection strings,
credentials, SQL, Cypher, internal object state, or the safety-rule text used
during synthesis. The provider object itself never holds a SQLAlchemy engine, a
Neo4j client, or a Redis client — that was already true and is unchanged.

## 6. Validation — the planning response is untrusted input

`plan_tool_calls()` returns `[]` on **every** failure. An empty plan is always
safe: the orchestrator proceeds to synthesis with the mandatory GraphRAG
evidence it already holds.

Rejections, each with a stable code recorded in the trace:

| Code | Cause |
|---|---|
| `planning_disabled` | model planning switched off, or provider unavailable |
| `planning_no_budget` | remaining budget is zero |
| `planning_http_error` | non-200; 401/403 and 429 are classified separately |
| `planning_timeout` | request timed out |
| `planning_transport_error` | DNS/TLS/connection failure |
| `planning_invalid_response` | not JSON, or unexpected envelope |
| `planning_output_too_large` | raw content over the cap |
| `unknown_tool` | tool name not in the registry allowlist |
| `unknown_operation` | operation not in that tool's enum |
| `invalid_arguments` | arguments not an object |
| `forbidden_argument` | an argument key matches the forbidden set |
| `too_many_calls` | more calls requested than budget allows (truncated) |

Raw SQL and raw Cypher cannot execute by construction, at three independent
layers: the planning validator drops forbidden keys, the orchestrator's
`_sanitize_tool_call` drops them again, and `ToolRegistry` validates arguments
against each tool's schema before dispatch — a `sql` or `cypher` key is not a
property of any tool schema. Filesystem, shell, and HTTP tools cannot be
requested because they are not in the registry; an unknown tool name is
rejected before it reaches the orchestrator.

## 7. Budgets

Unchanged from Phase 7: `PHASE7_MAX_TOOL_ROUNDS = 2`,
`PHASE7_MAX_TOOL_CALLS = 4`. The planning round additionally truncates its own
request list to the remaining budget, so a model asking for 100 tools produces
at most `remaining_budget` accepted calls, and the orchestrator's own budget
check remains the final authority.

The loop is a `for` over `range(max_tool_rounds)`. There is no `while`.

## 8. Configuration

One new setting, opt-out rather than opt-in, because a configured external
provider not planning is the debt this phase closes:

```text
PHASE12_MODEL_PLANNING_ENABLED   bool, default true
```

Planning only ever happens when the provider is *also* `available()` — that is,
external use is allowed and credentials are configured. No existing setting is
renamed. No credential is ever logged or persisted.

## 9. Observability

Phase 9's `AgentRun` / `ToolCall` model is reused; no parallel audit is
introduced. The `OrchestrationTrace` gains planning counters — rounds that
requested planning, calls proposed, accepted, and rejected with reason codes —
which flow into the existing trace surface.

Persisted: counts and stable reason codes. Never persisted: the planning
prompt, the raw planning response, arguments, credentials, or any header.

## 10. Test strategy

| File | Focus |
|---|---|
| `tests/test_phase12_tool_planning.py` | protocol, validation matrix, budgets, failure semantics, injection |

All offline: the planning round is exercised through a mocked `httpx` transport,
so the suite stays credential-free, network-free, and deterministic. A dedicated
test asserts the *before* state is gone — that the method is genuinely network
driven now rather than a stub returning `[]`.

Live acceptance, bounded to at most four successful provider requests, proves
the real model produces a valid tool request that the application accepts and
executes. It is recorded in `docs/phase12_runtime_acceptance_report.md`.
