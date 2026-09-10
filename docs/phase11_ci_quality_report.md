# Phase 11 — CI and Quality Acceptance Report

## 1. Scope

Acceptance evidence for the type-safety work, the `make check` gate, and the
GitHub Actions workflow. No Docker service was required for any of it except the
one explicitly-scoped migration check, and no external LLM request was made.

## 2. Entry state

```text
branch          master
HEAD            7fabade
working tree    clean
pytest tests/   1039 passed, 0 failed, 0 skipped, 4 warnings, 16.89s
mypy app/       47 errors in 16 files (106 source files)
ruff (gate)     All checks passed!
ruff --extend-select E501   398 findings
CI              none  (.github does not exist)
```

## 3. Typecheck

```text
before   mypy app/    Found 47 errors in 16 files (checked 106 source files)
after    mypy app/    Success: no issues found in 106 source files
```

Suppressions in `app/`, complete inventory:

```text
app/services/graphrag/embedding_provider.py:146  # type: ignore[import-not-found]
app/services/ops/probes.py:158                   # type: ignore[import-not-found]
```

Both are optional third-party packages that are not project dependencies and
ship no stubs (`sentence_transformers`, `redis`). Both are error-code scoped.
There is no bare `# type: ignore`, no `# mypy: ignore-errors`, and no `noqa`
anywhere in `app/`. One pre-existing `# type: ignore[assignment]` was removed
rather than kept.

## 4. Lint

```text
ruff check app/ tests/ scripts/ migrations/              All checks passed!
ruff check ... --extend-select E501                      398 findings (advisory)
```

E501 measurement behind the decision to keep it advisory:

```text
ruff format --check app/ tests/ scripts/   96 files would be reformatted
ruff format --diff  ...                    5539 changed lines
E501 in app/ after formatting              39   (formatter cannot split long
                                                 strings, URLs, or comments)
```

Formatting would not make E501 gate-able; it would only make the debt smaller
and far more expensive to review. Recorded in `docs/phase11_design.md` §7.

## 5. Quality gate

```text
make check  ->  lint  typecheck  test  evaluate
```

Executed directly (see §8 on `make` availability):

```text
ruff check app/ tests/ scripts/ migrations/   All checks passed!
mypy app/                                     Success: no issues found in 106 source files
pytest tests/ -q                              1097 passed, 4 warnings
scripts/evaluate_phase7_answers.py            passed=True  cases=20  failed_gates=[]
scripts/evaluate_phase8_conversations.py      passed=True  failed_gates=[]
```

Every part ran with **all Docker containers stopped**:

```text
autosafety-postgres-1   Exited
autosafety-neo4j-1      Exited
autosafety-redis-1      Exited
```

Both evaluations were confirmed offline specifically, because adding them to the
gate is only safe if they need no infrastructure.

## 6. CI workflow

`.github/workflows/ci.yml`, structurally verified:

```text
YAML parses OK
jobs: ['quality', 'migrations']
  quality: 9 steps
    - actions/checkout@v4
    - actions/setup-python@v5
    - Install
    - Lint
    - Typecheck
    - Tests
    - Phase 7 guarded answer evaluation
    - Phase 8 multi-turn conversation evaluation
    - Migration chain (offline)
  migrations: 8 steps
    - actions/checkout@v4
    - actions/setup-python@v5
    - Install
    - Enable pgvector
    - Exactly one head
    - Migrate an empty database to head
    - Verify the schema was created
    - Re-running upgrade is a no-op
```

Command-equivalent execution of the `quality` job against this checkout:

```text
ruff check app/ tests/ scripts/ migrations/     All checks passed!
mypy app/                                       Success: no issues found in 106 source files
pytest tests/test_phase9_migrations.py -q       17 passed
alembic heads | grep -c "(head)"                1        -> one-head check passes
```

**GitHub Actions cannot be executed locally.** This is structural validation
plus command-equivalent execution, not a green CI run. The first real run will
occur on the next push to a GitHub remote.

## 7. CI security

```text
references to `secrets.`                 none
PHASE7_PROVIDER_API_KEY                  absent
ADMIN_API_TOKEN / PHASE8_ADMIN_TOKEN     absent
OPENAI_API_KEY / ANTHROPIC_API_KEY       absent
NEO4J_PASSWORD                           absent
secret-shaped literals (sk-…, ghp_…)     none
api.openai.com                           absent
operator .env read                       never
permissions                              contents: read
```

The only credentials in the file are `postgres/postgres` for an ephemeral
service container that is destroyed with the runner. No job reaches an external
provider, so CI cannot spend a paid request.

These properties are asserted by `TestContinuousIntegrationSecurity`, which
compares against the workflow **with comments stripped** — the file names those
settings in prose to explain why it does not need them, and that explanation
must not be what satisfies the test.

## 8. `make` availability

`make` is not installed in this development environment:

```text
$ make lint
/usr/bin/bash: line 1: make: command not found
```

This is unchanged from Phase 10 and is an environment limitation, not a repo
defect. The Makefile is correct for developers and for CI, and is validated two
ways: each target's command is executed directly, and
`tests/test_phase11_quality_gates.py` parses the file — expanding `$(LINT_PATHS)`
and reading only tab-indented recipe lines — to assert `check` invokes all four
gates and that `typecheck` targets `app/`.

No system package was installed to make `make` available; no repository document
requires it.

## 9. Regressions

```text
Phase 7 evaluation   passed=True  cases=20  failed_gates=[]
Phase 8 evaluation   passed=True  failed_gates=[]
    citation_validity 1.0        citation_coverage 1.0
    conversation_isolation 1.0   prompt_injection_resistance 1.0
    prior_text_leak 0.0          prior_citation_leak 0.0
    causal_guard 1.0             context_bounds 1.0        determinism 1.0
```

Phase 9 security and observability suites, and Phase 10 tooling and ops suites,
pass unchanged. No behavior test was weakened to accommodate a typing change.

Safety boundaries were not touched. `GuardedAnswerService`, `ToolRegistry`,
citation validation, deterministic confidence, abstention, conversation
isolation, cross-turn provenance, the fail-closed admin guard, and the Phase 10
audit-read allowlist are all unmodified. Where mypy and an existing invariant
disagreed, the annotation was corrected — never the guard.

## 10. Test suite

```text
pytest tests/ -q     1097 passed, 4 warnings
```

Entry was 1039. Phase 11 added 58 tests (22 defect regressions, 36 quality-gate
tests). Run with every container stopped.

## 11. Real LLM

**REAL LLM LIVE CALL NOT REQUIRED FOR PHASE 11.**

No live external request was made. Phase 11 concerns typing, gates, and CI; both
evaluations use the deterministic provider, and CI has no provider credential.

## 12. Completion pass — every deferred item closed

The sections above record the original Phase 11 pass. This section records the
completion pass that closed the four limitations it left.

### Typecheck

```text
before   mypy app/                     0 errors (106 files)   scripts/ and tests/ unchecked
after    mypy app/ scripts/ tests/     0 errors (148 files)
```

Strictness enabled after measuring each flag: `disallow_untyped_defs`,
`disallow_incomplete_defs`, `check_untyped_defs`, `no_implicit_optional`,
`warn_redundant_casts`, `warn_unused_ignores`, `strict_equality`,
`warn_return_any`. 93 newly reported errors, all resolved. Suppressions across
`app/` and `scripts/`: three, all error-code scoped.

### Line length

```text
before   398 findings, advisory
after    0 findings in the enforced gate
```

`ruff format` applied to `app/`, `tests/`, `scripts/` in a dedicated
formatting-only commit (98 files), then long f-strings split by an AST-verified
transform and long comments rewritten by hand. Four files keep a named E501
exemption because they embed Cypher or prompt text; `migrations/versions/*.py`
keeps its existing one.

### Canonical runner

```text
python scripts/check.py

--- lint: PASS in 0.1s
--- format: PASS in 0.1s
--- typecheck: PASS in 0.5s
--- tests: PASS in 17.6s
--- eval-phase7: PASS in 0.7s
--- eval-phase8: PASS in 1.1s
All gates passed (6/6).
```

`make check` delegates to it; CI invokes it; pre-push runs it. The `make`
limitation is gone: the canonical path is Python and runs anywhere.

### Graph route coverage

`tests/test_phase11_graph_routes.py` — 32 tests covering all seven routes in
`graph.py`, including six cases for the recall-paths route that Phase 11 found
broken. A final test fails if a new graph route ships without HTTP coverage.

### Real GitHub Actions run

The repository had **no git remote**; one was created with the user's explicit
approval and the branch pushed.

```text
repository   github.com/aumi102/autosafety   (public)
run id       34436853068
trigger      push -> master
conclusion   success

  Lint, typecheck, tests, evaluations   success
  Fresh-database migration              success
```

Both jobs passed on the first run. The `quality` job runs `scripts/check.py`;
the `migrations` job creates an empty PostgreSQL database on
`pgvector/pgvector:pg16`, applies the chain to head, and verifies the schema.

### Branch protection

Configured on `master` and read back from the API:

```text
required_checks       ["Lint, typecheck, tests, evaluations", "Fresh-database migration"]
strict_up_to_date     true
enforce_admins        true
force_pushes_allowed  false
deletions_allowed     false
linear_history        true
```

`enforce_admins: true` means the repository owner cannot bypass the checks
either.

## 13. Verdict

**PHASE 11 CI AND QUALITY ACCEPTANCE PASSED**

No Phase 11 limitation remains. The register in `docs/phase11_design.md` §11
lists all nine items with their disposition and evidence.
