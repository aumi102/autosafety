"""
Read-only SQL executor for Phase 2 analytics.

Validates SQL through sql_safety module before execution.
Uses SQLAlchemy text() with bound parameters.
Enforces LIMIT.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.sql_safety import validate_sql, enforce_limit, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of SQL execution."""
    columns: Optional[list[str]]
    rows: Optional[list[dict]]
    row_count: int
    execution_ms: int
    truncated: bool
    validated: bool
    validation_warning: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "execution_ms": self.execution_ms,
            "truncated": self.truncated,
            "validated": self.validated,
        }


def execute_readonly_sql(
    session: Session,
    sql: str,
    params: Optional[dict[str, Any]] = None,
    max_rows: int = 100,
) -> ExecutionResult:
    """
    Execute a SQL query read-only with validation.

    Args:
        session: SQLAlchemy session
        sql: SQL query string (should use :param_name placeholders)
        params: Bound parameters
        max_rows: Maximum rows to return (enforced as hard limit)

    Returns:
        ExecutionResult with columns, rows, metadata
    """
    start_time = time.time()
    params = params or {}

    # Step 1: Validate SQL
    validation = validate_sql(sql)
    if not validation.valid:
        logger.warning(f"SQL validation failed: {validation.reason}")
        return ExecutionResult(
            columns=None,
            rows=None,
            row_count=0,
            execution_ms=int((time.time() - start_time) * 1000),
            truncated=False,
            validated=False,
            validation_warning=f"SQL validation failed: {validation.reason}",
        )

    # Step 2: Enforce LIMIT (add if missing, cap if excessive)
    # Fetch one extra row to detect truncation reliably
    enforced_max = min(max_rows, 500)
    fetch_limit = min(enforced_max + 1, 500)
    sql = enforce_limit(sql, default_limit=fetch_limit, hard_limit=fetch_limit)

    try:
        result = session.execute(text(sql), params)
        columns = list(result.keys())
        rows = []
        truncated = False

        for i, row in enumerate(result):
            if i >= enforced_max:
                truncated = True
                break
            # Convert row to dict
            rows.append(dict(zip(columns, row)))

        execution_ms = int((time.time() - start_time) * 1000)

        return ExecutionResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_ms=execution_ms,
            truncated=truncated,
            validated=True,
            validation_warning=validation.warning,
        )

    except Exception as e:
        logger.exception(f"SQL execution failed: {e}")
        return ExecutionResult(
            columns=None,
            rows=None,
            row_count=0,
            execution_ms=int((time.time() - start_time) * 1000),
            truncated=False,
            validated=True,
            validation_warning=f"Execution error: {str(e)}",
        )


def validate_template_sql(sql: str) -> ValidationResult:
    """
    Validate a generated SQL template string.

    This is called before inserting parameters to ensure
    the template itself is safe.
    """
    return validate_sql(sql)
