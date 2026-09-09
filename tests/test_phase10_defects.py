"""Phase 10 regression tests for defects the repaired lint gate exposed.

Offline and deterministic. Each test here corresponds to a real defect that had
been sitting in the codebase undetected because `make lint` pointed at a
directory that does not exist.
"""

from __future__ import annotations

import inspect

import pytest
from app.services.answer_synthesis.tools import bundle_builder
from app.services.graph import graph_service
from app.services.sql_analytics.question_parser import MODEL_CANONICAL

# =============================================================================
# graph_service.get_vehicle_neighborhood shadowed its own query-layer import
# =============================================================================


class TestVehicleNeighborhoodShadowing:
    """`ruff F811` caught a redefinition that silently disabled a retrieval path.

    `graph_service` defined a public `get_vehicle_neighborhood(vehicle_id)` while
    also importing `graph_queries.get_vehicle_neighborhood(client, make, ...)`
    under the same name. The module-level `def` won, so the internal call
    invoked *itself* with the query-layer signature. That raised `TypeError`,
    the broad `except Exception` swallowed it, and the function returned `None`
    for every vehicle — the graph neighborhood lookup was dead code in
    production while still appearing to "work".
    """

    def test_public_function_takes_a_single_vehicle_id(self):
        signature = inspect.signature(graph_service.get_vehicle_neighborhood)
        assert list(signature.parameters) == ["vehicle_id"]

    def test_query_layer_function_is_imported_under_a_distinct_name(self):
        """The alias is what stops the redefinition from coming back."""
        aliased = graph_service.get_neighborhood_for_vehicle
        assert list(inspect.signature(aliased).parameters)[:4] == [
            "client",
            "make",
            "model",
            "year",
        ]
        assert aliased is not graph_service.get_vehicle_neighborhood

    def test_internal_call_targets_the_query_layer_not_itself(self):
        source = inspect.getsource(graph_service.get_vehicle_neighborhood)
        assert "get_neighborhood_for_vehicle(" in source
        # A self-call with the query-layer signature is the bug being pinned.
        assert "return get_vehicle_neighborhood(" not in source

    def test_calling_with_the_query_signature_would_still_be_a_type_error(self):
        """Proves the two signatures really are incompatible, so the bug was real."""
        with pytest.raises(TypeError):
            graph_service.get_vehicle_neighborhood(
                object(), make="Ford", model="F-150", year=2019, vehicle_id="x"
            )


# =============================================================================
# Evidence metadata redaction denylist
# =============================================================================


class TestForbiddenKeyDenylist:
    """`ruff B033`/`F601` flagged a duplicated `"api_key"` entry.

    A set silently absorbs the duplicate, so nothing was broken — but the
    denylist matches by substring against a lowercased key, and `"api_key"`
    is not a substring of `"apikey"`. A metadata key spelled `apiKey` therefore
    passed straight through. The duplicate is removed and the gap closed.
    """

    def test_denylist_has_no_duplicate_entries(self):
        source = inspect.getsource(bundle_builder)
        block = source.split("_FORBIDDEN_KEYS: set[str] = {")[1].split("}")[0]
        entries = [line.strip().strip(",").strip('"') for line in block.splitlines()]
        entries = [entry for entry in entries if entry]
        assert len(entries) == len(set(entries)), "duplicate entry in the denylist"

    @pytest.mark.parametrize(
        "key",
        ["apiKey", "APIKEY", "apikey", "api_key", "Authorization", "neo4j_password"],
    )
    def test_secret_shaped_keys_are_stripped_from_evidence_metadata(self, key):
        sanitized = bundle_builder._sanitize({key: "leaked-value", "make": "Ford"})
        assert key not in sanitized
        assert "leaked-value" not in str(sanitized)
        assert sanitized["make"] == "Ford"

    def test_ordinary_metadata_survives_sanitization(self):
        sanitized = bundle_builder._sanitize({"make": "Ford", "model_year": 2019})
        assert sanitized == {"make": "Ford", "model_year": 2019}


# =============================================================================
# Duplicated dictionary literals
# =============================================================================


class TestModelCanonicalLiterals:
    """`ruff F601` flagged repeated keys. Values matched, so behavior is unchanged."""

    @pytest.mark.parametrize("variant", ["f-150", "f150", "f 150"])
    def test_every_f150_variant_still_maps_to_the_canonical_name(self, variant):
        assert MODEL_CANONICAL[variant] == "F-150"

    def test_other_models_are_untouched(self):
        assert MODEL_CANONICAL["accord"] == "Accord"
        assert MODEL_CANONICAL["camry"] == "Camry"
