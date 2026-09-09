"""Tests for ingestion service — mock-based."""

from app.services.ingestion.normalization import (
    normalize_component_name,
    normalize_make,
    normalize_model,
)


class TestNormalizeComponentName:
    def test_known_component(self):
        assert normalize_component_name("SERVICE BRAKES") == "SERVICE BRAKES"
        assert normalize_component_name("service brakes") == "SERVICE BRAKES"

    def test_partial_match(self):
        # "AIR BAGS" substring in longer name
        result = normalize_component_name("AIR BAGS - PASSENGER SIDE")
        assert result is not None

    def test_unknown_component(self):
        result = normalize_component_name("MY CUSTOM COMPONENT XYZ")
        assert result is not None
        assert result == "MY CUSTOM COMPONENT XYZ"

    def test_empty(self):
        assert normalize_component_name("") is None
        assert normalize_component_name(None) is None
        assert normalize_component_name("   ") is None


class TestNormalizeMake:
    def test_uppercase(self):
        assert normalize_make("ford") == "FORD"
        assert normalize_make("Ford") == "FORD"

    def test_whitespace(self):
        assert normalize_make("  Ford  ") == "FORD"

    def test_empty(self):
        assert normalize_make("") == ""
        assert normalize_make(None) == ""


class TestNormalizeModel:
    def test_uppercase(self):
        assert normalize_model("f-150") == "F-150"
        assert normalize_model("F-150") == "F-150"

    def test_whitespace(self):
        assert normalize_model("  Camry  ") == "CAMRY"
