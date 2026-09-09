"""Phase 10 dependency probes for readiness and operator diagnostics.

Every probe is read-only, bounded, and reports a *shape*, never a detail: a
boolean, a short status word, and at most a coarse error class. Connection
strings, credentials, hostnames, and driver exception text never leave this
module — a readiness endpoint is typically the most exposed route a service
has, and an unauthenticated caller must not be able to use it to learn where
the database lives or why it is unhappy.

Nothing here mutates state. `readiness()` is safe to call on a hot path.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import text

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# A probe that cannot answer quickly is a failed probe: readiness must not hang
# behind a dependency that is merely slow.
PROBE_TIMEOUT_SECONDS = 3

# Dependencies that must be up for the service to answer questions at all.
REQUIRED_DEPENDENCIES = ("postgresql",)


@dataclass(frozen=True)
class DependencyStatus:
    """One dependency's health. Carries no connection detail of any kind."""

    name: str
    reachable: bool
    required: bool
    detail: str | None = None
    latency_ms: int | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "reachable": self.reachable,
            "required": self.required,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True)
class ReadinessReport:
    """Aggregate readiness. `ready` is false when any *required* dependency is down."""

    ready: bool
    dependencies: list[DependencyStatus] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": "ready" if self.ready else "not_ready",
            "ready": self.ready,
            "dependencies": [d.to_dict() for d in self.dependencies],
        }


def _classify(exc: BaseException) -> str:
    """Reduce a driver exception to a coarse, non-identifying class.

    The exception's own message is logged, never returned: SQLAlchemy and the
    Neo4j driver both put the host, port, and user straight into the text.
    """
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "auth" in name:
        return "auth_failed"
    if "operational" in name or "connection" in name or "servicunavailable" in name:
        return "unreachable"
    return "error"


def _timed(name: str, required: bool, probe) -> DependencyStatus:
    """Run one probe, converting any failure into a safe status."""
    started = time.monotonic()
    try:
        probe()
    except Exception as exc:
        logger.warning("Readiness probe failed for %s: %s", name, exc)
        return DependencyStatus(
            name=name,
            reachable=False,
            required=required,
            detail=_classify(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return DependencyStatus(
        name=name,
        reachable=True,
        required=required,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def probe_postgresql(settings: Settings | None = None) -> DependencyStatus:
    """`SELECT 1` against the application engine."""

    def _run() -> None:
        from app.db.session import get_sync_engine

        engine = get_sync_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    return _timed("postgresql", True, _run)


def probe_pgvector(settings: Settings | None = None) -> DependencyStatus:
    """Confirm the `vector` extension is installed, not merely that PG is up."""

    def _run() -> None:
        from app.db.session import get_sync_engine

        engine = get_sync_engine()
        with engine.connect() as connection:
            installed = connection.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).scalar()
            if not installed:
                raise RuntimeError("vector extension not installed")

    return _timed("pgvector", False, _run)


def probe_neo4j(settings: Settings | None = None) -> DependencyStatus:
    """Driver-level connectivity check. Runs no query."""

    def _run() -> None:
        from app.services.graph.neo4j_client import verify_connectivity

        if not verify_connectivity():
            raise RuntimeError("neo4j not reachable")

    return _timed("neo4j", False, _run)


def probe_redis(settings: Settings | None = None) -> DependencyStatus:
    """Redis is provisioned but unused by the answer path, so it is never required."""
    resolved = settings or get_settings()
    url = getattr(resolved, "REDIS_URL", None)
    if not url:
        return DependencyStatus(
            name="redis", reachable=False, required=False, detail="not_configured"
        )

    try:
        import redis  # type: ignore[import-not-found]  # optional, not a project dep
    except ImportError:
        # The client library is not a project dependency: Redis is provisioned
        # by docker-compose but nothing on the answer path uses it yet. Say that
        # plainly rather than reporting a generic probe error, which would send
        # an operator looking for a network fault that does not exist.
        return DependencyStatus(
            name="redis", reachable=False, required=False, detail="client_not_installed"
        )

    def _run() -> None:
        client = redis.Redis.from_url(url, socket_connect_timeout=PROBE_TIMEOUT_SECONDS)
        try:
            client.ping()
        finally:
            client.close()

    return _timed("redis", False, _run)


def readiness(settings: Settings | None = None) -> ReadinessReport:
    """Probe every dependency and decide whether this instance should take traffic.

    Only `REQUIRED_DEPENDENCIES` can make the service not-ready. Neo4j, pgvector,
    and Redis degrade the answer quality rather than removing the ability to
    answer, so they are reported but never fail the probe.
    """
    resolved = settings or get_settings()
    statuses = [
        probe_postgresql(resolved),
        probe_pgvector(resolved),
        probe_neo4j(resolved),
        probe_redis(resolved),
    ]
    ready = all(s.reachable for s in statuses if s.name in REQUIRED_DEPENDENCIES)
    return ReadinessReport(ready=ready, dependencies=statuses)
