"""Phase 9 maintenance/admin route protection tests (fail-closed).

Offline and deterministic. Settings come from explicit `Settings(_env_file=None)`
fixtures, never the operator `.env`, and no real secret is required.

Phase 8 shipped this guard fail-**open**: with no token configured, maintenance
routes stayed callable. Phase 9 makes it fail-**closed**. These tests pin the
new contract:

    server token unset      -> 503 ADMIN_PROTECTION_UNAVAILABLE
    server token set, none  -> 401 ADMIN_TOKEN_REQUIRED
    server token set, wrong -> 401 ADMIN_TOKEN_REQUIRED
    server token set, right -> allowed
"""

from __future__ import annotations

import inspect
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core import security as security_module
from app.core.config import Settings
from app.core.security import (
    ADMIN_TOKEN_HEADER,
    MIN_ADMIN_TOKEN_CHARS,
    admin_protection_enabled,
    verify_admin_token,
)
from app.main import app

VALID_TOKEN = "phase9-admin-token-value-32chars"
MUTATION_ROUTES = (
    "/v1/ingestion/nhtsa/phase1/run",
    "/v1/ingestion/nhtsa/phase1-5/complaints-flat-file/run",
    "/v1/graph/schema/setup",
    "/v1/graph/build",
    "/v1/graphrag/index",
)
READ_ONLY_ROUTES = (
    "/v1/ingestion/source-runs",
    "/v1/conversations/status/config",
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def configured(monkeypatch):
    """Server configured with a usable admin token."""
    monkeypatch.setattr(
        security_module,
        "get_settings",
        lambda: _settings(ADMIN_API_TOKEN=SecretStr(VALID_TOKEN)),
    )


@pytest.fixture
def unconfigured(monkeypatch):
    """Server with no usable admin token."""
    monkeypatch.setattr(security_module, "get_settings", lambda: _settings())


# =============================================================================
# Token resolution
# =============================================================================


class TestTokenResolution:
    def test_unset_token_is_not_protection(self):
        assert admin_protection_enabled(_settings()) is False

    def test_valid_token_enables_protection(self):
        assert admin_protection_enabled(_settings(ADMIN_API_TOKEN=SecretStr(VALID_TOKEN))) is True

    def test_deprecated_phase8_alias_still_works(self):
        settings = _settings(PHASE8_ADMIN_TOKEN=SecretStr(VALID_TOKEN))
        assert admin_protection_enabled(settings) is True

    def test_canonical_token_takes_precedence_over_alias(self, monkeypatch):
        settings = _settings(
            ADMIN_API_TOKEN=SecretStr(VALID_TOKEN),
            PHASE8_ADMIN_TOKEN=SecretStr("legacy-token-value-long-enough"),
        )
        monkeypatch.setattr(security_module, "get_settings", lambda: settings)
        assert verify_admin_token(VALID_TOKEN) is None

    def test_short_token_is_rejected_as_unconfigured(self):
        short = "x" * (MIN_ADMIN_TOKEN_CHARS - 1)
        assert admin_protection_enabled(_settings(ADMIN_API_TOKEN=SecretStr(short))) is False

    @pytest.mark.parametrize(
        "placeholder", ["changeme", "CHANGEME", "placeholder", "secret", "admin"]
    )
    def test_placeholder_tokens_are_rejected(self, placeholder):
        settings = _settings(ADMIN_API_TOKEN=SecretStr(placeholder))
        assert admin_protection_enabled(settings) is False

    def test_whitespace_only_token_is_rejected(self):
        assert admin_protection_enabled(_settings(ADMIN_API_TOKEN=SecretStr("     "))) is False


# =============================================================================
# B. Fail-closed dependency behavior
# =============================================================================


class TestFailClosedDependency:
    def test_missing_server_config_fails_closed(self, unconfigured):
        with pytest.raises(Exception) as exc:
            verify_admin_token(VALID_TOKEN)
        assert exc.value.status_code == 503
        assert exc.value.detail["error"]["code"] == "ADMIN_PROTECTION_UNAVAILABLE"

    def test_missing_server_config_fails_closed_without_a_header(self, unconfigured):
        with pytest.raises(Exception) as exc:
            verify_admin_token(None)
        assert exc.value.status_code == 503

    def test_missing_token_is_rejected_when_configured(self, configured):
        with pytest.raises(Exception) as exc:
            verify_admin_token(None)
        assert exc.value.status_code == 401
        assert exc.value.detail["error"]["code"] == "ADMIN_TOKEN_REQUIRED"

    def test_wrong_token_is_rejected(self, configured):
        with pytest.raises(Exception) as exc:
            verify_admin_token("not-the-right-token-but-long-enough")
        assert exc.value.status_code == 401

    def test_empty_header_is_rejected(self, configured):
        with pytest.raises(Exception) as exc:
            verify_admin_token("   ")
        assert exc.value.status_code == 401

    def test_prefix_of_valid_token_is_rejected(self, configured):
        with pytest.raises(Exception) as exc:
            verify_admin_token(VALID_TOKEN[:-1])
        assert exc.value.status_code == 401

    def test_valid_token_is_accepted(self, configured):
        assert verify_admin_token(VALID_TOKEN) is None

    def test_surrounding_whitespace_is_tolerated(self, configured):
        assert verify_admin_token(f"  {VALID_TOKEN}  ") is None


# =============================================================================
# Route-level enforcement
# =============================================================================


class TestRouteEnforcement:
    @pytest.mark.parametrize("route", MUTATION_ROUTES)
    def test_mutation_route_is_unavailable_without_server_config(self, route, unconfigured):
        with TestClient(app) as client:
            response = client.post(route, json={})
        assert response.status_code == 503
        assert response.json()["detail"]["error"]["code"] == "ADMIN_PROTECTION_UNAVAILABLE"

    @pytest.mark.parametrize("route", MUTATION_ROUTES)
    def test_mutation_route_rejects_a_missing_token(self, route, configured):
        with TestClient(app) as client:
            response = client.post(route, json={})
        assert response.status_code == 401

    @pytest.mark.parametrize("route", MUTATION_ROUTES)
    def test_mutation_route_rejects_a_wrong_token(self, route, configured):
        with TestClient(app) as client:
            response = client.post(
                route, json={}, headers={ADMIN_TOKEN_HEADER: "wrong-token-value-long-enough"}
            )
        assert response.status_code == 401

    @pytest.mark.parametrize("route", MUTATION_ROUTES)
    def test_valid_token_clears_the_guard_for_every_route(self, route, configured):
        """Every guarded route declares the header and resolves the shared guard.

        Asserted through the OpenAPI schema and the dependency rather than by
        issuing the request: these handlers perform real ingestion, graph
        builds, and index rebuilds, which this offline suite must never trigger.
        """
        parameters = app.openapi()["paths"][route]["post"].get("parameters", [])
        assert any(p.get("name") == ADMIN_TOKEN_HEADER for p in parameters), route
        assert verify_admin_token(VALID_TOKEN) is None

    @pytest.mark.parametrize("route", READ_ONLY_ROUTES)
    def test_read_only_routes_are_unaffected(self, route, unconfigured):
        with TestClient(app) as client:
            response = client.get(route)
        assert response.status_code == 200

    def test_token_is_not_accepted_via_query_string(self, configured):
        """A query-string token would leak into access and proxy logs."""
        with TestClient(app) as client:
            response = client.post(f"/v1/graph/build?x_admin_token={VALID_TOKEN}", json={})
        assert response.status_code == 401


# =============================================================================
# Secret handling
# =============================================================================


class TestSecretHandling:
    def test_rejection_never_reveals_the_expected_token(self, configured):
        with pytest.raises(Exception) as exc:
            verify_admin_token("wrong-token-value-long-enough")
        assert VALID_TOKEN not in json.dumps(exc.value.detail)

    def test_unavailable_response_never_reveals_configuration(self, unconfigured):
        with pytest.raises(Exception) as exc:
            verify_admin_token(None)
        rendered = json.dumps(exc.value.detail).lower()
        assert "token" not in rendered.replace("administrator", "")
        assert "phase8_admin_token" not in rendered

    def test_token_is_never_logged(self, configured, caplog):
        import logging

        caplog.set_level(logging.DEBUG)
        with pytest.raises(Exception):
            verify_admin_token("wrong-token-value-long-enough")
        assert VALID_TOKEN not in caplog.text
        assert "wrong-token-value-long-enough" not in caplog.text

    def test_openapi_does_not_leak_a_token_value(self):
        schema = json.dumps(app.openapi())
        assert VALID_TOKEN not in schema
        assert "ADMIN_API_TOKEN" not in schema

    def test_openapi_declares_the_header_on_mutation_routes(self):
        paths = app.openapi()["paths"]
        for route in MUTATION_ROUTES:
            parameters = paths[route]["post"].get("parameters", [])
            assert any(p.get("name") == ADMIN_TOKEN_HEADER for p in parameters), route

    def test_openapi_does_not_declare_the_header_on_read_only_routes(self):
        schema = app.openapi()["paths"]["/v1/ingestion/source-runs"]["get"]
        parameters = schema.get("parameters", [])
        assert not any(p.get("name") == ADMIN_TOKEN_HEADER for p in parameters)

    def test_comparison_is_constant_time(self):
        source = inspect.getsource(security_module)
        assert "hmac.compare_digest" in source
        assert "expected ==" not in source

    def test_settings_holds_the_token_as_a_secret(self):
        settings = _settings(ADMIN_API_TOKEN=SecretStr(VALID_TOKEN))
        assert VALID_TOKEN not in repr(settings)
        assert VALID_TOKEN not in str(settings.ADMIN_API_TOKEN)


# =============================================================================
# Route classification
# =============================================================================


class TestRouteClassification:
    def test_conversation_routes_are_not_admin_gated(self):
        """Conversation access is scoped by an unguessable id, not an admin role."""
        paths = app.openapi()["paths"]
        for path, method in (
            ("/v1/conversations", "post"),
            ("/v1/conversations/{conversation_id}", "delete"),
            ("/v1/conversations/{conversation_id}/messages", "post"),
        ):
            parameters = paths[path][method].get("parameters", [])
            assert not any(p.get("name") == ADMIN_TOKEN_HEADER for p in parameters), path

    def test_every_guarded_route_uses_the_shared_dependency(self):
        from app.api.v1.endpoints import graph, graphrag, ingestion

        for module in (graph, graphrag, ingestion):
            source = inspect.getsource(module)
            assert "verify_admin_token" in source

    def test_no_module_reimplements_token_comparison(self):
        from app.api.v1.endpoints import graph, graphrag, ingestion

        for module in (graph, graphrag, ingestion):
            source = inspect.getsource(module)
            assert "compare_digest" not in source
            assert "ADMIN_API_TOKEN" not in source
