# Docs Index

Start here:

1. `00_project_brief.md`
2. `01_system_architecture.md`
3. `09_roadmap_phase0_phase1.md`
4. `prompts/phase0_bootstrap_prompt.md`

Implementation order:

```text
Phase 0: repo + backend + DB + Docker + tests
Phase 1: NHTSA complaints/recalls ingestion
Phase 2: Text-to-SQL
Phase 3: GraphRAG
Phase 4: Hybrid agent
Phase 5: frontend
Phase 6: eval + production hardening
```

Phase reports:

```text
Phase 7: guarded answer synthesis   phase7_*.md
Phase 8: multi-turn conversation    phase8_design.md,
                                    phase8_implementation_report.md,
                                    phase8_runtime_acceptance_report.md
Phase 9: deployability, security,   phase9_design.md,
         observability              phase9_implementation_report.md,
                                    phase9_runtime_acceptance_report.md
Phase 10: maintainability and       phase10_design.md,
          operational hardening     phase10_runtime_acceptance_report.md,
                                    phase10_operator_runbook.md
Phase 11: type safety, CI, and      phase11_design.md,
          developer quality gates   phase11_ci_quality_report.md
```

Before committing, run `python scripts/check.py` (or `make check`).

Operators start at `phase10_operator_runbook.md`.
