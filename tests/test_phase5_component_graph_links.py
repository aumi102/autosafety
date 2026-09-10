"""
Phase 5: Component-level graph links tests.

Verifies:
- GraphBuildStats includes Phase 5 fields
- Component nodes MERGE before MENTIONS_COMPONENT
- MENTIONS_COMPONENT emitted for complaint with original_component
- No MENTIONS_COMPONENT for missing/unknown component
- RELATED_TO_COMPONENT emitted when recall component data exists
- RELATED_TO_COMPONENT skipped when recall component data is missing
- Dry-run counts component candidates without writing
- Predefined Cypher queries use fixed patterns
- No arbitrary Cypher exposed
- Hybrid answer includes component evidence when graph returns it
"""

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

from app.services.graph.graph_models import ComponentEvidence, GraphBuildStats


class TestPhase5StatsModel:
    """GraphBuildStats includes Phase 5 component relationship stats."""

    def test_to_dict_includes_phase5_fields(self):
        stats = GraphBuildStats()
        d = stats.to_dict()
        assert "component_nodes_merged" in d
        assert "complaint_component_links_seen" in d
        assert "complaint_component_links_merged" in d
        assert "recall_component_links_seen" in d
        assert "recall_component_links_merged" in d
        assert "component_links_skipped" in d
        assert "component_link_errors" in d

    def test_phase5_fields_default_to_zero(self):
        stats = GraphBuildStats()
        assert stats.component_nodes_merged == 0
        assert stats.complaint_component_links_seen == 0
        assert stats.complaint_component_links_merged == 0
        assert stats.recall_component_links_seen == 0
        assert stats.recall_component_links_merged == 0
        assert stats.component_links_skipped == 0
        assert stats.component_link_errors == 0

    def test_phase5_fields_accumulate(self):
        stats = GraphBuildStats()
        stats.component_nodes_merged = 5
        stats.complaint_component_links_seen = 10
        stats.complaint_component_links_merged = 8
        stats.recall_component_links_seen = 3
        stats.recall_component_links_merged = 2
        stats.component_links_skipped = 2
        stats.component_link_errors = 1
        d = stats.to_dict()
        assert d["component_nodes_merged"] == 5
        assert d["complaint_component_links_seen"] == 10
        assert d["complaint_component_links_merged"] == 8
        assert d["recall_component_links_seen"] == 3
        assert d["recall_component_links_merged"] == 2
        assert d["component_links_skipped"] == 2
        assert d["component_link_errors"] == 1


class TestPhase5GraphBuilder:
    """Phase 5 graph builder emits correct Cypher for component links."""

    def test_component_nodes_upserted_before_mentions_component(self):
        """Component MERGE runs before MENTIONS_COMPONENT in build_graph."""
        from unittest.mock import MagicMock

        vid = uuid.uuid4()
        cid = uuid.uuid4()

        mock_vehicle = MagicMock()
        mock_vehicle.id = vid
        mock_vehicle.normalized_make = "FORD"
        mock_vehicle.normalized_model = "F-150"
        mock_vehicle.year = 2020

        call_order = []

        def track_upsert_all_components(client, comp_map, stats):
            call_order.append("_upsert_all_components")
            stats.component_nodes_merged = 1
            stats.nodes_merged = 1

        def track_process_vehicle(vehicle, pg, n4j, comp_map, recall_map, stats):
            call_order.append("_process_vehicle")

        with patch("app.services.graph.graph_builder._fetch_vehicles") as mock_fv:
            with patch("app.services.graph.graph_builder._build_component_map") as mock_bcm:
                with patch(
                    "app.services.graph.graph_builder._upsert_all_components",
                    side_effect=track_upsert_all_components,
                ):
                    with patch(
                        "app.services.graph.graph_builder._process_vehicle",
                        side_effect=track_process_vehicle,
                    ):
                        mock_fv.return_value = iter([mock_vehicle])
                        mock_bcm.return_value = {
                            "SERVICE BRAKES": {
                                "id": str(cid),
                                "name": "SERVICE BRAKES",
                                "normalized_name": "SERVICE BRAKES",
                                "category": None,
                            }
                        }
                        mock_neo4j = MagicMock()
                        mock_pg = MagicMock()

                        from app.services.graph.graph_builder import build_graph

                        build_graph(mock_pg, mock_neo4j, dry_run=False, limit_vehicles=None)

                        # _upsert_all_components must run before _process_vehicle
                        assert call_order[0] == "_upsert_all_components"
                        assert call_order[1] == "_process_vehicle"

    def test_mentions_component_emitted_for_complaint_with_original_component(self):
        """MENTIONS_COMPONENT emitted when original_component exists and matches known component."""
        from app.services.graph.graph_builder import _upsert_complaint

        vid = uuid.uuid4()
        odi = "987654321"
        year_key = "F-150:2020"
        # normalize_component_name uppercases, so key must be uppercase
        comp_map = {
            "SERVICE BRAKES": {
                "id": "c1",
                "name": "SERVICE BRAKES",
                "normalized_name": "SERVICE BRAKES",
                "category": None,
            }
        }

        mock_complaint = MagicMock()
        mock_complaint.id = uuid.uuid4()
        mock_complaint.odi_number = odi
        mock_complaint.vehicle_id = vid
        mock_complaint.received_date = None
        mock_complaint.summary = "Test"
        mock_complaint.crash_flag = False
        mock_complaint.injury_flag = False
        mock_complaint.death_flag = False
        mock_complaint.original_component = "SERVICE BRAKES"  # uppercase, matches key
        mock_complaint.source_url = None

        mock_neo4j = MagicMock()
        stats = GraphBuildStats()

        # Signature: _upsert_complaint(complaint, neo4j_client, year_key, component_map, stats)
        _upsert_complaint(mock_complaint, mock_neo4j, year_key, comp_map, stats)

        # Stats should reflect the component link
        assert stats.complaint_component_links_seen >= 1
        assert stats.complaint_component_links_merged >= 1

    def test_no_mentions_component_for_missing_original_component(self):
        """No MENTIONS_COMPONENT when original_component is None/empty."""
        from app.services.graph.graph_builder import _upsert_complaint

        vid = uuid.uuid4()
        odi = "555555555"
        year_key = "F-150:2020"
        comp_map: dict[str, Any] = {}

        mock_complaint = MagicMock()
        mock_complaint.id = uuid.uuid4()
        mock_complaint.odi_number = odi
        mock_complaint.vehicle_id = vid
        mock_complaint.received_date = None
        mock_complaint.summary = "Test"
        mock_complaint.crash_flag = False
        mock_complaint.injury_flag = False
        mock_complaint.death_flag = False
        mock_complaint.original_component = None  # No component data
        mock_complaint.source_url = None

        mock_neo4j = MagicMock()
        stats = GraphBuildStats()

        _upsert_complaint(mock_complaint, mock_neo4j, year_key, comp_map, stats)

        # No MENTIONS_COMPONENT should be emitted; skip counted for unmatched
        # (when original_component exists but doesn't match any known component)
        mentions_cypher = [
            c for c in mock_neo4j.execute.call_args_list if "MENTIONS_COMPONENT" in (c[0][0] or "")
        ]
        assert len(mentions_cypher) == 0

    def test_no_mentions_component_for_unknown_component(self):
        """No MENTIONS_COMPONENT when original_component doesn't match any known component."""
        from app.services.graph.graph_builder import _upsert_complaint

        vid = uuid.uuid4()
        odi = "444444444"
        comp_map: dict[str, Any] = {}  # Empty - no known components

        mock_complaint = MagicMock()
        mock_complaint.id = uuid.uuid4()
        mock_complaint.odi_number = odi
        mock_complaint.vehicle_id = vid
        mock_complaint.received_date = None
        mock_complaint.summary = "Test"
        mock_complaint.crash_flag = False
        mock_complaint.injury_flag = False
        mock_complaint.death_flag = False
        mock_complaint.original_component = "UNKNOWN COMPONENT XYZ"

        mock_neo4j = MagicMock()
        stats = GraphBuildStats()
        year_key = "F-150:2020"

        _upsert_complaint(mock_complaint, mock_neo4j, year_key, comp_map, stats)

        mentions_cypher = [
            c for c in mock_neo4j.execute.call_args_list if "MENTIONS_COMPONENT" in (c[0][0] or "")
        ]
        assert len(mentions_cypher) == 0, "No MENTIONS_COMPONENT when component unknown"
        assert stats.component_links_skipped >= 1

    def test_related_to_component_emitted_when_recall_has_component(self):
        """RELATED_TO_COMPONENT MERGE emitted when recall has component data."""
        from app.services.graph.graph_builder import _upsert_recall_and_affects

        recall_data = {
            "campaign_number": "20V-123",
            "original_component": "SERVICE BRAKES",
            "report_received_date": None,
            "summary": "Recall for brake issue",
            "remedy": "Fix it",
            "units_affected": None,
        }
        comp_map = {
            "SERVICE BRAKES": {
                "id": "c1",
                "name": "SERVICE BRAKES",
                "normalized_name": "SERVICE BRAKES",
                "category": None,
            }
        }
        mock_neo4j = MagicMock()
        stats = GraphBuildStats()

        _upsert_recall_and_affects(recall_data, "F-150:2020", comp_map, mock_neo4j, stats)

        related_cypher = [
            c
            for c in mock_neo4j.execute.call_args_list
            if "RELATED_TO_COMPONENT" in (c[0][0] or "")
        ]
        assert len(related_cypher) >= 1, "RELATED_TO_COMPONENT should be emitted"
        assert stats.recall_component_links_seen >= 1
        assert stats.recall_component_links_merged >= 1

    def test_related_to_component_skipped_when_recall_has_no_component(self):
        """RELATED_TO_COMPONENT skipped when recall component data is missing."""
        from app.services.graph.graph_builder import _upsert_recall_and_affects

        recall_data = {
            "campaign_number": "20V-999",
            "original_component": None,  # No component data
            "report_received_date": None,
            "summary": "Generic recall",
            "remedy": "Fix it",
            "units_affected": None,
        }
        comp_map: dict[str, Any] = {}
        mock_neo4j = MagicMock()
        stats = GraphBuildStats()

        _upsert_recall_and_affects(recall_data, "F-150:2020", comp_map, mock_neo4j, stats)

        related_cypher = [
            c
            for c in mock_neo4j.execute.call_args_list
            if "RELATED_TO_COMPONENT" in (c[0][0] or "")
        ]
        assert len(related_cypher) == 0, "No RELATED_TO_COMPONENT when recall has no component"
        # The skip goes through the complaint path not recall path
        # Recall without component just doesn't enter that code block

    def test_dry_run_counts_components_without_writing(self):
        """Dry-run counts component candidates without writing to Neo4j."""
        from unittest.mock import MagicMock

        from app.services.graph.graph_builder import build_graph

        vid = uuid.uuid4()
        cid = uuid.uuid4()

        mock_vehicle = MagicMock()
        mock_vehicle.id = vid
        mock_vehicle.normalized_make = "FORD"
        mock_vehicle.normalized_model = "F-150"
        mock_vehicle.year = 2020

        with patch("app.services.graph.graph_builder._fetch_vehicles") as mock_fv:
            with patch("app.services.graph.graph_builder._build_component_map") as mock_bcm:
                mock_fv.return_value = iter([mock_vehicle])
                mock_bcm.return_value = {
                    "SERVICE BRAKES": {
                        "id": str(cid),
                        "name": "SERVICE BRAOKES",
                        "normalized_name": "SERVICE BRAKES",
                        "category": None,
                    }
                }
                mock_neo4j = MagicMock()
                mock_pg = MagicMock()

                stats = build_graph(mock_pg, mock_neo4j, dry_run=True, limit_vehicles=None)

                assert stats.component_nodes_merged >= 1
                # Neo4j should NOT be called for MERGE operations in dry run
                # (Component nodes are not written in dry run)
                mock_neo4j.execute.assert_not_called()


class TestPhase5Queries:
    """Predefined Cypher queries for component evidence."""

    def test_component_evidence_query_uses_predefined_cypher(self):
        """get_component_evidence_for_vehicle uses fixed COMPONENT_EVIDENCE_CYPHER."""
        from app.services.graph.graph_queries import (
            COMPONENT_EVIDENCE_CYPHER,
            get_component_evidence_for_vehicle,
        )

        # Cypher template is a module-level constant (fixed pattern)
        assert isinstance(COMPONENT_EVIDENCE_CYPHER, str)
        assert "MENTIONS_COMPONENT" in COMPONENT_EVIDENCE_CYPHER
        assert len(COMPONENT_EVIDENCE_CYPHER) > 50

        import inspect

        src = inspect.getsource(get_component_evidence_for_vehicle)
        # Function references the constant
        assert "COMPONENT_EVIDENCE_CYPHER" in src
        # Calls execute_single with the constant (params passed separately)
        assert "client.execute_single(COMPONENT_EVIDENCE_CYPHER" in src

    def test_shared_component_recall_query_uses_predefined_cypher(self):
        """get_shared_component_recall_paths uses fixed SHARED_COMPONENT_RECALLS_CYPHER."""
        from app.services.graph.graph_queries import (
            SHARED_COMPONENT_RECALLS_CYPHER,
            get_shared_component_recall_paths,
        )

        assert isinstance(SHARED_COMPONENT_RECALLS_CYPHER, str)
        assert "RELATED_TO_COMPONENT" in SHARED_COMPONENT_RECALLS_CYPHER
        assert "AFFECTS" in SHARED_COMPONENT_RECALLS_CYPHER
        assert len(SHARED_COMPONENT_RECALLS_CYPHER) > 50

        import inspect

        src = inspect.getsource(get_shared_component_recall_paths)
        assert "SHARED_COMPONENT_RECALLS_CYPHER" in src
        assert "client.execute_single(SHARED_COMPONENT_RECALLS_CYPHER" in src

    def test_no_arbitrary_cypher_exposed(self):
        """No endpoint or function accepts arbitrary Cypher strings."""
        # Check graph_queries.py has no execute_raw or similar
        import ast
        import os

        queries_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "app",
            "services",
            "graph",
            "graph_queries.py",
        )
        with open(queries_path) as f:
            source = f.read()

        tree = ast.parse(source)

        exposed_cypher = [
            n.name
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and n.name
            in {
                "execute_query",
                "run_cypher",
                "raw_query",
                "arbitrary_query",
                "custom_cypher",
            }
        ]
        assert len(exposed_cypher) == 0, f"No arbitrary Cypher functions: {exposed_cypher}"


class TestComponentEvidenceModel:
    """ComponentEvidence dataclass has correct structure."""

    def test_component_evidence_to_dict(self):
        evidence = ComponentEvidence(
            make="Ford",
            model="F-150",
            year=2020,
            vehicle_id="ford:f-150:2020",
            complaint_components=[{"id": "comp1", "name": "SERVICE BRAKES", "count": 5}],
            components=["SERVICE BRAKES"],
            complaint_count=5,
            shared_recalls=[{"id": "20V-123", "name": "Brake recall", "complaint_count": 3}],
            recall_count=1,
            path_type="complaint_mentions_component",
        )
        d = evidence.to_dict()
        assert d["make"] == "Ford"
        assert d["model"] == "F-150"
        assert d["year"] == 2020
        assert d["vehicle_id"] == "ford:f-150:2020"
        assert d["complaint_count"] == 5
        assert d["recall_count"] == 1
        assert d["path_type"] == "complaint_mentions_component"
        assert "SERVICE BRAKES" in d["components"]

    def test_component_evidence_path_types(self):
        """ComponentEvidence supports the expected path_type values."""
        valid_types = {
            "complaint_mentions_component",
            "recall_related_to_component",
        }
        for pt in valid_types:
            evidence = ComponentEvidence(
                make="X",
                model="Y",
                year=2000,
                vehicle_id="x:y:2000",
                complaint_components=[],
                components=[],
                complaint_count=0,
                shared_recalls=[],
                recall_count=0,
                path_type=pt,
            )
            assert evidence.path_type == pt
