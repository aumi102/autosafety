"""Hybrid SQL + Graph API endpoint — Phase 4."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.hybrid import answer_hybrid_question

router = APIRouter(tags=["hybrid"])


class HybridQueryRequest(BaseModel):
    question: str
    include_sql: bool = True
    include_graph: bool = True
    max_graph_paths: int = 5


@router.post("/query")
def hybrid_query(data: HybridQueryRequest) -> dict:
    """
    Answer a hybrid SQL + graph question.

    Combines Phase 2 deterministic SQL analytics with Phase 3 graph retrieval.
    Returns structured answer conforming to answer_contract.md with intent="hybrid".

    Example questions:
    - "Which component has the most complaints for Ford F-150 2020, and are there related recalls?"
    - "Show complaints and recall evidence for Honda Accord 2021."
    - "Does Toyota Camry 2022 have complaints and recalls in the current database?"

    WARNING: Graph relationships via shared vehicle/component are POTENTIAL associations,
    not official causality. Only Recall → AFFECTS → ModelYear is official.
    """
    try:
        result = answer_hybrid_question(data.question)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Hybrid query failed: {e}"
        )
