"""Phase 9 migration-history correctness tests.

Offline and deterministic. These assert the property that actually matters:
**a fresh clone must be able to rebuild the schema from tracked migrations.**

Phase 8 shipped with `.gitignore` excluding `migrations/versions/*.py`, so none
of the revisions were tracked and a clean checkout could not migrate at all.
These tests lock the fix in place.

No database connection is required: the revision chain is validated by parsing
the tracked revision files and by loading Alembic's own ScriptDirectory.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = ROOT / "migrations" / "versions"

REVISION_RE = re.compile(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
DOWN_REVISION_RE = re.compile(
    r"^down_revision(?::\s*(?:str|Union\[str, None\]))?\s*=\s*(?:[\"']([^\"']+)[\"']|None)",
    re.MULTILINE,
)


def _revision_files() -> list[Path]:
    return sorted(p for p in VERSIONS_DIR.glob("*.py") if p.name != "__init__.py")


def _parse(path: Path) -> tuple[str, str | None]:
    text = path.read_text(encoding="utf-8")
    revision = REVISION_RE.search(text)
    down = DOWN_REVISION_RE.search(text)
    assert revision, f"{path.name} has no revision identifier"
    assert down, f"{path.name} has no down_revision"
    return revision.group(1), down.group(1)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout


# =============================================================================
# A. Migration tracking
# =============================================================================


class TestMigrationsAreTracked:
    def test_gitignore_does_not_exclude_migration_versions(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "migrations/versions/*.py" not in ignored
        assert "migrations/versions/" not in ignored.replace("!migrations/versions/.gitkeep", "")

    def test_every_revision_file_is_tracked_by_git(self):
        tracked = set(_git("ls-files", "migrations/versions").split())
        on_disk = {f"migrations/versions/{p.name}" for p in _revision_files()}
        assert on_disk, "no revision files found on disk"
        missing = sorted(on_disk - tracked)
        assert not missing, f"untracked revision files would break a fresh clone: {missing}"

    def test_no_revision_file_is_git_ignored(self):
        for path in _revision_files():
            result = subprocess.run(
                ["git", "check-ignore", f"migrations/versions/{path.name}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode != 0, f"{path.name} is git-ignored"

    def test_complete_historical_chain_is_present(self):
        """Every revision that existed before the tracking fix must be present."""
        names = {p.name for p in _revision_files()}
        for expected in (
            "2025_01_01_0001_initial.py",
            "2025_01_01_0002_phase6_graphrag.py",
            "2025_01_01_0003_phase6_pgvector_column.py",
            "2025_01_01_0004_phase8_conversation.py",
        ):
            assert expected in names


# =============================================================================
# Revision chain integrity
# =============================================================================


class TestRevisionChain:
    def test_revision_ids_are_unique(self):
        revisions = [_parse(p)[0] for p in _revision_files()]
        assert len(revisions) == len(set(revisions))

    def test_exactly_one_base_revision(self):
        bases = [rev for rev, down in map(_parse, _revision_files()) if down is None]
        assert len(bases) == 1, f"expected exactly one base revision, found {bases}"

    def test_every_down_revision_resolves(self):
        parsed = [_parse(p) for p in _revision_files()]
        known = {rev for rev, _ in parsed}
        for revision, down in parsed:
            if down is not None:
                assert down in known, f"{revision} points at missing revision {down}"

    def test_chain_has_exactly_one_head(self):
        parsed = [_parse(p) for p in _revision_files()]
        referenced = {down for _, down in parsed if down is not None}
        heads = [rev for rev, _ in parsed if rev not in referenced]
        assert len(heads) == 1, f"expected exactly one head, found {heads}"

    def test_chain_is_linear_and_reaches_every_revision(self):
        parsed = dict(_parse(p) for p in _revision_files())
        referenced = {down for down in parsed.values() if down is not None}
        head = next(rev for rev in parsed if rev not in referenced)
        walked, cursor = [], head
        while cursor is not None:
            assert cursor not in walked, "cycle detected in revision chain"
            walked.append(cursor)
            cursor = parsed[cursor]
        assert set(walked) == set(parsed), "chain does not reach every revision"

    def test_phase8_revision_follows_phase6_pgvector(self):
        parsed = dict(_parse(p) for p in _revision_files())
        assert parsed["2025_01_01_0004"] == "2025_01_01_0003"


@pytest.fixture(scope="module")
def script_directory():
    """Alembic's own view of the revision chain."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


class TestAlembicScriptDirectory:
    """Validate the chain through Alembic itself, not just by parsing."""

    def test_alembic_reports_exactly_one_head(self, script_directory):
        assert len(script_directory.get_heads()) == 1

    def test_alembic_can_walk_base_to_head(self, script_directory):
        revisions = list(script_directory.walk_revisions())
        assert len(revisions) == len(_revision_files())

    def test_alembic_head_matches_the_newest_revision_file(self, script_directory):
        """Derived from the chain, so a new revision never needs a test edit."""
        parsed = dict(_parse(p) for p in _revision_files())
        referenced = {down for down in parsed.values() if down is not None}
        expected_head = next(rev for rev in parsed if rev not in referenced)
        assert script_directory.get_current_head() == expected_head


# =============================================================================
# Migration content safety
# =============================================================================


class TestMigrationSafety:
    def test_no_upgrade_path_is_destructive(self):
        """Upgrades may add and backfill; they must never drop or delete data."""
        for path in _revision_files():
            text = path.read_text(encoding="utf-8")
            if "def upgrade()" not in text:
                continue
            body = text.split("def upgrade()")[1].split("def downgrade()")[0]
            for destructive in ("drop_table", "DROP TABLE", "DELETE FROM", "TRUNCATE"):
                assert destructive not in body, f"{path.name} upgrade is destructive"

    def test_no_migration_contains_a_credential(self):
        for path in _revision_files():
            text = path.read_text(encoding="utf-8").lower()
            for secret in ("api_key", "password=", "postgresql://", "bolt://", "sk-"):
                assert secret not in text, f"{path.name} contains {secret}"

    def test_initial_migration_enables_required_extensions(self):
        text = (VERSIONS_DIR / "2025_01_01_0001_initial.py").read_text(encoding="utf-8")
        assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in text
        assert "CREATE EXTENSION IF NOT EXISTS vector" in text

    def test_every_revision_defines_a_downgrade(self):
        for path in _revision_files():
            assert "def downgrade()" in path.read_text(encoding="utf-8"), path.name
