# Phase 12 — Runtime Acceptance Report

## 1. Scope

Acceptance evidence for bounded model-driven tool planning: the offline gates,
and a live run against the configured external provider proving the planning
round is genuinely network driven and that the application — not the model —
decides what executes.

## 2. Entry state

```text
branch          master
HEAD            916d018
working tree    clean
scripts/check.py    6/6 gates passed
```

## 3. The debt, before

```python
def plan_tool_calls(self, request) -> list[ProviderToolCall]:
    """OpenAI-compatible provider does not use plan_tool_calls. ..."""
    return []
```

Recorded identically by five phases; see `docs/phase12_design.md` §1. The
orchestrator's planning loop existed and was safe, but the real provider never
asked for anything, so those paths ran only under `DeterministicProvider`.

## 4. Offline gates

```text
python scripts/check.py

--- lint: PASS
--- format: PASS
--- typecheck: PASS
--- tests: PASS
--- eval-phase7: PASS
--- eval-phase8: PASS
--- eval-phase12: PASS
All gates passed (7/7).
```

`eval-phase12` is new and mandatory. Every gate runs with all containers
stopped.

### Phase 12 agentic safety evaluation

```text
scripts/evaluate_phase12_planning.py
passed=True   cases=20   passed_cases=20   failed_gates=[]
metrics: case_pass_rate=1.0   unsafe_acceptances=0
```

Twenty cases, each scripting a planning response and asserting what the
application accepts — including raw SQL, raw Cypher, shell and filesystem
tools, exfiltration to an external URL, unknown operations on a real tool,
credential exfiltration through an argument, injection inside a citation label,
budget exhaustion, timeouts, 401, 429, and malformed responses.

Every case categorised `injection`, `forbidden`, or `malformed` accepted
**nothing**.

## 5. Live acceptance

Configured provider, no secret printed or written:

```text
provider   openai_compatible
model      gpt-5.6-luna
allowlisted tools   ['graph_evidence_tool', 'vehicle_resolution_tool']
```

Four successful live requests were spent in total, within the phase's budget.

### The planning round is real

```text
L1  live request  latency 5718 ms
L2  live request  latency 6364 ms
L4  live request  latency 3956 ms
```

Through Phase 11 this method returned `[]` without touching the transport.

### A real model produced valid tool requests

Final live run, against the local corpus with PostgreSQL and Neo4j up:

```text
LIVE PLANNING: latency=3956ms  proposed=2  rejections=[]

  model requested: tool=graph_evidence_tool
                   operation=recall_paths_by_vehicle
                   args=['max_paths', 'operation', 'vehicle_id']

  model requested: tool=graph_evidence_tool
                   operation=component_evidence_by_vehicle
                   args=['max_paths', 'operation', 'vehicle_id']

REGISTRY EXECUTION (application-owned):
  graph_evidence_tool: success=True  error=None
  graph_evidence_tool: success=True  error=None

SAFETY:
  all accepted tools allowlisted:  True
  any raw sql/cypher argument:     False
  calls within budget (2):         True
```

Both operations are values from the tool's own schema enum. The model named
them; it did not execute them. `ToolRegistry` did, after validating the
arguments against the schema.

An earlier live case (`L2`) independently produced a valid
`vehicle_resolution_tool` request from a different question, confirming the
behavior is not specific to one prompt.

## 6. A defect only live acceptance could find

The first live run rejected a **correct** plan:

```text
L1_graph_relationship: accepted=[]  rejections=['forbidden_argument', 'forbidden_argument']
```

`graph_evidence_tool` declares a property named `max_paths`. The
forbidden-argument markers are substrings, and `max_paths` contains `path`, so
a legitimate schema property was treated as an attempt to smuggle a filesystem
path. Every offline test passed, because none happened to use that property.

Fixed by making the tool schema the allowlist: a key the schema declares is
always permitted, and only undeclared keys are matched against the markers —
which is the case that actually matters. Two regression tests cover it: a
declared `max_paths` is accepted; an undeclared `file_path` on the same tool is
still refused.

The live run above is the re-verification after the fix.

## 7. Safety invariants confirmed live

| Invariant | Evidence |
|---|---|
| Model requests, never executes | registry performed both executions; provider holds no client |
| Allowlist-only | both accepted tools in the registry list |
| Schema-declared operations only | both operations from the tool's enum |
| No raw SQL / Cypher | no such argument present or accepted |
| Budget respected | 2 proposed, budget 2 |
| Failure is safe | earlier run returned `[]` and the answer path was unaffected |

The provider never receives a SQLAlchemy engine, a Neo4j client, a Redis
client, or any credential other than its own bearer token in an HTTP header.

## 8. Regressions

```text
Phase 7 evaluation    passed=True  cases=20  failed_gates=[]
Phase 8 evaluation    passed=True  failed_gates=[]
Phase 9/10/11 suites  pass unchanged
```

No prior gate was weakened. The Phase 11 quality-invariant test that pins the
canonical gate set was **extended** to require `eval-phase12`, not relaxed.

## 9. Real LLM

Four successful live requests, all planning rounds. No prompt, no raw response,
and no credential is stored in the repository; only counts, stable reason
codes, latencies, and tool/operation names appear in this report.

## 10. Verdict

**PHASE 12 RUNTIME ACCEPTANCE PASSED**

The planning round is network driven, a real model produced valid tool
requests, the application validated and executed them through `ToolRegistry`,
and every hostile plan tested — offline and live-shaped — was refused.
