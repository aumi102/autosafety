"""
Maintenance/admin route protection — Phase 9 (fail-closed).

`docs/08_security_safety_guardrails.md` requires an admin role for ingestion
and other mutation routes. Full JWT authentication and user roles
(`docs/06_api_contract.md`) remain deferred; this is the smallest mechanism
that closes the open-maintenance-route exposure without adding an identity
platform.

Phase 8 shipped this guard in a fail-**open** form: when no token was
configured, maintenance routes stayed callable. Phase 9 makes it
fail-**closed**, because a misconfigured deployment silently exposing
ingestion, graph rebuild, and index rebuild is the more dangerous default.

Resolved behavior:

| Server token | Request header | Result |
|---|---|---|
| not configured | anything | **503 `ADMIN_PROTECTION_UNAVAILABLE`** |
| configured | missing | 401 `ADMIN_TOKEN_REQUIRED` |
| configured | wrong | 401 `ADMIN_TOKEN_REQUIRED` |
| configured | correct | allowed |

Read-only routes are never guarded by this dependency.

The token is accepted **only** via the `X-Admin-Token` request header — never
via query string, where it would leak into access logs, proxy logs, and browser
history. It is never logged, never returned in a response, never included in an
OpenAPI example, and compared in constant time.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

ADMIN_TOKEN_HEADER = "X-Admin-Token"

# A configured token shorter than this is treated as unconfigured: it offers no
# real protection and is almost always a placeholder left in an env file.
MIN_ADMIN_TOKEN_CHARS = 16

_PLACEHOLDER_TOKENS = frozenset({"changeme", "change-me", "placeholder", "secret", "admin"})


def _configured_token(settings: Settings) -> str:
    """Resolve the operator-configured admin token, or "" when unusable.

    `ADMIN_API_TOKEN` is canonical. `PHASE8_ADMIN_TOKEN` remains accepted as a
    deprecated alias so existing deployments keep working.
    """
    for attribute in ("ADMIN_API_TOKEN", "PHASE8_ADMIN_TOKEN"):
        raw = getattr(settings, attribute, "")
        if hasattr(raw, "get_secret_value"):
            raw = raw.get_secret_value()
        candidate = (raw or "").strip()
        if not candidate:
            continue
        if candidate.lower() in _PLACEHOLDER_TOKENS:
            continue
        if len(candidate) < MIN_ADMIN_TOKEN_CHARS:
            continue
        return candidate
    return ""


def admin_protection_enabled(settings: Settings | None = None) -> bool:
    """True when a usable admin token is configured and enforcement is active."""
    return bool(_configured_token(settings or get_settings()))


def _unavailable() -> HTTPException:
    """Fail closed: protection is required but the server cannot enforce it."""
    return HTTPException(
        status_code=503,
        detail={
            "error": {
                "code": "ADMIN_PROTECTION_UNAVAILABLE",
                "message": (
                    "Administrator protection is not configured, so maintenance "
                    "routes are disabled."
                ),
                "details": {},
            }
        },
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "error": {
                "code": "ADMIN_TOKEN_REQUIRED",
                "message": "A valid administrator token is required for this route.",
                "details": {},
            }
        },
    )


def verify_admin_token(
    x_admin_token: str | None = Header(
        default=None,
        alias=ADMIN_TOKEN_HEADER,
        description="Operator-issued administrator token. Header only.",
    ),
) -> None:
    """FastAPI dependency guarding maintenance and mutation routes."""
    expected = _configured_token(get_settings())

    if not expected:
        # Deliberately fail closed. The log line names the setting, never a value.
        logger.error("Maintenance route refused: no usable ADMIN_API_TOKEN is configured.")
        raise _unavailable()

    provided = (x_admin_token or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        logger.warning("Maintenance route refused: missing or invalid administrator token.")
        raise _unauthorized()
