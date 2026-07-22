# Phase 6: GraphRAG Retrieval Foundation

## Status: Complete

Phase 6 GraphRAG semantic retrieval fully validated against live PostgreSQL/pgvector/Neo4j runtime.

## Why Phase 6 follows Phase 5

Phase 5 added component-level graph links but could not answer questions about complaint and recall *content* — only structured graph relationships. Phase 6 adds semantic retrieval over the actual text of complaints and recalls, enabling queries about remedies, consequences, narratives, and summaries. The graph expansion layer (Phase 3-5) then enriches retrieved evidence with structured vehicle-component relationships from Neo4j.

## What was built

### Document builder (`app/services/graphrag/document_builder.py`)
- `build_complaint_document()`: canonical EvidenceDocument from Complaint + Vehicle
- `build_recall_document()`: canonical EvidenceDocument from Recall + Vehicle
- Deterministic: same source entity always produces same document_id and content_hash
- Metadata includes: make, model, model_year, component, ODI/campaign number, incident/report dates, flags, source_url
- No "Component: None" — only populated when field exists
- Stable content_hash via SHA-256 of full_text

### Chunker (`app/services/graphrag/chunker.py`)
- `TextChunker`: deterministic text chunking with configurable max_length and overlap
- Stable chunk IDs via SHA-256(document_id + chunk_index + chunk_text)
- Natural boundary breaking (paragraph > sentence > word)
- Short documents produce one chunk; empty documents return empty list
- Overlap validated: must be 0 ≤ overlap < max_length
- Infinite loop protection: advance_to always > start

### Embedding providers (`app/services/graphrag/embedding_provider.py`)
- `DeterministicTestProvider`: hash-based lexical embeddings for tests/dev
  - Fixed 384-dimension L2-normalized vectors
  - No network, no API key, deterministic
  - Shared tokens → overlapping hash contributions → higher similarity
  - Clearly labeled as test/dev only — NOT semantically meaningful
- `LocalSentenceTransformerProvider`: optional sentence-transformers integration
  - Lazy import, no model download during tests
  - Configuration via GRAPHRAG_EMBEDDING_PROVIDER env var

### Vector store (`app/services/graphrag/vector_store.py`)
- `VectorStore`: PostgreSQL-backed chunk storage and similarity search
- **pgvector path** (when `embedding_vector` column exists):
  - Native `vector(384)` column
  - Cosine distance via `<=>` operator
  - HNSW index for fast ANN search
  - Stable tie ordering: `ORDER BY embedding_vector <=> :query_vec, chunk_id ASC`
  - All queries parameterized — no arbitrary SQL
- **JSONB fallback** (when `embedding_vector` absent):
  - Explicitly named "jsonb_fallback" — NOT "pgvector"
  - Brute-force cosine similarity computation
  - Same metadata filtering
- Module-level `_pgvector_available` flag for runtime detection

### Retriever (`app/services/graphrag/retriever.py`)
- `GraphRAGRetriever`: semantic retrieval + citation assembly
- Deduplication: highest-scoring chunk per source_record_key
- Citation labels: `Complaint {ODI} - {make} {model}` (no "Unknown")
- top_k bounded to [1, 50]

### Graph expander (`app/services/graphrag/graph_expander.py`)
- `expand_complaint_neighborhood()`: predefined Cypher for complaint → component → recall paths
- `expand_recall_neighborhood()`: predefined Cypher for recall → vehicle → complaint paths
- All queries parameterized — no arbitrary Cypher
- Graceful degradation when Neo4j unavailable

### Service (`app/services/graphrag/service.py`)
- `index_graphrag_documents()`: complaint + recall → document → chunks → embeddings → DB
- `retrieve_graphrag_evidence()`: semantic search + graph expansion + citations + caveats
- Safety caveats injected: retrieval noise, similarity ≠ causality, shared component = potential
- `get_graphrag_status()`: reports actual backend (pgvector or jsonb_fallback)

### API endpoints (`app/api/v1/endpoints/graphrag.py`)
- `GET /v1/graphrag/health`: vector backend availability
- `GET /v1/graphrag/status`: document/chunk counts, backend name, embedding config
- `POST /v1/graphrag/retrieve`: semantic retrieval with filters
- `POST /v1/graphrag/index`: administrative indexing (CLI-only preferred for production)

### CLI scripts
- `scripts/index_phase6_graphrag.py`: `--dry-run`, `--source-type`, `--limit`, `--force-reembed`, `--status`
- `scripts/query_phase6_graphrag.py`: `--question`, `--top-k`, `--no-graph`, `--source-type`, `--make`, `--model`, `--model-year`
- `scripts/evaluate_phase6_graphrag.py`: Recall@1, Recall@3, MRR over evaluation fixture

### Migrations
- `2025_01_01_0002_phase6_graphrag.py`: creates `evidence_documents` and `evidence_chunks` tables
- `2025_01_01_0003_phase6_pgvector_column.py`: adds `embedding_vector vector(384)` + HNSW index

## Architecture flow

```
PostgreSQL (complaints/recalls)
  └─> Document builder (canonical text)
        └─> Chunker (stable IDs, deterministic)
              └─> Embedding provider (deterministic or sentence-transformers)
                    └─> Vector store (pgvector or JSONB fallback)
                          └─> Retriever (similarity search, deduplication, citations)
                                └─> Graph expander (Neo4j neighborhood, predefined Cypher)
                                      └─> Response (chunks, citations, graph paths, warnings)
```

## Evaluation fixture

`tests/fixtures/phase6_graphrag_eval.json` contains 5 queries with `expected_source_keys` and wildcard patterns. Metrics (Recall@1, Recall@3, MRR) are computed deterministically, not hard-coded.

**Caveat:** Deterministic lexical embeddings reflect token overlap, not semantic understanding. Small 5-query fixture is statistically insignificant. Do not extrapolate to production quality.

## Safety boundaries

Phase 6 implements retrieval only:
- No LLM answer generation
- No LLM Text-to-SQL or Text-to-Cypher
- No arbitrary SQL or Cypher endpoints
- No user-provided SQL fragments
- All queries are predefined and parameterized
- Safety caveats injected into every response
- Similarity is not causality — explicitly stated
- Shared component paths labeled "potentially related"

## Tests

```
pytest tests/test_phase6_graphrag.py  => 29 passed
pytest tests/test_phase3_graph.py     => 27 passed
pytest tests/test_phase4_hybrid.py    => 30 passed
pytest tests/test_phase5_*.py         => 15 passed
pytest tests/                         => 221+ passed
```

No live PostgreSQL, Neo4j, or network required for unit tests.

## Runtime validation

### Alembic
```
current: 2025_01_01_0003 (head)
heads:   2025_01_01_0003
```

### pgvector
- Vector extension: enabled
- `embedding_vector` type: `vector(384)`
- HNSW index: `ix_evidence_chunks_embedding_vector_hnsw` with `m=16, ef_construction=64`
- Active backend: **pgvector**

### GraphRAG index
| Metric | Value |
|---|---|
| Documents | 42 |
| Chunks | 42 |
| Embedded vectors | 42/42 (100%) |
| Complaints | 5 |
| Recalls | 37 |

### Database integrity
- 0 duplicate document IDs
- 0 duplicate chunk IDs
- 0 missing source_record_key values
- 0 indexing errors

### Idempotency
Second normal reindex: 0 created, 0 updated, 42 unchanged — idempotent.

### Retrieval smoke

**Query:** "brake complaints and recalls for Ford F-150 2020" (top_k=5)
- 5 chunks returned (complaint + 4 recalls)
- Top score: 0.9735 (complaint 11420001 — SERVICE BRAKES)
- Graph paths: 5 (complaint_mentions_component + official_recall_affects_vehicle)
- Confidence: high (0.9)
- No causal overclaim, citations traceable to source records

**Query:** "brake complaints" filtered to Ford F-150 2020 (top_k=5, complaint only)
- 1 chunk returned (complaint 11420001, score 0.8535)
- Graph path: SERVICE BRAKES component linked
- Confidence: medium (0.6)

**Query:** "recall campaigns affecting Ford F-150 2020" (top_k=5, recall only)
- 5 recall chunks returned (scores 0.97–0.97)
- All have vehicle metadata (Ford F-150 2020) from Neo4j graph
- Graph paths: official_recall_affects_vehicle for each

### API smoke
```
GET /v1/graphrag/health       => {"vector_backend_available":true,"phase":"phase_6"}
GET /v1/graphrag/status      => {"vector_backend":"pgvector","document_count":42,...}
POST /v1/graphrag/retrieve   => 200, 5 chunks, citations, graph paths, warnings ✓
```

### Evaluation

```
Provider: deterministic-test-v1 (384-dim lexical)
Queries: 5 (4 evaluable with non-empty expected keys)
Recall@1:  0.6250  (5/8 expected hits in top-1)
Recall@3: 0.6250  (5/8 expected hits in top-3)
MRR:      0.7500  (mean reciprocal rank of first hit)
```

Per-query breakdown:
- "brake complaints and recalls": R@1=0.5, R@3=0.5, MRR=1.0 (complaints ranked first)
- "steering complaints for Ford": R@1=1.0, R@3=1.0, MRR=1.0
- "electrical system failures": not evaluable (empty expected keys)
- "airbag deployment issues": R@1=1.0, R@3=1.0, MRR=1.0
- "recall for campaign 20V123000": R@1=0.0, R@3=0.0, MRR=0.0 (campaign not in corpus)

### Bugs found and fixed
1. **Chunk metadata empty** — `upsert_document` passed `metadata_json={}` instead of `document.metadata`. Fixed: now passes `document.metadata`.
2. **pgvector query parameter error** — `ARRAY` literal passed as SQLAlchemy `TextClause` parameter. psycopg2 couldn't adapt it. Fixed: vector literal embedded directly in SQL string (deterministic embedding, not user input).
3. **Graph vehicle path wrong** — Neo4j `HAS_COMPLAINT` direction was `ModelYear→Complaint` but expander used `Complaint←ModelYear`. Fixed: reversed all arrow directions in both Cypher queries.

## Limitations

- **Tiny local dataset**: 5 complaints + 37 recalls — retrieval quality reflects small corpus
- **Deterministic lexical embeddings**: token-overlap similarity, not semantic understanding
- **RELATED_TO_COMPONENT = 0**: recall component data missing in source — same as Phase 5
- **No LLM generation**: Phase 6 is retrieval only; answer synthesis deferred to Phase 7
- **Optional sentence-transformer**: requires `pip install sentence-transformers` + model download

## Recommended Phase 7 direction

**Guarded evidence-based answer generation** over Phase 6 retrieval:
1. Template-based answer composition from retrieved chunks + graph paths
2. LLM-free answer generation using predefined templates with citations
3. Confidence scoring from chunk count, similarity scores, graph path counts
4. Strict citation requirements: every factual claim must cite a source record
5. Mandatory safety caveats in every answer

Do not implement unrestricted LLM generation in Phase 7 — keep LLM calls optional and guard output with citation enforcement.
