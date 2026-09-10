"""Phase 11 tests for the developer quality gates, pre-commit, and CI workflow.

Offline and deterministic. These assert the *shape* of the gates rather than
exact whitespace, so ordinary edits do not break them, but removing a gate,
widening a suppression, letting the Makefile drift from CI, or introducing a
secret does.

The contract being pinned:

    scripts/check.py  ->  lint + format + typecheck + tests + both evaluations
    make check        ->  delegates to that runner, never duplicates it
    CI                ->  runs the same runner, plus a real fresh-database migration
    CI secrets        ->  none: no provider key, no admin token, no operator .env
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from scripts.check import GATES

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
PYPROJECT = REPO_ROOT / "pyproject.toml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RUNNER = REPO_ROOT / "scripts" / "check.py"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"

LINT_TREES = ("app", "tests", "scripts", "migrations")
FORMAT_TREES = ("app", "tests", "scripts")

REQUIRED_GATES = {
    "lint",
    "format",
    "typecheck",
    "tests",
    "eval-phase7",
    "eval-phase8",
    # Phase 12 made agentic planning safety a mandatory gate.
    "eval-phase12",
}

# Files exempt from E501 because they embed a DSL as a string constant, where a
# line break changes what is transmitted rather than how it reads.
E501_EXEMPT = {
    "migrations/versions/*.py",
    "app/services/graph/graph_queries.py",
    "app/services/graphrag/graph_expander.py",
    "app/services/answer_synthesis/prompt_builder.py",
    "app/services/answer_synthesis/providers.py",
}

# Values that must never appear in a committed workflow.
SECRET_NAMES = (
    "PHASE7_PROVIDER_API_KEY",
    "ADMIN_API_TOKEN",
    "PHASE8_ADMIN_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "NEO4J_PASSWORD",
)


def _makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _recipe_lines() -> str:
    """Executed recipe lines only, with Make variables expanded."""
    text = _makefile_text()
    variables = dict(re.findall(r"^([A-Z_]+) *\??= *([^\n]*)$", text, re.M))
    recipes = "\n".join(line for line in text.splitlines() if line.startswith("\t"))
    for name, value in variables.items():
        recipes = recipes.replace(f"$({name})", value)
    return recipes


def _target_body(name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}:[^\n]*\n((?:\t[^\n]*\n|\n(?=\t))*)", _makefile_text(), re.M
    )
    assert match, f"target {name!r} not found"
    return match.group(1)


def _gate(name: str):
    return next(gate for gate in GATES if gate.name == name)


def _workflow() -> dict:
    parsed: dict = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return parsed


def _workflow_source() -> str:
    """Workflow text with comments stripped.

    The file documents *why* it needs no credentials, naming them to say so.
    Those sentences must not satisfy — or fail — a test about what it does.
    """
    return "\n".join(
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _all_run_commands(workflow: dict) -> str:
    return "\n".join(
        step["run"] for job in workflow["jobs"].values() for step in job["steps"] if "run" in step
    )


def _ruff_lint_config() -> dict:
    with PYPROJECT.open("rb") as handle:
        config: dict = tomllib.load(handle)["tool"]["ruff"]["lint"]
    return config


def _mypy_config() -> dict:
    with PYPROJECT.open("rb") as handle:
        config: dict = tomllib.load(handle)["tool"]["mypy"]
    return config


# =============================================================================
# The canonical runner
# =============================================================================


class TestCanonicalRunner:
    """`scripts/check.py` is the authority; the Makefile and CI both delegate."""

    def test_runner_exists_and_declares_every_required_gate(self):
        assert RUNNER.is_file()
        assert {gate.name for gate in GATES} == REQUIRED_GATES

    def test_makefile_check_delegates_instead_of_duplicating(self):
        """Duplicated commands are exactly how a Makefile drifts from CI."""
        body = _target_body("check")
        assert "scripts/check.py" in body
        for tool in ("ruff", "mypy", "pytest"):
            assert tool not in body

    def test_runner_stops_at_the_first_failure(self):
        assert "break" in RUNNER.read_text(encoding="utf-8")

    def test_runner_never_uses_a_shell(self):
        """shell=True would let a path or argument be reinterpreted."""
        assert "shell=True" not in RUNNER.read_text(encoding="utf-8")

    def test_runner_propagates_a_nonzero_exit(self):
        result = subprocess.run(
            [sys.executable, "scripts/check.py", "--only", "no-such-gate"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_runner_lists_its_gates(self):
        result = subprocess.run(
            [sys.executable, "scripts/check.py", "--list"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        for name in REQUIRED_GATES:
            assert name in result.stdout

    def test_typecheck_gate_covers_every_first_party_tree(self):
        assert {"app/", "scripts/", "tests/"} <= set(_gate("typecheck").command)

    def test_lint_gate_covers_every_first_party_tree(self):
        assert {f"{tree}/" for tree in LINT_TREES} <= set(_gate("lint").command)

    def test_format_gate_checks_without_reformatting(self):
        assert "--check" in _gate("format").command

    def test_every_safety_evaluation_is_a_gate(self):
        commands = " ".join(" ".join(gate.command) for gate in GATES)
        assert "evaluate_phase7_answers.py" in commands
        assert "evaluate_phase8_conversations.py" in commands
        assert "evaluate_phase12_planning.py" in commands

    def test_no_gate_depends_on_docker_or_a_live_provider(self):
        """A developer must be able to run the whole gate with nothing running."""
        for gate in GATES:
            joined = " ".join(gate.command)
            assert "docker" not in joined
            assert "openai" not in joined.lower()


# =============================================================================
# Typing policy
# =============================================================================


class TestTypecheckConfiguration:
    def test_mypy_is_configured_for_the_supported_python(self):
        assert _mypy_config()["python_version"] == "3.11"

    def test_errors_are_not_globally_disabled(self):
        config = _mypy_config()
        assert not config.get("ignore_errors", False)
        assert config.get("follow_imports") != "skip"

    def test_untyped_production_functions_are_rejected(self):
        """The completion policy: no function escapes checking by being untyped."""
        config = _mypy_config()
        assert config["disallow_untyped_defs"] is True
        assert config["disallow_incomplete_defs"] is True
        assert config["warn_return_any"] is True

    @pytest.mark.parametrize(
        "flag",
        [
            "check_untyped_defs",
            "no_implicit_optional",
            "warn_redundant_casts",
            "warn_unused_ignores",
            "strict_equality",
        ],
    )
    def test_measured_strict_flags_stay_enabled(self, flag):
        assert _mypy_config()[flag] is True

    def test_only_tests_and_scripts_relax_untyped_defs(self):
        overrides = _mypy_config()["overrides"]
        relaxed = {
            module
            for override in overrides
            if override.get("disallow_untyped_defs") is False
            for module in override["module"]
        }
        assert relaxed == {"scripts.*", "tests.*"}

    def test_typecheck_is_clean_across_every_first_party_tree(self):
        """Must stay at zero errors, not merely be configured."""
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "app/", "scripts/", "tests/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout[-3000:]

    def test_every_type_ignore_is_error_code_scoped(self):
        """A bare `# type: ignore` suppresses everything on the line."""
        bare = []
        for tree in ("app", "scripts"):
            for path in (REPO_ROOT / tree).rglob("*.py"):
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    if "type: ignore" in line and "type: ignore[" not in line:
                        bare.append(f"{path.relative_to(REPO_ROOT)}:{number}")
        assert not bare, f"bare type: ignore comments: {bare}"

    def test_suppressions_stay_rare(self):
        """Each remaining ignore is a third-party or tooling limitation."""
        ignores = [
            f"{path.relative_to(REPO_ROOT)}:{number}"
            for tree in ("app", "scripts")
            for path in (REPO_ROOT / tree).rglob("*.py")
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
            if "type: ignore[" in line
        ]
        assert len(ignores) <= 3, f"unexpected growth in suppressions: {ignores}"

    def test_no_module_wide_mypy_suppression(self):
        marker = "# mypy: " + "ignore-errors"
        for tree in ("app", "scripts", "tests"):
            for path in (REPO_ROOT / tree).rglob("*.py"):
                if path == Path(__file__):
                    continue  # this file names the marker to test for it
                assert marker not in path.read_text(encoding="utf-8")


# =============================================================================
# Lint and formatting policy
# =============================================================================


class TestLintConfiguration:
    def test_only_cosmetic_rules_are_globally_ignored(self):
        assert set(_ruff_lint_config()["ignore"]) == {"N806", "UP042"}

    def test_line_length_is_enforced_not_ignored(self):
        """E501 was advisory through Phase 11; the completion pass enforces it."""
        lint = _ruff_lint_config()
        assert "E501" not in lint["ignore"]
        exempt = {path for path, rules in lint["per-file-ignores"].items() if "E501" in rules}
        assert exempt == E501_EXEMPT

    def test_no_line_length_debt_remains_outside_the_exemptions(self):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", *[f"{t}/" for t in LINT_TREES]],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert "E501" not in result.stdout
        assert result.returncode == 0, result.stdout[-2000:]

    def test_the_tree_is_formatted(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                *[f"{t}/" for t in FORMAT_TREES],
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout[-2000:]

    def test_migrations_are_linted_but_never_reformatted(self):
        """An applied revision records what ran; restyling it is not safe."""
        assert "migrations/" in " ".join(_gate("lint").command)
        assert "migrations/" not in " ".join(_gate("format").command)

    def test_no_file_carries_a_blanket_ruff_noqa(self):
        for tree in ("app", "tests", "scripts"):
            for path in (REPO_ROOT / tree).rglob("*.py"):
                assert "# ruff: noqa\n" not in path.read_text(encoding="utf-8")


# =============================================================================
# Pre-commit
# =============================================================================


class TestPreCommit:
    def test_config_exists_and_parses(self):
        assert PRE_COMMIT.is_file()
        assert yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))["repos"]

    def test_pre_commit_is_a_declared_dev_dependency(self):
        with PYPROJECT.open("rb") as handle:
            dev = tomllib.load(handle)["project"]["optional-dependencies"]["dev"]
        assert any(item.startswith("pre-commit") for item in dev)

    def test_every_hook_repo_is_pinned(self):
        """A floating rev makes the hook non-reproducible."""
        config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
        for repo in config["repos"]:
            if repo.get("repo") == "local":
                continue
            rev = str(repo.get("rev", ""))
            assert rev and rev not in {"latest", "HEAD", "main", "master"}, repo

    def test_hooks_cover_lint_format_and_typecheck(self):
        text = PRE_COMMIT.read_text(encoding="utf-8")
        for expected in ("ruff", "ruff-format", "mypy"):
            assert expected in text

    def test_commit_hooks_do_not_run_the_full_suite(self):
        """A commit hook that runs 1000+ tests gets bypassed instead of used."""
        config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
        for repo in config["repos"]:
            for hook in repo["hooks"]:
                stages = hook.get("stages", ["pre-commit"])
                entry = hook.get("entry", "")
                if "pre-commit" in stages:
                    # Named test files are fine; the unqualified suite is not.
                    assert not re.search(r"pytest\s+tests/\s*$", entry), hook["id"]
                    assert "scripts/check.py" not in entry, hook["id"]

    def test_the_full_gate_runs_before_push(self):
        config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
        entries = [
            hook.get("entry", "")
            for repo in config["repos"]
            for hook in repo["hooks"]
            if "pre-push" in hook.get("stages", [])
        ]
        assert any("scripts/check.py" in entry for entry in entries)


# =============================================================================
# CI workflow
# =============================================================================


class TestContinuousIntegration:
    def test_workflow_exists_and_parses(self):
        assert WORKFLOW.is_file()
        assert _workflow()["jobs"]

    def test_runs_on_push_and_pull_request(self):
        workflow = _workflow()
        # PyYAML parses a bare `on:` key as the boolean True.
        triggers = workflow.get("on", workflow.get(True))
        assert triggers is not None
        assert set(triggers) >= {"push", "pull_request"}

    def test_ci_invokes_the_canonical_runner(self):
        """CI and `make check` must not be able to diverge."""
        assert "scripts/check.py" in _all_run_commands(_workflow())

    def test_workflow_still_proves_a_real_migration(self):
        assert "alembic upgrade head" in _all_run_commands(_workflow())

    def test_uses_the_supported_python_version(self):
        declared = str(_workflow()["env"]["PYTHON_VERSION"])
        with PYPROJECT.open("rb") as handle:
            pyproject = tomllib.load(handle)
        assert pyproject["project"]["requires-python"] == ">=3.11"
        assert declared == "3.11"

    def test_migration_job_uses_a_pgvector_image(self):
        """Revision 0003 adds a vector column; stock postgres cannot apply it."""
        services = _workflow()["jobs"]["migrations"]["services"]
        assert "pgvector" in services["postgres"]["image"]

    def test_quality_job_provisions_no_services(self):
        """The suite is hermetic, so the fast job must stay service-free."""
        assert "services" not in _workflow()["jobs"]["quality"]

    def test_no_job_is_allowed_to_fail_silently(self):
        """continue-on-error would make a mandatory gate advisory."""
        assert "continue-on-error" not in _workflow_source()

    def test_every_action_is_version_pinned(self):
        for job in _workflow()["jobs"].values():
            for step in job["steps"]:
                uses = step.get("uses")
                if uses:
                    assert "@" in uses and not uses.endswith("@main"), uses


# =============================================================================
# CI security
# =============================================================================


class TestContinuousIntegrationSecurity:
    def test_workflow_requires_no_repository_secrets(self):
        assert "secrets." not in _workflow_source()

    @pytest.mark.parametrize("name", SECRET_NAMES)
    def test_workflow_never_references_a_credential_setting(self, name):
        assert name not in _workflow_source()

    def test_workflow_contains_no_secret_shaped_literal(self):
        text = _workflow_source()
        assert not re.search(r"sk-[A-Za-z0-9]{16,}", text)
        assert not re.search(r"gh[pousr]_[A-Za-z0-9]{16,}", text)

    def test_workflow_does_not_enable_an_external_provider(self):
        text = _workflow_source()
        assert 'PHASE7_SYNTHESIS_ALLOW_EXTERNAL: "true"' not in text
        assert "api.openai.com" not in text

    def test_workflow_permissions_are_read_only(self):
        assert _workflow()["permissions"] == {"contents": "read"}

    def test_workflow_never_uses_pull_request_target(self):
        """pull_request_target runs untrusted code with repository write scope."""
        assert "pull_request_target" not in _workflow_source()

    def test_workflow_does_not_interpolate_user_controlled_fields(self):
        """A branch or title interpolated into `run:` is a script-injection path."""
        commands = _all_run_commands(_workflow())
        for field in (
            "github.event.pull_request.title",
            "github.head_ref",
            "github.event.issue",
        ):
            assert field not in commands

    def test_workflow_does_not_read_the_operator_env_file(self):
        commands = _all_run_commands(_workflow())
        assert "cp .env" not in commands
        assert "source .env" not in commands
