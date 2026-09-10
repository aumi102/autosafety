"""
Tests for answer contract model.
"""

from app.services.answer_contract import (
    Answer,
    AnswerResponse,
    AnswerSection,
    CitationItem,
    Confidence,
    Evidence,
    GraphPath,
    SqlResult,
    make_safety_response,
    make_stub_response,
)


class TestCitationItem:
    def test_to_dict(self):
        cit = CitationItem(
            source_type="recall",
            source_id="abc-123",
            source_key="22V176000",
            field_name="remedy",
            text_span="Free repair at dealer",
            confidence=0.9,
        )
        d = cit.to_dict()
        assert d["source_type"] == "recall"
        assert d["source_key"] == "22V176000"
        assert d["confidence"] == 0.9


class TestGraphPath:
    def test_to_dict(self):
        path = GraphPath(
            path_text="Ford F-150 2022 -> SERVICE BRAKES -> Recall 22V176000",
            relation_source="source_record",
            confidence=0.85,
        )
        d = path.to_dict()
        assert "Ford F-150" in d["path_text"]
        assert d["relation_source"] == "source_record"


class TestAnswerSection:
    def test_to_dict(self):
        section = AnswerSection(
            title="Summary",
            content="Top components with most complaints",
            type="text",
        )
        d = section.to_dict()
        assert d["title"] == "Summary"
        assert d["type"] == "text"


class TestSqlResult:
    def test_to_dict(self):
        sql = SqlResult(
            used=True,
            query="SELECT component_id, COUNT(*) FROM complaints GROUP BY component_id",
            columns=["component_id", "count"],
            rows=[{"component_id": "brakes", "count": 42}],
            row_count=1,
            execution_ms=150,
            validated=True,
        )
        d = sql.to_dict()
        assert d["used"] is True
        assert d["validated"] is True
        assert d["row_count"] == 1


class TestEvidence:
    def test_to_dict_empty(self):
        ev = Evidence()
        d = ev.to_dict()
        assert d["citations"] == []
        assert d["graph_paths"] == []

    def test_to_dict_with_items(self):
        ev = Evidence(
            citations=[
                CitationItem(
                    source_type="recall",
                    source_id="abc-123",
                    confidence=0.9,
                )
            ],
            graph_paths=[
                GraphPath(
                    path_text="Ford F-150 -> Recall 22V176000",
                    confidence=0.85,
                )
            ],
        )
        d = ev.to_dict()
        assert len(d["citations"]) == 1
        assert len(d["graph_paths"]) == 1


class TestConfidence:
    def test_to_dict(self):
        conf = Confidence(
            label="high",
            score=0.92,
            reasons=["Multiple citations", "Low complaint count"],
        )
        d = conf.to_dict()
        assert d["label"] == "high"
        assert d["score"] == 0.92
        assert len(d["reasons"]) == 2


class TestAnswerResponse:
    def test_minimal_response(self):
        """Test a minimal valid response object."""
        response = AnswerResponse(
            run_id="run-123",
            intent="safety",
            answer=Answer(summary="Safety response"),
            sql=SqlResult(used=False),
            evidence=Evidence(),
            warnings=["Complaint volume alone does not prove a safety defect."],
            confidence=Confidence(label="low", score=0.0, reasons=["stub"]),
        )
        d = response.to_dict()
        assert d["run_id"] == "run-123"
        assert d["intent"] == "safety"
        assert d["sql"]["used"] is False
        assert "Complaint volume" in d["warnings"][0]

    def test_hybrid_response(self):
        """Test a richer hybrid response with SQL and evidence."""
        response = AnswerResponse(
            run_id="run-456",
            intent="hybrid",
            answer=Answer(
                summary="Ford F-150 2022 has 142 brake complaints",
                sections=[
                    AnswerSection(
                        title="Complaint Summary",
                        content="142 complaints related to SERVICE BRAKES",
                        type="evidence_summary",
                    ),
                    AnswerSection(
                        title="Safety Caveat",
                        content="Complaint volume alone does not prove a safety defect.",
                        type="caveat",
                    ),
                ],
            ),
            sql=SqlResult(
                used=True,
                query="SELECT component_id, COUNT(*) FROM complaints WHERE vehicle_id = 'xxx' GROUP BY component_id",
                columns=["component_id", "count"],
                rows=[{"component_id": "SERVICE BRAKES", "count": 142}],
                row_count=1,
                execution_ms=85,
                validated=True,
            ),
            evidence=Evidence(
                citations=[
                    CitationItem(
                        source_type="recall",
                        source_id="recall-abc",
                        source_key="22V176000",
                        field_name="remedy",
                        text_span="Free repair at dealer",
                        confidence=0.9,
                    )
                ],
                graph_paths=[
                    GraphPath(
                        path_text="Ford F-150 2022 -> SERVICE BRAKES -> Recall 22V176000",
                        relation_source="normalized_join",
                        confidence=0.85,
                    )
                ],
            ),
            warnings=[
                "Complaint volume alone does not prove a safety defect or official causality."
            ],
            confidence=Confidence(
                label="medium",
                score=0.71,
                reasons=["Has citations", "Low complaint volume relative to sales"],
            ),
            tool_call_count=3,
            latency_ms=1200,
        )
        d = response.to_dict()
        assert d["intent"] == "hybrid"
        assert d["sql"]["used"] is True
        assert d["evidence"]["citations"][0]["source_key"] == "22V176000"
        assert d["evidence"]["graph_paths"][0]["relation_source"] == "normalized_join"
        assert d["confidence"]["label"] == "medium"
        assert d["debug"]["tool_call_count"] == 3

    def test_from_dict(self):
        """Test deserialization from a dict."""
        data = {
            "run_id": "run-789",
            "intent": "sql",
            "answer": {
                "summary": "5 recalls found",
                "sections": [{"title": "Recalls", "content": "5 recalls", "type": "table_summary"}],
            },
            "sql": {
                "used": True,
                "query": "SELECT * FROM recalls",
                "columns": ["id", "campaign_number"],
                "rows": [{"id": "1", "campaign_number": "22V176000"}],
                "row_count": 1,
                "execution_ms": 50,
                "validated": True,
            },
            "evidence": {"citations": [], "graph_paths": []},
            "warnings": [],
            "confidence": {"label": "high", "score": 0.95, "reasons": []},
            "debug": {"tool_call_count": 1, "latency_ms": 500},
        }
        response = AnswerResponse.from_dict(data)
        assert response.run_id == "run-789"
        assert response.intent == "sql"
        assert response.sql.row_count == 1


class TestFactoryFunctions:
    def test_make_safety_response(self):
        response = make_safety_response(
            summary="Query blocked for safety reasons.",
            warnings=["Only SELECT/WITH queries allowed."],
            run_id="safety-001",
        )
        assert response.intent == "safety"
        assert response.sql.used is False
        assert "blocked" in response.answer.summary

    def test_make_stub_response(self):
        response = make_stub_response(phase_note="NHTSA ingestion not ready")
        assert response.intent == "safety"
        assert "Phase 0" in response.answer.summary
        assert "Phase 1" in response.answer.sections[0].content
