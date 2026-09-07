"""
Phase 8 — minimal maintenance/admin route protection.

`docs/08_security_safety_guardrails.md` requires an admin role for
ingestion and other mutation routes. Full JWT authentication and user roles
(`docs/06_api_contract.md`) remain deferred; this is the smallest mechanism
that closes the open-maintenance-route exposure without adding an auth
platform.

Behavior is explicit and fail-closed once configured:

* `PHASE8_ADMIN_TOKEN` unset — maintenance routes stay open. This is the
  documented pre-existing exposure, not a claim of protection. The
  condition is reported by `/v1/health/admin-protection` and logged once.
* `PHASE8_ADMIN_TOKEN` set — every protected route requires a matching
  `X-Admin-Token` header. Comparison is constant time. A missing or wrong
  token returns 401 with no detail about the expected value.

The token is never logged, never returned, and never included in any
response body.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

ADMIN_TOKEN_HEADER = "X-Admin-Token"
_UNPROTECTED_WARNING_EMITTED = False


def _configured_token(settings: Settings) -> str:
    raw = settings.PHASE8_ADMIN_TOKEN
    if hasattr(raw, "get_secret_value"):
        raw = raw.get_secret_value()
    return (raw or "").strip()


def admin_protection_enabled(settings: Settings | None = None) -> bool:
    """True when an admin token is configured and maintenance routes are enforced."""
    return bool(_configured_token(settings or get_settings()))


def verify_admin_token(
    x_admin_token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
) -> None:
    """FastAPI dependency guarding maintenance and mutation routes."""
    global _UNPROTECTED_WARNING_EMITTED
    expected = _configured_token(get_settings())

    if not expected:
        if not _UNPROTECTED_WARNING_EMITTED:
            logger.warning(
                "Maintenance routes are unprotected: PHASE8_ADMIN_TOKEN is not configured."
            )
            _UNPROTECTED_WARNING_EMITTED = True
        return

    provided = (x_admin_token or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "ADMIN_TOKEN_REQUIRED",
                    "message": "A valid administrator token is required for this route.",
                    "details": {},
                }
            },
        )
