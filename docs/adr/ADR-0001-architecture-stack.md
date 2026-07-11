# ADR-0001 — Initial Architecture and Stack

## Status

Accepted for Phase 0 implementation.

## Context

The project needs to demonstrate production-grade GraphRAG, Text-to-SQL, evidence-grounded answer synthesis, and evaluation over public vehicle-safety data.

The system must be serious enough to show backend/data/agent architecture, but small enough to complete as a side project.

## Decision

Use a modular monolith with explicit service boundaries.

```text
Frontend: Next.js, Tailwind, shadcn/ui, Recharts
Backend: FastAPI, Pydantic, SQLAlchemy, Alembic
Structured DB: PostgreSQL
Vector store: pgvector first, optional Neo4j vector later
Graph DB: Neo4j
Agent workflow: LangGraph or compatible state machine
Jobs/cache: Celery + Redis
Eval: pytest + custom eval runner
Deployment: Docker Compose local first
```

## Why this decision

### FastAPI

Good for typed APIs, async support, simple local dev, clean OpenAPI documentation.

### PostgreSQL

Best core store for structured analytics, app state, audit logs, eval runs, and SQL generation.

### pgvector first

Keeps embeddings close to source records and avoids adding another vector database early.

### Neo4j

Vehicle-component-complaint-recall-investigation relationships are naturally graph-shaped. Neo4j makes graph paths explainable in the UI.

### LangGraph-compatible workflow

The agent needs explicit state and traceable tool calls. A graph/state-machine workflow is clearer than a single opaque chat completion.

## Consequences

### Positive

```text
clear architecture story
local-first development
strong eval and observability foundation
recruiter-friendly tech stack
```

### Negative

```text
more infrastructure than a simple RAG app
Neo4j adds operational complexity
GraphRAG quality depends on normalization and relation provenance
```

## Alternatives considered

### PostgreSQL-only

Simpler, but graph evidence paths would be less visible and less compelling.

### Vector-only RAG

Simpler, but would not demonstrate structured relation traversal or graph semantics.

### Microservices from day one

Too heavy for side-project delivery. Modular monolith is enough at this stage.
