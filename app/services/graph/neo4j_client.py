"""
Neo4j client — driver creation, connectivity, and session management.

Supports dependency injection for tests via constructor override.
"""

from __future__ import annotations

import logging
from typing import Optional
from contextlib import contextmanager

from neo4j import GraphDatabase, Driver, Session
from neo4j.api import BookmarkManager

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Neo4j notification messages embed full Cypher text. Phase 7 public API/CLI
# logs must not disclose raw queries; application-level graph warnings remain
# available in bounded tool and answer results.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

_driver: Optional[Driver] = None


def get_neo4j_driver() -> Driver:
    """Get or create the global Neo4j driver singleton."""
    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
    return _driver


def close_driver() -> None:
    """Close the global driver if open."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def verify_connectivity() -> bool:
    """Check if Neo4j is reachable."""
    try:
        driver = get_neo4j_driver()
        driver.verify_connectivity()
        return True
    except Exception as e:
        logger.warning(f"Neo4j connectivity check failed: {e}")
        return False


class Neo4jClient:
    """
    Neo4j client wrapper with dependency-injection support.

    Use constructor to inject a fake/mock driver in tests.
    """

    def __init__(self, driver: Optional[Driver] = None):
        self._driver = driver

    @property
    def driver(self) -> Driver:
        if self._driver is not None:
            return self._driver
        return get_neo4j_driver()

    def close(self) -> None:
        """Close the driver if injected (not singleton)."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    @contextmanager
    def session(self, **kwargs):
        """Context manager for a Neo4j session."""
        session = self.driver.session(**kwargs)
        try:
            yield session
        finally:
            session.close()

    def execute(self, cypher: str, params: Optional[dict] = None) -> list[dict]:
        """Execute a Cypher query and return results as list of dicts."""
        with self.session() as session:
            result = session.run(cypher, params or {})
            return [dict(record) for record in result]

    def execute_single(self, cypher: str, params: Optional[dict] = None) -> Optional[dict]:
        """Execute and return first result."""
        results = self.execute(cypher, params)
        return results[0] if results else None
