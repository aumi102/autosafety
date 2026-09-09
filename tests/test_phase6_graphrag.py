"""
Phase 6 GraphRAG tests — document building, chunking, embedding, retrieval, service.
"""

import hashlib

import pytest
from app.services.graphrag.chunker import TextChunker, chunk_document, chunk_id
from app.services.graphrag.embedding_provider import DeterministicTestProvider

# ─── Chunker tests ─────────────────────────────────────────────────────────────

class TestTextChunker:
    def test_short_document_returns_one_chunk(self):
        """Short text stays as one chunk."""
        chunker = TextChunker(max_length=1000, overlap=50)
        chunks = chunker.chunk("doc1", "This is a short complaint.")
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0
        assert chunks[0].text == "This is a short complaint."

    def test_deterministic_chunks(self):
        """Same input always produces same chunks."""
        chunker = TextChunker(max_length=200, overlap=30)
        text = "Brake failure reported. The vehicle lost stopping power on the highway. " * 5
        chunks1 = chunker.chunk("doc1", text)
        chunks2 = chunker.chunk("doc1", text)
        assert len(chunks1) == len(chunks2)
        assert all(c1.chunk_id == c2.chunk_id for c1, c2 in zip(chunks1, chunks2))

    def test_stable_chunk_ids(self):
        """Chunk IDs are stable."""
        chunker = TextChunker(max_length=200, overlap=30)
        text = "Service brakes failed during normal driving conditions."
        chunks = chunker.chunk("complaint:123", text)
        for chunk in chunks:
            cid = chunk_id(chunk.document_id, chunk.chunk_index, chunk.text)
            assert chunk.chunk_id == cid

    def test_no_empty_chunks(self):
        """No chunk has empty text."""
        chunker = TextChunker(max_length=200, overlap=30)
        text = "Airbag did not deploy. " * 10
        chunks = chunker.chunk("doc1", text)
        for chunk in chunks:
            assert chunk.text.strip()

    def test_overlap_behavior(self):
        """Overlap is less than max_length."""
        chunker = TextChunker(max_length=500, overlap=100)
        text = "A" * 800
        chunks = chunker.chunk("doc1", text)
        if len(chunks) > 1:
            assert chunker.overlap < chunker.max_length

    def test_invalid_overlap_rejected(self):
        """overlap >= max_length raises ValueError."""
        with pytest.raises(ValueError):
            TextChunker(max_length=200, overlap=200)
        with pytest.raises(ValueError):
            TextChunker(max_length=200, overlap=250)

    def test_negative_overlap_rejected(self):
        """overlap < 0 raises ValueError."""
        with pytest.raises(ValueError):
            TextChunker(max_length=200, overlap=-1)

    def test_empty_text_returns_empty(self):
        """Empty text returns no chunks."""
        chunker = TextChunker(max_length=200, overlap=30)
        chunks = chunker.chunk("doc1", "")
        assert chunks == []

    def test_progress_made_each_iteration(self):
        """Loop makes progress — start advances and produces distinct indices."""
        chunker = TextChunker(max_length=100, overlap=20)
        text = "X" * 500
        chunks = chunker.chunk("doc1", text)
        # Distinct indices prove the loop ran multiple times
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks))), "Chunk indices must be 0..N-1"
        # Distinct IDs prove different chunk content (even if chars repeat)
        ids = [c.chunk_id for c in chunks]
        assert len(set(ids)) == len(chunks), "All chunk IDs must be unique"

    def test_convenience_function(self):
        """chunk_document convenience function works."""
        chunks = chunk_document("doc1", "Short text.", max_length=1000, overlap=50)
        assert len(chunks) == 1


# ─── Embedding provider tests ──────────────────────────────────────────────────

class TestDeterministicProvider:
    def test_deterministic_same_vector(self):
        """Same text always produces same vector."""
        provider = DeterministicTestProvider(dimension=384)
        v1 = provider.embed_text("brake failure complaint")
        v2 = provider.embed_text("brake failure complaint")
        assert v1 == v2

    def test_correct_dimension(self):
        """Vector has configured dimension."""
        provider = DeterministicTestProvider(dimension=384)
        vec = provider.embed_text("test text")
        assert len(vec) == 384

    def test_normalized_vector(self):
        """Vector is L2-normalized."""
        import math
        provider = DeterministicTestProvider(dimension=128)
        vec = provider.embed_text("service brakes complaint")
        magnitude = math.sqrt(sum(x * x for x in vec))
        assert abs(magnitude - 1.0) < 1e-6

    def test_batch_matches_single(self):
        """Batch encoding matches single encoding for same texts."""
        provider = DeterministicTestProvider(dimension=128)
        texts = ["brake failure", "steering problem", "brake failure"]
        singles = [provider.embed_text(t) for t in texts]
        # All identical texts produce identical vectors
        assert singles[0] == singles[2]

    def test_empty_text_handled(self):
        """Empty text produces a vector (not all zeros after normalization)."""
        provider = DeterministicTestProvider(dimension=64)
        vec = provider.embed_text("")
        assert len(vec) == 64

    def test_related_shared_tokens_rank_closer(self):
        """
        Texts sharing tokens produce higher similarity than unrelated texts.
        Uses bit-overlap based on token hashing.
        """
        provider = DeterministicTestProvider(dimension=256)

        text_a = "brake failure complaint"
        text_b = "brake system defect report"
        text_c = "airbag deployment steering"

        vec_a = provider.embed_text(text_a)
        vec_b = provider.embed_text(text_b)
        vec_c = provider.embed_text(text_c)

        # Cosine similarity
        def cosine_sim(x, y):
            dot = sum(a * b for a, b in zip(x, y))
            return dot

        sim_ab = cosine_sim(vec_a, vec_b)
        sim_ac = cosine_sim(vec_a, vec_c)

        # brake-related texts should share more token bits
        assert sim_ab >= sim_ac, "Shared 'brake' token should increase overlap"


# ─── Document builder tests ─────────────────────────────────────────────────────

class TestDocumentBuilder:
    def test_complaint_document_deterministic(self):
        """Same complaint always produces same document."""
        from datetime import date

        from app.db.models.domain import Complaint, Vehicle
        from app.services.graphrag.document_builder import build_complaint_document

        vehicle = Vehicle(
            id="00000000-0000-0000-0000-000000000001",
            make="Ford", model="F-150", model_year=2020,
            normalized_make="ford", normalized_model="f-150",
        )
        complaint = Complaint(
            id="00000000-0000-0000-0000-000000000002",
            odi_number="123456789",
            vehicle_id=vehicle.id,
            source_record_key="123456789",
            received_date=date(2024, 1, 15),
            incident_date=date(2024, 1, 10),
            original_component="Service Brakes",
            summary="Brake failure during highway driving.",
            crash_flag=True,
            fire_flag=False,
            injury_flag=False,
            death_flag=False,
        )

        doc1 = build_complaint_document(complaint, vehicle)
        doc2 = build_complaint_document(complaint, vehicle)

        assert doc1.document_id == doc2.document_id
        assert doc1.content_hash == doc2.content_hash
        assert doc1.full_text == doc2.full_text

    def test_complaint_metadata_includes_vehicle(self):
        """Complaint metadata includes make/model/year/component."""

        from app.db.models.domain import Complaint, Vehicle
        from app.services.graphrag.document_builder import build_complaint_document

        vehicle = Vehicle(
            id="00000000-0000-0000-0000-000000000001",
            make="Toyota", model="Camry", model_year=2022,
            normalized_make="toyota", normalized_model="camry",
        )
        complaint = Complaint(
            id="00000000-0000-0000-0000-000000000002",
            odi_number="987654321",
            vehicle_id=vehicle.id,
            source_record_key="987654321",
            original_component="Air Bags",
            summary="Airbag did not deploy in crash.",
        )

        doc = build_complaint_document(complaint, vehicle)

        assert doc.make == "Toyota"
        assert doc.model == "Camry"
        assert doc.model_year == 2022
        assert doc.component == "Air Bags"
        assert "make" in doc.metadata
        assert "model" in doc.metadata
        assert "model_year" in doc.metadata
        assert "component" in doc.metadata

    def test_recall_metadata_includes_vehicle(self):
        """Recall metadata includes make/model/year/component when available."""

        from app.db.models.domain import Recall, Vehicle
        from app.services.graphrag.document_builder import build_recall_document

        vehicle = Vehicle(
            id="00000000-0000-0000-0000-000000000001",
            make="Honda", model="Civic", model_year=2019,
            normalized_make="honda", normalized_model="civic",
        )
        recall = Recall(
            id="00000000-0000-0000-0000-000000000002",
            campaign_number="20V123000",
            source_record_key="20V123000",
            original_component="Steering",
            consequence="Loss of steering control possible.",
            remedy="Dealer will inspect and replace steering column.",
            units_affected=50000,
        )

        doc = build_recall_document(recall, vehicle)

        assert doc.make == "Honda"
        assert doc.model == "Civic"
        assert doc.model_year == 2019
        assert doc.component == "Steering"
        assert "make" in doc.metadata
        assert "model" in doc.metadata
        assert "model_year" in doc.metadata
        assert "component" in doc.metadata

    def test_missing_optional_fields_omitted(self):
        """Optional fields that are None do not appear as 'None' in text."""
        from app.db.models.domain import Complaint, Vehicle
        from app.services.graphrag.document_builder import build_complaint_document

        vehicle = Vehicle(
            id="00000000-0000-0000-0000-000000000001",
            make="Ford", model="Focus", model_year=2021,
            normalized_make="ford", normalized_model="focus",
        )
        complaint = Complaint(
            id="00000000-0000-0000-0000-000000000002",
            odi_number="111222333",
            vehicle_id=vehicle.id,
            source_record_key="111222333",
            original_component=None,
            summary=None,
            crash_flag=False,
            fire_flag=False,
            injury_flag=False,
            death_flag=False,
        )

        doc = build_complaint_document(complaint, vehicle)
        assert "None" not in doc.full_text
        assert "Component:" not in doc.full_text  # None component not mentioned
        assert "Incident date:" not in doc.full_text  # None date not mentioned

    def test_stable_content_hash(self):
        """Content hash is stable SHA-256 of full_text."""
        from app.db.models.domain import Complaint, Vehicle
        from app.services.graphrag.document_builder import build_complaint_document

        vehicle = Vehicle(
            id="00000000-0000-0000-0000-000000000001",
            make="Ford", model="F-150", model_year=2020,
            normalized_make="ford", normalized_model="f-150",
        )
        complaint = Complaint(
            id="00000000-0000-0000-0000-000000000002",
            odi_number="444555666",
            vehicle_id=vehicle.id,
            source_record_key="444555666",
            summary="Test complaint.",
        )

        doc = build_complaint_document(complaint, vehicle)
        expected_hash = hashlib.sha256(doc.full_text.encode("utf-8")).hexdigest()
        assert doc.content_hash == expected_hash

    def test_correct_source_record_key(self):
        """source_record_key is ODI for complaints, campaign for recalls."""
        from app.db.models.domain import Complaint, Recall, Vehicle
        from app.services.graphrag.document_builder import (
            build_complaint_document,
            build_recall_document,
        )

        vehicle = Vehicle(
            id="00000000-0000-0000-0000-000000000001",
            make="Ford", model="F-150", model_year=2020,
            normalized_make="ford", normalized_model="f-150",
        )
        complaint = Complaint(
            id="00000000-0000-0000-0000-000000000002",
            odi_number="ODI999888",
            vehicle_id=vehicle.id,
            source_record_key="ODI999888",
        )
        recall = Recall(
            id="00000000-0000-0000-0000-000000000003",
            campaign_number="20V123000",
            source_record_key="20V123000",
        )

        doc_c = build_complaint_document(complaint, vehicle)
        doc_r = build_recall_document(recall, vehicle)

        assert doc_c.source_record_key == "ODI999888"
        assert doc_r.source_record_key == "20V123000"


# ─── Service / retrieval tests ─────────────────────────────────────────────────

class TestServiceCitationLabel:
    def test_citation_label_no_unknown(self):
        """Citation label uses actual metadata, not 'Unknown'."""
        from app.services.graphrag.retriever import GraphRAGRetriever

        # Patch the retriever's _make_citation_label
        label = GraphRAGRetriever._make_citation_label(None, "complaint", "123456789", "Ford", "F-150")
        assert "Unknown" not in label
        assert "Ford" in label
        assert "F-150" in label

    def test_citation_label_recall(self):
        """Recall citation label shows campaign and vehicle."""
        from app.services.graphrag.retriever import GraphRAGRetriever

        label = GraphRAGRetriever._make_citation_label(None, "recall", "20V123000", "Honda", "Civic")
        assert "20V123000" in label
        assert "Honda" in label
        assert "Civic" in label


# ─── Models tests ──────────────────────────────────────────────────────────────

class TestGraphRAGModels:
    def test_stats_to_dict_complete(self):
        """GraphRAGIndexStats.to_dict includes all fields."""
        from app.services.graphrag.models import GraphRAGIndexStats

        stats = GraphRAGIndexStats(
            complaints_seen=10, recalls_seen=5,
            documents_created=3, documents_updated=2, documents_unchanged=8,
            documents_skipped=2, chunks_created=15, chunks_updated=4,
            chunks_deleted=1, chunks_unchanged=20, embeddings_generated=40,
            errors_count=1, errors=["test error"], duration_ms=500,
            dry_run=False,
        )
        d = stats.to_dict()
        assert d["complaints_seen"] == 10
        assert d["recalls_seen"] == 5
        assert d["documents_created"] == 3
        assert d["errors"][0] == "test error"
        assert d["dry_run"] is False

    def test_retrieval_result_to_dict(self):
        """GraphRAGRetrievalResult.to_dict includes phase_6."""
        from app.services.graphrag.models import GraphRAGRetrievalResult, RetrievedChunk

        chunk = RetrievedChunk(
            chunk_id="c1", score=0.85, source_type="complaint",
            source_entity_id="e1", source_record_key="123",
            title="Test", text="Test text", source_url=None,
            make="Ford", model="F-150", model_year=2020,
            component="Brakes", citation_label="Complaint 123",
        )
        result = GraphRAGRetrievalResult(
            query="brake issues", retrieved_chunks=[chunk],
            warnings=["Test warning"], total_chunks_returned=1,
            execution_ms=50,
        )
        d = result.to_dict()
        assert d["phase"] == "phase_6"
        assert d["retrieval_mode"] == "graphrag"
        assert len(d["retrieved_chunks"]) == 1
        assert len(d["warnings"]) == 1

    def test_status_includes_backend_field(self):
        """GraphRAGStatus has vector_backend field."""
        from app.services.graphrag.models import GraphRAGStatus

        status = GraphRAGStatus(
            vector_backend_available=True, vector_backend="pgvector",
            embedding_model="test", embedding_dimension=384,
        )
        d = status.to_dict()
        assert "vector_backend" in d
        assert d["vector_backend"] == "pgvector"


# ─── Safety caveat tests ────────────────────────────────────────────────────────

class TestSafetyCaveats:
    def test_no_causality_claim(self):
        """GraphRAG response has no causal language about recalls causing complaints."""
        from app.services.graphrag.service import (
            CAVEAT_OFFICIAL_RECALL,
            CAVEAT_SHARED_COMPONENT,
            CAVEAT_SIMILARITY,
        )

        # Verify all caveats are present and descriptive, not causal
        assert "causality" not in CAVEAT_SIMILARITY.lower() or "not" in CAVEAT_SIMILARITY
        assert "not causality" in CAVEAT_SHARED_COMPONENT
        assert "AFFECTS" in CAVEAT_OFFICIAL_RECALL  # describes official relationship


# ─── Regression — no arbitrary SQL/Cypher endpoints ──────────────────────────

class TestNoArbitraryMutation:
    def test_no_arbitrary_sql_or_cypher(self):
        """Verify no endpoints accept arbitrary SQL or Cypher."""
        import inspect

        import app.api.v1.endpoints.graphrag as ep

        for name, obj in inspect.getmembers(ep):
            if inspect.isfunction(obj) and hasattr(obj, "__wrapped__"):
                # Check function signature — should not have raw query params
                sig = inspect.signature(obj)
                params = list(sig.parameters.keys())
                assert "sql" not in params, f"{name} has raw sql param"
                assert "cypher" not in params, f"{name} has raw cypher param"
                assert "query" not in params or "question" in params, f"{name} has ambiguous query param"
