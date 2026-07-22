# Phase 6: GraphRAG Retrieval Foundation

## Status: Complete (with infrastructure caveat)

Phase 6 GraphRAG semantic retrieval is substantially implemented and validated. Docker was unavailable in this environment, so live runtime validation (migrations, indexing, retrieval) was deferred. All unit tests pass, code compiles cleanly, and the architecture is verified against source-of-truth docs.

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

## Infrastructure caveat

Docker daemon was unavailable during this session. Live validation (migrations, indexing, retrieval, API smoke) could not execute. Code review confirmed:

- Migrations are syntactically correct and use the pgvector extension properly
- vector_store.py correctly detects `embedding_vector` column existence at runtime
- All queries are parameterized with no arbitrary SQL
- Backend detection is accurate (pgvector vs jsonb_fallback)

**To complete live validation:**
```bash
docker compose up postgres redis neo4j -d
alembic upgrade head
python scripts/index_phase6_graphrag.py --status
python scripts/index_phase6_graphrag.py --source-type all --limit 50 --force-reembed
python scripts/query_phase6_graphrag.py --question "brake complaints for Ford F-150 2020" --top-k 5
uvicorn app.main:app --host 127.0.0.1 --port 8016
# test GET /v1/graphrag/health, /v1/graphrag/status, POST /v1/graphrag/retrieve
```

## Limitations

- **Tiny local dataset**: ~5 complaints + ~37 recalls — retrieval quality reflects small corpus
- **Deterministic lexical embeddings**: token-overlap similarity, not semantic understanding
- **RELATED_TO_COMPONENT = 0**: recall component data missing in source — same as Phase 5
- **No LLM generation**: Phase 6 is retrieval only; answer synthesis deferred to Phase 7
- **Optional sentence-transformer**: requires `pip install sentence-transformers` + model download
- **Docker required for live indexing**: no in-process seeding
- **Live validation deferred**: Docker unavailable in this session

## Recommended Phase 7 direction

**Guarded evidence-based answer generation** over Phase 6 retrieval:
1. Template-based answer composition from retrieved chunks + graph paths
2. LLM-free answer generation using predefined templates with citations
3. Confidence scoring from chunk count, similarity scores, graph path counts
4. Strict citation requirements: every factual claim must cite a source record
5. Mandatory safety caveats in every answer

Do not implement unrestricted LLM generation in Phase 7 — keep LLM calls optional and guard output with citation enforcement.
