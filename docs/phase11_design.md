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

## 7. E501 and formatting decision

Phase 10 excluded `E501` from the enforced gate and routed it to `make lint-all`.
Phase 11 re-examined that with measurements rather than inheriting it:

```text
ruff format would reformat  96 files, 5539 changed lines
E501 in app/ after formatting  39   (down from 398 across all trees)
```

**Decision: keep E501 advisory (Phase 10's option B), confirmed by measurement.**

The formatter cannot break long string literals, URLs, or comments, so 39
findings survive it. Adopting it would therefore push 5539 lines of churn
through stable, security-sensitive Phase 7–10 code **and still leave E501
unable to serve as a hard gate**. The debt does not become zero; it becomes
smaller and much more expensive to review.

The debt stays visible and counted through `make lint-all` (398). This records
the numbers so a future phase can revisit the trade-off with evidence rather
than re-deriving it.

## 8. Quality gate architecture

```make
lint       ruff check app/ tests/ scripts/ migrations/   enforced, green
typecheck  mypy app/                                     enforced, zero errors
test       pytest tests/                                 enforced
evaluate   Phase 7 + Phase 8 safety evaluations          enforced
check      lint + typecheck + test + evaluate            the canonical gate
lint-all   lint plus E501                                advisory
```

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

## 11. Deferred

- **398 `E501` findings** — measured, visible through `make lint-all`; §7.
- **`make` is not installed in the development environment.** The Makefile is
  correct for developers and CI; locally its targets are validated by running
  their commands directly and by parsing the file in tests.
- **`tests/` and `scripts/` are not type-checked.** `app/` is the production
  package and the primary gate; extending mypy outward is a future step.
- `/v1/chat/*` removal, audit fail-open policy, live model-driven
  `plan_tool_calls()`, and the retrieval/corpus debts are all unchanged.
