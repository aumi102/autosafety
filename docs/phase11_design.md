# Phase 11 — Type Safety, CI, and Developer Quality Gates

## 1. How this scope was chosen

The repository does not define Phase 11. A search across `docs/` and `README.md`
for `Phase 11` / `phase11` returns **no matches at all**.

Two documents do constrain the work, and they are followed closely:

| Source | Requirement |
|---|---|
| `docs/09_roadmap_phase0_phase1.md:32,57` | "CI skeleton"; "Add CI workflow for lint/test/migration check" |
| `docs/prompts/phase0_bootstrap_prompt.md:61` | "GitHub Actions CI for lint/test/migration check if feasible" |
| `docs/phase7_closeout_report.md:176` | "Preserve Phase 7 evaluation and full regression as mandatory CI gates" |

So the CI platform (**GitHub Actions**), its jobs (**lint, test, migration
check**), and two mandatory safety gates (**Phase 7 and Phase 8 evaluations**)
are all documented. CI was a *Phase 0 deliverable that was never built* — for
eleven phases nothing verified this repository automatically.

The rest of the scope comes from Phase 10's recorded debts: 47 mypy errors, a
typecheck excluded from the gate, and no CI.

## 2. Non-goals

No LLM feature, no retrieval or embedding change, no frontend, no auth
redesign, no architecture rewrite. No safety boundary was altered to satisfy a
static checker — where mypy and a Phase 7–10 invariant disagreed, the annotation
was corrected, never the guard.

## 3. Typecheck baseline

mypy was configured in `pyproject.toml` from the start and shipped in the `dev`
extras, but **no target invoked it** until Phase 10 added one. Running it
reported:

```text
mypy app/    47 errors in 16 files (106 source files checked)
```

By error code:

| Code | Count | Nature |
|---|---|---|
| `arg-type` | 15 | wrong argument types, several crossing a documented contract |
| `assignment` | 12 | ORM annotations disagreeing with the column definition |
| `return` | 6 | "missing return statement", all one root cause |
| `attr-defined` | 5 | attributes that do not exist |
| `union-attr` | 4 | Optional dereferenced without narrowing |
| `exit-return` | 2 | context manager annotated as possibly swallowing exceptions |
| `misc` / `index` / `import-not-found` | 3 | async generator, dict key, optional dependency |

The 19 `annotation-unchecked` entries are notes, not errors.

## 4. Real defects found

Static analysis was treated as a source of correctness findings, not cosmetics.
Four errors were genuine defects.

### 4.1 An HTTP route that always raised (`attr-defined`)

`GET /v1/graph/vehicles/{vehicle_id}/recall-paths` builds its response with:

```python
recalls=[r.to_dict() for r in result.recalls],
```

`RecallNode` was a plain dataclass with **no `to_dict`**, unlike the sibling
node types in the same module. Any call returning at least one recall raised
`AttributeError`. It survived because no test covers the HTTP route — only
`get_recall_paths_for_vehicle` beneath it.

The gap was visible in the code all along: `RecallPathResult.to_dict` carried a
hand-inlined copy of exactly the mapping `RecallNode.to_dict` should have
provided. `RecallNode` now has the method and the container delegates to it, so
the two cannot drift apart.

### 4.2 An answer-contract violation (`arg-type`)

`docs/contracts/answer_contract.md` defines `relation_source` as exactly:

```text
source_record | normalized_join | semantic_similarity
```

The Phase 4 hybrid composer passed the internal graph-layer `relation_basis`
straight through, so the public answer carried values the contract does not
define — `official_recall_affects_vehicle`, `complaint_mentions_component`,
`potentially_related_by_shared_component`.

Internal bases are now **mapped** onto the documented vocabulary: an official
campaign record becomes `source_record`, a component-name join becomes
`normalized_join`, and an unrecognized basis falls back to `normalized_join`
rather than leaking an internal label.

### 4.3 A supported intent outside its own type (`arg-type`)

`complaint_count_by_component_for_vehicle` has a `TemplateId`, a parser branch,
two service branches, and a tool operation — but was **missing from the
`ParsedQuestion.intent` Literal**. The vocabulary is now the named
`QuestionIntent` alias, and a test asserts every `TemplateId` value appears in
it, so the two cannot diverge again.

### 4.4 Two classes, one name (`arg-type`)

`VectorStore.upsert_document` was annotated with the ORM `EvidenceDocument` from
`graphrag.models`, while every caller passes the dataclass of the same name from
`graphrag.document_builder` — as its own docstring said. Under the ORM type,
`document.metadata` resolved to SQLAlchemy's `MetaData` rather than the payload.
The parameter is now annotated with the dataclass, imported under an alias so
the ORM class remains available for the queries in the same module.

## 5. Annotations that did not describe the code

| Fix | Errors resolved |
|---|---|
| 33 nullable ORM columns declared non-Optional `Mapped[...]` | 12 |
| `__exit__` annotated `-> bool` instead of `Literal[False]` | 8 |
| Two JSON **array** columns declared `Mapped[dict]` | 2 |
| Audit recorder parameter typed `object` → `AuditRecorder` Protocol | 4 |
| Async generator dependency annotated as its yield type | 1 |
| `_classify_intent` / confidence labels returning `str` where a Literal is required | 3 |
| Local narrowing at tool-adapter and graph-builder boundaries | 5 |

The `__exit__` case is worth noting: annotating it `-> bool` tells mypy the
context manager *might* suppress an exception, which made six correct methods
report "missing return statement". Both implementations return `False`;
`Literal[False]` says so precisely. One root cause, eight errors.

The `object`-typed audit recorder was replaced with a `Protocol` mirroring the
real signatures. That keeps `ConversationService` decoupled from the recorder
module — the original reason for the loose type — while actually describing the
calls it makes.

### The invariant that lived in another module

`_build_template_sql` read `vehicle.model_year` for vehicle-scoped intents while
`vehicle` was Optional. It never crashed, because `parse_question` downgrades
those intents to `clarification_needed` when the vehicle is incomplete. But
nothing local said so, and a parser change would have turned it into an
`AttributeError` deep in SQL construction. The invariant is now stated where it
is relied on.

## 6. Result and suppression policy

```text
mypy app/ scripts/ tests/    Success: no issues found in 148 source files
```

### Strictness (completion pass)

Every flag was measured before enabling. `check_untyped_defs`,
`no_implicit_optional`, `warn_redundant_casts`, `warn_unused_ignores`, and
`strict_equality` were already clean. `disallow_untyped_defs` (with
`disallow_incomplete_defs`) reported **88** errors and `warn_return_any` **5**;
all 93 are resolved.

`strict = true` is deliberately not used: it also turns on
`disallow_any_generics` and `disallow_untyped_calls`, which would require
annotating third-party generics and every SQLAlchemy/FastAPI boundary — churn,
not safety.

`tests/` and `scripts/` are exempt from `disallow_untyped_defs` **only**. Every
other flag applies to them, and a test pins that the relaxation covers exactly
those two module globs.

Notable fixes rather than annotations:

- `HybridIntent.vehicle` was typed `object`, making every attribute read on a
  parsed vehicle unverifiable.
- `AuditedGuardedAnswerService` is a transparent decorator, not a subclass, so
  every call site declaring the concrete `GuardedAnswerService` was inaccurate.
  A `GuardedAnswerLike` Protocol now states the substitutability both satisfy.
- `isolated_settings()` replaces `Settings(_env_file=None)` across six modules,
  so that one pydantic-settings suppression lives in one named place.
- `pytest.raises(Exception)` became `pytest.raises(HTTPException)` where the
  test already asserted on `.status_code` and `.detail` — strictly tighter.
- The Phase 7 evaluation harness built a `CaseHarness` with `None` fields and
  suppressed the error; it now closes over a `BaseCallCounter` and the
  suppression is deleted rather than moved.

Three suppressions remain across `app/` and `scripts/`, all error-code scoped:
the pydantic-settings runtime keyword, and two optional dependencies that are
not project requirements and ship no stubs (`sentence_transformers`, `redis`).

### Original pass

```text
mypy app/    Success: no issues found in 106 source files
```

Zero errors, with no blanket ignore, no `ignore_errors`, and no weakening of the
mypy settings. **Two** suppressions remain in `app/`, both error-code-scoped and
both for optional third-party packages that are not project dependencies and
ship no stubs:

```text
app/services/graphrag/embedding_provider.py  # type: ignore[import-not-found]  sentence_transformers
app/services/ops/probes.py                   # type: ignore[import-not-found]  redis
```

One pre-existing `# type: ignore[assignment]` was **removed**, by replacing a
`None` sentinel plus `__post_init__` with a proper `field(default_factory=set)`.

Tests enforce the policy: no bare `# type: ignore`, no `# mypy: ignore-errors`,
and at most three suppressions in `app/`.

## 7. E501 and formatting decision — superseded

The original Phase 11 pass measured `ruff format` across the whole repository,
found it would change 5539 lines and still leave 39 findings, and concluded that
E501 could not become a hard gate either way. It stayed advisory.

**The completion pass reversed that, because the measurement was wrong in one
respect:** it counted the immutable Alembic revisions, which are never
reformatted. Excluding them, the residue after formatting was **56**, not 398 —
and 56 is hand-fixable.

What was done:

```text
ruff format app/ tests/ scripts/     98 files reformatted   (dedicated commit)
long f-strings split                 implicit concatenation, AST-verified
long comments and docstrings         rewritten
remaining E501 in the gate           0
```

`E501` is now **enforced**. Four files keep an exemption, each named
individually in `pyproject.toml`:

| File | Why |
|---|---|
| `graph_queries.py`, `graph_expander.py` | embed Cypher sent to Neo4j |
| `prompt_builder.py`, `providers.py` | embed literal prompt text sent to the model |

In those four, a line break changes what is transmitted to a database or a
model. That is a behavior change, not formatting. `migrations/versions/*.py`
keeps its existing exemption: an applied revision is a record of what ran.

Every other line-length finding in the repository is fixed rather than
exempted, and `TestLintConfiguration` asserts the exemption set so it cannot
quietly grow.

## 8. Quality gate architecture

`scripts/check.py` is the canonical gate and the single source of truth:

```text
lint        ruff check app/ tests/ scripts/ migrations/
format      ruff format --check app/ tests/ scripts/
typecheck   mypy app/ scripts/ tests/
tests       pytest tests/
eval-phase7 Phase 7 guarded answer evaluation
eval-phase8 Phase 8 multi-turn conversation evaluation
```

`make check` delegates to it. CI invokes it. Pre-push runs it. None of the three
repeats the commands, so they cannot drift — and a test fails if the Makefile
starts naming `ruff`, `mypy`, or `pytest` directly.

It is a Python script rather than only a Make target because `make` is not
available on a stock Windows install, which is where this repository is
developed. The original Phase 11 pass recorded that as a limitation; this
removes it. The runner stops at the first failure, propagates the exit code,
uses no shell, and takes `--list` / `--only` / `--skip` for iterating.

Adding `evaluate` matters beyond tidiness: `docs/phase7_closeout_report.md`
already called those evaluations mandatory gates, but nothing ran them
automatically, so a regression in citation validity or conversation isolation
would only surface if someone remembered to run a script.

Every part of `check` is offline. Both evaluations were confirmed to pass with
all containers stopped, so the gate needs no Docker, no credential, and no paid
provider.

## 9. CI design

| Job | Services | Runs |
|---|---|---|
| `quality` | none | lint, typecheck, tests, Phase 7 eval, Phase 8 eval, offline migration-chain test |
| `migrations` | `pgvector/pgvector:pg16` | one-head check, empty DB → head, schema verification, idempotent re-run |

Triggers: `push` to `master`/`main`, and every `pull_request`.
Python **3.11**, matching `requires-python`, ruff's `target-version`, mypy's
`python_version`, and the `python:3.11-slim` Dockerfile base. No matrix.

`quality` provisions **no** service containers because the suite is genuinely
hermetic. `migrations` needs a real database to prove the Phase 9 fresh-clone
gate, and needs the pgvector image specifically: revision `0003` adds a
`vector(384)` column that stock postgres cannot create.

### Secrets

The workflow **references no repository secret and requires none** — no provider
API key, no `ADMIN_API_TOKEN`, no operator `.env`, no external LLM request. The
only credentials present are throwaway `postgres/postgres` values for an
ephemeral service container destroyed with the runner. `permissions` is
`contents: read`.

Tests assert this against the workflow with comments stripped, so the file can
explain *why* it needs no credentials — naming them to do so — without that
prose either satisfying or breaking the check.

### Validation limits

GitHub Actions cannot be executed locally. Validation was therefore structural
plus command-equivalent: the YAML was parsed and every step enumerated, and each
`quality` command was run directly against this checkout. This is stated plainly
rather than claimed as a green CI run.

## 10. Test strategy

| File | Focus |
|---|---|
| `tests/test_phase11_type_defects.py` | regressions for the four real defects |
| `tests/test_phase11_quality_gates.py` | gate composition, suppression policy, CI shape and security |

Both offline, deterministic, network-free, and independent of the operator
`.env`. They assert shape rather than whitespace, so ordinary edits do not break
them — but removing a gate, widening a suppression, or introducing a secret does.

## 11. Phase 11 debt register — final

Every item the original Phase 11 pass deferred, and its disposition.

| # | Debt | Disposition | Evidence |
|---|---|---|---|
| 1 | ~398 E501 findings advisory | **RESOLVED** | E501 enforced; 0 findings in the gate; 4 named DSL exemptions (§7) |
| 2 | `tests/` not type-checked | **RESOLVED** | `mypy app/ scripts/ tests/` — 148 files, 0 errors |
| 3 | `scripts/` not type-checked | **RESOLVED** | same command |
| 4 | `make` unavailable, so `make check` never literally ran | **RESOLVED** | `scripts/check.py` is the canonical runner; `make` delegates to it |
| 5 | CI never proven on GitHub | **RESOLVED** | run `34436853068`, both jobs green |
| 6 | Branch protection unconfigured | **RESOLVED** | both checks required, `enforce_admins` on, force-push and deletion blocked |
| 7 | No pre-commit hooks | **RESOLVED** | `.pre-commit-config.yaml`, all revs pinned |
| 8 | Stronger typing deferred | **RESOLVED** | `disallow_untyped_defs` + 6 further flags (§3) |
| 9 | `graph.py` HTTP routes uncovered | **RESOLVED** | `tests/test_phase11_graph_routes.py`, all 7 routes |

**No Phase 11 item remains open, deferred, or advisory.**

### Not Phase 11 scope

These belong to other phases and are listed only so they are not confused with
Phase 11 debt: `/v1/chat/*` retirement (Phase 9 decision, no milestone
defined), audit fail-open policy (Phase 9, deliberate), live model-driven
`plan_tool_calls()` (Phase 7), and corpus breadth / embedding quality
(Phase 6/7).
