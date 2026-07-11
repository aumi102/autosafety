# Phase 0 Bootstrap Prompt

Use this prompt in Codex/Claude from the empty repo root.

```text
You are working inside a new repository named autosafety-graphsql.

Goal: implement Phase 0 foundation for AutoSafety GraphSQL Copilot.

Read these docs first:
- README.md
- docs/00_project_brief.md
- docs/01_system_architecture.md
- docs/02_data_sources_and_ingestion.md
- docs/03_database_schema.md
- docs/04_graph_schema.md
- docs/05_agent_workflow.md
- docs/06_api_contract.md
- docs/08_security_safety_guardrails.md
- docs/09_roadmap_phase0_phase1.md
- docs/adr/ADR-0001-architecture-stack.md
- docs/contracts/answer_contract.md

Implement only Phase 0. Do not implement GraphRAG, Text-to-SQL generation, frontend, or full NHTSA ingestion yet.

Required deliverables:
1. Python FastAPI backend skeleton under backend/.
2. Docker Compose with api, postgres, redis, neo4j.
3. SQLAlchemy + Alembic configured.
4. Initial database models and migration for:
   - users
   - chat_sessions
   - chat_messages
   - agent_runs
   - tool_calls
   - source_runs
   - raw_source_rows
   - vehicles
   - components
   - complaints
   - recalls
   - recall_vehicle_links
   - investigations
   - investigation_vehicle_links
   - manufacturer_communications
   - manufacturer_communication_vehicle_links
   - document_chunks
   - citations
5. Health endpoints:
   - GET /healthz returns {"status":"ok"}
   - GET /readyz checks database connectivity and returns status.
6. .env.example with all required env vars and no real secrets.
7. Makefile with at least:
   - make up
   - make down
   - make test
   - make migrate
   - make lint
8. pytest tests for health endpoint and DB connectivity.
9. README local setup section if missing.
10. GitHub Actions CI for lint/test/migration check if feasible.

Constraints:
- Keep implementation small and clean.
- Do not hardcode secrets.
- Do not add fake NHTSA data yet except tiny test fixtures if needed.
- Do not claim production readiness.
- Keep architecture docs unchanged unless you find a clear inconsistency; if you update docs, explain why.
- Prefer simple, maintainable code over overengineering.

After implementation, run:
- make test
- alembic upgrade head or equivalent migration check
- docker compose config

Final response format:
A. Files changed
B. Commands run and results
C. What works now
D. What is intentionally not implemented yet
E. Risks / warnings
F. Next recommended Phase 1 prompt
```
