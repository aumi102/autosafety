"""
Hybrid service package — Phase 4.

Combines Phase 2 SQL analytics with Phase 3 graph retrieval
for hybrid SQL + graph evidence answers.

Components:
- hybrid_models: HybridIntent, HybridAnswerResult, GraphEvidenceItem
- hybrid_parser: deterministic hybrid question parser
- hybrid_service: orchestrates SQL + graph, returns answer contract dict
- answer_composer: merges SQL + graph evidence into AnswerResponse
"""

from app.services.hybrid.hybrid_service import answer_hybrid_question

__all__ = ["answer_hybrid_question"]
