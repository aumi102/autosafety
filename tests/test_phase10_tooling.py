"""Phase 10 quality-tooling tests.

Offline and deterministic. These pin the two things that made the tooling
untrustworthy before Phase 10:

1. `make lint` ran `ruff check autosafety/` — a directory that has never existed
   in this repository — so the quality gate silently inspected nothing.
2. `make db-reset` ran `rm -rf postgres_data redis_data neo4j_data`, but
   docker-compose.yml declares *named volumes*. The command removed nothing and
   reported success, so the stack came back with all data still in place.

They also lock the lint configuration's exclusions to the narrow, justified set,
so a future "just make it green" change has to break a test to land.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
PYPROJECT = REPO_ROOT / "pyproject.toml"

LINT_TREES = ("app", "tests", "scripts", "migrations")


def _makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _recipe_lines() -> str:
    """Only the executed recipe lines, so prose in comments cannot satisfy a test."""
    text = _makefile_text()
    variables = dict(re.findall(r"^([A-Z_]+) *= *([^\n]*)$", text, re.M))
    recipes = "\n".join(line for line in text.splitlines() if line.startswith("\t"))
    for name, value in variables.items():
        recipes = recipes.replace(f"$({name})", value)
    return recipes


def _target_body(name: str) -> str:
    """Return the recipe lines for one Make target."""
    text = _makefile_text()
    match = re.search(rf"^{re.escape(name)}:[^\n]*\n((?:\t[^\n]*\n|\n(?=\t))*)", text, re.M)
    assert match, f"target {name!r} not found in Makefile"
    return match.group(1)


def _ruff_config() -> dict:
    with PYPROJECT.open("rb") as handle:
        config: dict = tomllib.load(handle)["tool"]["ruff"]
        return config


# =============================================================================
# Make targets point at real paths
# =============================================================================


class TestLintTargets:
    def test_lint_does_not_reference_the_nonexistent_autosafety_package(self):
        """The original defect: the gate pointed at a directory that never existed."""
        assert not (REPO_ROOT / "autosafety").exists()
        assert "autosafety/" not in _recipe_lines()

    @pytest.mark.parametrize("tree", LINT_TREES)
    def test_lint_covers_every_first_party_tree(self, tree):
        assert (REPO_ROOT / tree).is_dir()
        assert tree + "/" in _recipe_lines()

    def test_lint_target_actually_runs_ruff(self):
        assert "ruff check" in _target_body("lint")

    @pytest.mark.parametrize("target", ["lint", "lint-all", "lint-fix", "typecheck", "check"])
    def test_quality_targets_exist(self, target):
        assert _target_body(target) is not None or target == "check"

    def test_lint_fix_never_uses_unsafe_fixes(self):
        """--unsafe-fixes can change behavior; it must never be the default path."""
        assert "--unsafe-fixes" not in _recipe_lines()

    def test_every_declared_target_is_phony(self):
        text = _makefile_text()
        declared = set(re.findall(r"^([a-z][a-z-]*):", text, re.M))
        phony_block = re.search(r"\.PHONY:((?:[^\n\\]*\\\n)*[^\n]*)", text)
        assert phony_block
        phony = set(phony_block.group(1).replace("\\", " ").split())
        assert declared <= phony, f"not declared .PHONY: {sorted(declared - phony)}"


# =============================================================================
# db-reset must be honest about what it deletes
# =============================================================================


class TestDbReset:
    def test_db_reset_does_not_rm_paths_that_are_not_bind_mounts(self):
        """docker-compose.yml uses named volumes, so `rm -rf postgres_data` was a no-op."""
        body = _target_body("db-reset")
        assert "rm -rf" not in body
        for name in ("postgres_data", "redis_data", "neo4j_data"):
            assert not (REPO_ROOT / name).exists()

    def test_db_reset_removes_named_volumes(self):
        assert "down -v" in _target_body("db-reset")

    def test_db_reset_requires_explicit_confirmation(self):
        """A destructive target must not run from a bare `make db-reset`."""
        body = _target_body("db-reset")
        assert "CONFIRM" in body
        # The guard must precede the destructive command.
        assert body.index("CONFIRM") < body.index("down -v")

    def test_compose_declares_the_named_volumes(self):
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert re.search(r"^volumes:", compose, re.M)
        for name in ("postgres_data", "redis_data", "neo4j_data"):
            assert name in compose


# =============================================================================
# Lint configuration: exclusions stay narrow and justified
# =============================================================================


class TestLintConfiguration:
    def test_correctness_rules_are_selected(self):
        select = _ruff_config()["lint"]["select"]
        for family in ("E", "F", "I", "N", "W", "UP"):
            assert family in select

    def test_only_cosmetic_rules_are_globally_ignored(self):
        """A blanket ignore would make the gate meaningless. Pin the exact set."""
        assert set(_ruff_config()["lint"]["ignore"]) == {"E501", "N806", "UP042"}

    def test_no_correctness_rule_family_is_globally_ignored(self):
        ignored = _ruff_config()["lint"]["ignore"]
        assert not any(rule.startswith("F") for rule in ignored)

    def test_per_file_ignores_are_limited_to_known_files(self):
        per_file = _ruff_config()["lint"]["per-file-ignores"]
        assert set(per_file) == {
            "migrations/versions/*.py",
            "migrations/env.py",
            "tests/test_phase2_sql_analytics.py",
        }

    def test_migration_exemptions_never_disable_correctness_checks(self):
        """Alembic revisions are exempt from style rules only, never from F8xx/E7xx."""
        per_file = _ruff_config()["lint"]["per-file-ignores"]
        for rules in per_file.values():
            assert not any(rule.startswith(("F8", "E7", "E9")) for rule in rules)

    def test_enforced_gate_is_green(self):
        """`make lint` must actually pass, not merely be configured."""
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", *LINT_TREES],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout[-3000:]
