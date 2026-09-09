"""Phase 11 tests for the developer quality gates and CI workflow.

Offline and deterministic. These assert the *shape* of the gates rather than
exact whitespace, so ordinary edits to the Makefile or workflow do not break
them, but removing a gate, widening a suppression, or introducing a secret does.

The contract being pinned:

    make check  ->  lint + typecheck + tests + evaluations
    CI          ->  the same commands, plus a real fresh-database migration
    CI secrets  ->  none: no provider key, no admin token, no operator .env
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
PYPROJECT = REPO_ROOT / "pyproject.toml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

LINT_TREES = ("app", "tests", "scripts", "migrations")

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
    variables = dict(re.findall(r"^([A-Z_]+) *= *([^\n]*)$", text, re.M))
    recipes = "\n".join(line for line in text.splitlines() if line.startswith("\t"))
    for name, value in variables.items():
        recipes = recipes.replace(f"$({name})", value)
    return recipes


def _target_prerequisites(name: str) -> list[str]:
    match = re.search(rf"^{re.escape(name)}:([^\n]*)$", _makefile_text(), re.M)
    assert match, f"target {name!r} not found"
    return match.group(1).split()


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _workflow_source() -> str:
    """Workflow text with comments stripped.

    The file documents *why* it needs no credentials, naming them to say so.
    Those sentences must not satisfy — or fail — a test about what the workflow
    actually does.
    """
    return "\n".join(
        line
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _all_run_commands(workflow: dict) -> str:
    return "\n".join(
        step["run"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "run" in step
    )


# =============================================================================
# The canonical developer gate
# =============================================================================


class TestCheckGate:
    def test_check_runs_lint_typecheck_tests_and_evaluations(self):
        assert _target_prerequisites("check") == ["lint", "typecheck", "test", "evaluate"]

    def test_typecheck_targets_the_production_package(self):
        assert re.search(r"mypy\s+app/", _recipe_lines())

    def test_lint_covers_every_first_party_tree(self):
        recipes = _recipe_lines()
        for tree in LINT_TREES:
            assert f"{tree}/" in recipes

    def test_evaluate_runs_both_safety_evaluations(self):
        recipes = _recipe_lines()
        assert "evaluate_phase7_answers.py" in recipes
        assert "evaluate_phase8_conversations.py" in recipes

    def test_check_does_not_depend_on_docker_or_a_live_provider(self):
        """A developer must be able to run the gate with nothing running."""
        recipes = "\n".join(
            line
            for line in _makefile_text().splitlines()
            if line.startswith("\t")
        )
        gate_targets = ("lint", "typecheck", "test", "evaluate")
        for target in gate_targets:
            body = re.search(
                rf"^{target}:[^\n]*\n((?:\t[^\n]*\n)*)", _makefile_text(), re.M
            )
            assert body
            assert "docker" not in body.group(1)
        assert recipes  # sanity


# =============================================================================
# mypy configuration and suppressions stay honest
# =============================================================================


class TestTypecheckConfiguration:
    def test_mypy_is_configured_for_the_supported_python(self):
        with PYPROJECT.open("rb") as handle:
            mypy_config = tomllib.load(handle)["tool"]["mypy"]
        assert mypy_config["python_version"] == "3.11"

    def test_errors_are_not_globally_disabled(self):
        with PYPROJECT.open("rb") as handle:
            mypy_config = tomllib.load(handle)["tool"]["mypy"]
        assert not mypy_config.get("ignore_errors", False)
        assert not mypy_config.get("follow_imports") == "skip"

    def test_production_typecheck_is_clean(self):
        """`mypy app/` must stay at zero errors, not merely be configured."""
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "app/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout[-3000:]

    def test_every_type_ignore_is_error_code_scoped(self):
        """A bare `# type: ignore` suppresses everything on the line."""
        bare = []
        for path in (REPO_ROOT / "app").rglob("*.py"):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "type: ignore" in line and "type: ignore[" not in line:
                    bare.append(f"{path.relative_to(REPO_ROOT)}:{number}")
        assert not bare, f"bare type: ignore comments: {bare}"

    def test_type_ignores_stay_rare(self):
        """Each remaining ignore is for an optional dependency without stubs."""
        ignores = [
            f"{path.relative_to(REPO_ROOT)}:{number}"
            for path in (REPO_ROOT / "app").rglob("*.py")
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            )
            if "type: ignore[" in line
        ]
        assert len(ignores) <= 3, f"unexpected growth in suppressions: {ignores}"

    def test_no_module_wide_mypy_suppression(self):
        for path in (REPO_ROOT / "app").rglob("*.py"):
            assert "# mypy: ignore-errors" not in path.read_text(encoding="utf-8")


# =============================================================================
# Lint suppressions stay narrow (carried forward from Phase 10)
# =============================================================================


class TestLintConfiguration:
    def test_only_cosmetic_rules_are_globally_ignored(self):
        with PYPROJECT.open("rb") as handle:
            ignore = tomllib.load(handle)["tool"]["ruff"]["lint"]["ignore"]
        assert set(ignore) == {"E501", "N806", "UP042"}

    def test_no_file_carries_a_blanket_ruff_noqa(self):
        for tree in ("app", "tests", "scripts"):
            for path in (REPO_ROOT / tree).rglob("*.py"):
                assert "# ruff: noqa\n" not in path.read_text(encoding="utf-8")

    def test_e501_remains_visible_through_an_advisory_target(self):
        """Excluded from the gate is not the same as hidden."""
        assert "--extend-select E501" in _recipe_lines()


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
        assert set(triggers) >= {"push", "pull_request"}

    @pytest.mark.parametrize(
        "command",
        ["ruff check", "mypy app/", "pytest tests/", "alembic upgrade head"],
    )
    def test_workflow_runs_each_required_gate(self, command):
        assert command in _all_run_commands(_workflow())

    def test_workflow_runs_both_safety_evaluations(self):
        commands = _all_run_commands(_workflow())
        assert "evaluate_phase7_answers.py" in commands
        assert "evaluate_phase8_conversations.py" in commands

    def test_lint_step_covers_every_first_party_tree(self):
        commands = _all_run_commands(_workflow())
        lint = next(line for line in commands.splitlines() if "ruff check" in line)
        for tree in LINT_TREES:
            assert f"{tree}/" in lint

    def test_uses_the_supported_python_version(self):
        workflow = _workflow()
        declared = str(workflow["env"]["PYTHON_VERSION"])
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
        assert "PHASE7_SYNTHESIS_ALLOW_EXTERNAL: \"true\"" not in text
        assert "api.openai.com" not in text

    def test_workflow_permissions_are_read_only(self):
        assert _workflow()["permissions"] == {"contents": "read"}

    def test_workflow_does_not_read_the_operator_env_file(self):
        commands = _all_run_commands(_workflow())
        assert "cp .env" not in commands
        assert "source .env" not in commands
