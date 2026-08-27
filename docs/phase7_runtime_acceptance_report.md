# Phase 7 Runtime Acceptance Report

## Scope and Method

This report records Phase 7F acceptance against live local PostgreSQL/pgvector
and Neo4j state. Docker Desktop and the repository's existing containers/volumes
were reused. Only `postgres`, `redis`, and `neo4j` were started. No image was
pulled or rebuilt, no volume was deleted, and no data was re-ingested or mutated.

The API smoke used FastAPI `TestClient` with real local dependencies. CLI smoke
used the actual `scripts/query_phase7_answer.py` process. The configured provider
was deterministic, so no external request was made.

## Docker and Migration State

| Service | Image | State |
|---|---|---|
| PostgreSQL/pgvector | `pgvector/pgvector:pg16` | running, healthy |
| Redis | `redis:7-alpine` | running |
| Neo4j | `neo4j:5-community` | running, healthy |

Containers were created approximately six weeks earlier and resumed with their
existing named volumes.

Alembic:

```text
current: 2025_01_01_0003 (head)
heads:   2025_01_01_0003 (head)
```

## PostgreSQL and pgvector

Read-only checks returned:

- PostgreSQL 16.12, database reachable on configured local port 5433.
- `vector` extension 0.8.1 installed.
- `evidence_chunks.embedding_vector` is `vector(384)`.
- HNSW index `ix_evidence_chunks_embedding_vector_hnsw` exists with cosine ops,
  `m=16`, and `ef_construction=64`.
- 42 evidence documents: 5 complaint and 37 recall documents.
- 42 chunks and 42 non-null vectors.
- Base tables: 5 complaints and 37 recalls.
- Duplicate document IDs: 0.
- Duplicate `(source_type, source_record_key)` values: 0.
- Duplicate chunk IDs: 0.
- Missing vectors: 0.
- Wrong embedding dimensions: 0.
- `get_graphrag_status()` reports backend `pgvector`, model
  `deterministic-test-v1`, dimension 384, and no error.

## Neo4j

Neo4j was reachable through the repository singleton driver.

Node counts:

| Label | Count |
|---|---:|
| VehicleMake | 3 |
| VehicleModel | 4 |
| ModelYear | 5 |
| Complaint | 5 |
| Recall | 37 |
| Component | 5 |
| **Total** | **59** |

Relationship counts:

| Type | Count |
|---|---:|
| HAS_MODEL | 4 |
| HAS_YEAR | 5 |
| HAS_COMPLAINT | 5 |
| AFFECTS | 39 |
| MENTIONS_COMPONENT | 5 |
| RELATED_TO_COMPONENT | 0 |
| **Total** | **58** |

Graph schema uses `ModelYear`, not a generic `Vehicle` node. Correct official
path is `Recall -[:AFFECTS]-> ModelYear`, optionally reached through
`VehicleMake -> VehicleModel -> ModelYear`. Live checks found 39 official paths,
37 distinct recalls, and 2 affected model-year nodes. Ford F-150 2020 had 11
official recall paths. Phase 7 applicability continues to require this explicit
`AFFECTS` relation.

`RELATED_TO_COMPONENT = 0` remains a source-data limitation. Neo4j notifications
about the absent optional relationship are now kept out of public CLI logs while
the application warning remains visible.

## Live ToolRegistry Smoke

All four registered tools reported `read_only=true`. Calls used structured,
bounded arguments through `ToolRegistry`.

| Tool | Result | Bounded evidence | Observed latency |
|---|---|---|---:|
| `vehicle_resolution_tool` | resolved Ford F-150 2020 | one application-owned vehicle ID | 33.54 ms |
| `sql_analytics_tool` | success | one row, complaint count = 1 | 13.24 ms |
| `graph_evidence_tool` | success | 20 recalls, max-path bound applied | 400.31 ms |
| `graphrag_retrieval_tool` | success | 5 chunks, 5 citations, 5 paths | 501.64 ms |

No raw SQL/Cypher request field, credential marker, mutation, or secret appeared
in the sanitized summaries. SQL used the predefined analytics operation;
application-generated query text is no longer retained in provider evidence.

## Live GuardedAnswerService Smoke

Actual `GuardedAnswerService` used the local DB, graph, pgvector retrieval, and
configured deterministic provider.

| Case | Result | Key semantics | Latency |
|---|---|---|---:|
| Complaint | phase_7, deterministic, 3 claims/11 citations | Complaint warnings; no unsafe conclusion | 111.76 ms |
| Recall | phase_7, deterministic, 5 claims/11 citations | 5 applicability claims backed by `AFFECTS` | 115.01 ms |
| Causality | phase_7, deterministic, 3 claims/10 citations | Observations only; causal warning | 62.59 ms |
| Component | phase_7, deterministic, 3 claims/20 citations | No shared-component claim fabricated while relation count is zero | 88.37 ms |

Every result had bounded application-computed confidence, valid Phase 7 shape,
Neo4j availability true, and no secret marker. The causality result did not say
complaints caused the recall.

## Live API Acceptance

FastAPI `TestClient` used real local service dependencies.

| Request | HTTP | Result | Latency |
|---|---:|---|---:|
| `GET /v1/graphrag/answer/status` | 200 | phase_7, deterministic configured/available, fallback available | 29.28 ms |
| Complaint answer | 200 | phase_7, 3 claims/11 citations | 190.82 ms first-call |
| Recall answer | 200 | phase_7, 5 applicable claims/11 citations | 64.48 ms |
| Causal answer | 200 | phase_7, no causal violation | 62.34 ms |
| `POST /v1/graphrag/retrieve` | 200 | phase_6, 5 chunks/5 citations/5 paths | 61.97 ms |

Answer responses exposed no raw Phase 7C `provider_result`, traceback, prompt,
query, API key, DB URL, or Neo4j credential. `include_trace` defaulted to false
and the stable trace field was `null`. Phase 6 retrieval compatibility is intact.

## Live CLI Acceptance

Both required commands used the actual application service factory:

```bash
python scripts/query_phase7_answer.py \
  --question "What brake complaints are reported for Ford F-150 2020?"

python scripts/query_phase7_answer.py \
  --question "Are there official recalls affecting Ford F-150 2020?" \
  --pretty
```

Both exited 0 and produced valid Phase 7 JSON. Complaint result contained 3
claims/11 citations; recall result contained 5 applicability claims/11
citations. Default trace was omitted. Stderr was empty after the logging-boundary
fix; no raw Cypher, secret, or traceback was printed.

## External Provider Status

**REAL PROVIDER LIVE SMOKE NOT RUN — CREDENTIALS/ENABLEMENT UNAVAILABLE**

Sanitized configuration state:

- configured provider: `deterministic`
- provider available: true
- external enabled: false
- real provider configured: false
- deterministic fallback available: true

No external status probe or provider request was attempted.

## Prompt-Injection Runtime Test

Controlled evidence contained both an instruction to ignore rules and cite a fake
ID, and a request to reveal the system prompt/database credentials. It ran through
the real registry, orchestrator, evidence adapter, validator, composer, confidence
model, and guarded service with controlled in-process evidence/provider inputs.

Observed result:

- phase_7, safe fallback, not a transport failure.
- Malicious provider claim rejected (`rejected_claim_count=1`).
- Instruction-like citation span redacted.
- Only `cite-complaint-INJECT-001` remained.
- No `cite-fake-999`, unsafe conclusion, system prompt, DB credential, or
  arbitrary tool call appeared.

## Performance Observations

Measurements are single lightweight local smoke observations, not SLA claims or
benchmarks. Deterministic/offline service calls were 62.59–115.01 ms after local
dependencies were warm. API answer calls were 62.34–190.82 ms, with the high end
on first service construction. Graph and GraphRAG tool calls were the largest
individual costs at 400.31 ms and 501.64 ms. No external LLM latency was measured.

## Limitations

- Real external provider remains unverified.
- Corpus is 42 documents and cannot establish broad retrieval quality.
- Default deterministic embeddings are lexical/token-overlap based.
- `RELATED_TO_COMPONENT = 0`; shared-component paths are unavailable in live data.
- Graph covers 5 model-year nodes, 5 complaints, and 37 recalls only.
- No frontend or Phase 8 multi-turn/memory runtime exists.
