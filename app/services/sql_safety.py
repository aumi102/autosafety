"""
SQL safety validator.

Allows only SELECT and WITH statements.
Blocks all write/DDL operations.
Enforces query constraints for analytics safety.
"""

import re
from dataclasses import dataclass
from typing import Optional

# Unsafe SQL keywords that should never appear in user-generated queries
BLOCKED_KEYWORDS = {
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE",
    "CREATE", "GRANT", "REVOKE", "COPY", "MERGE", "CALL", "EXECUTE",
    "INTO",  # INSERT INTO, COPY INTO
}

# Patterns that indicate multiple statements or injection attempts
BLOCKED_PATTERNS = [
    r";",                    # Statement terminator (possible multi-statement)
    r"--",                   # SQL comment (could hide keywords)
    r"/\*",                  # Block comment
    r"\*/",
    r"xp_",                  # SQL Server extended procedures
    r"sp_executesql",        # SQL Server dynamic SQL
    r"exec\s*\(",            # SQL Server execute
    r"eval\s*\(",            # Code injection attempt
    r"__import__",           # Python injection
    r"os\.system",           # OS command injection
]

# Allowed SQL statement prefixes
ALLOWED_PREFIXES = {"SELECT", "WITH", "EXPLAIN", "DESCRIBE", "SHOW"}


@dataclass
class ValidationResult:
    valid: bool
    query: str
    reason: Optional[str] = None
    warning: Optional[str] = None

    def __bool__(self) -> bool:
        return self.valid


def validate_sql(query: str) -> ValidationResult:
    """
    Validate a SQL query for safety.

    Returns ValidationResult with:
    - valid: True if query is safe
    - query: the normalized query
    - reason: explanation if invalid
    - warning: caution if valid but needs attention
    """
    if not query or not query.strip():
        return ValidationResult(
            valid=False,
            query=query or "",
            reason="Empty query"
        )

    # Normalize whitespace for analysis
    normalized = re.sub(r"\s+", " ", query.strip())

    # Check for blocked patterns first
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return ValidationResult(
                valid=False,
                query=query,
                reason=f"Blocked pattern detected: {pattern}"
            )

    # Check for blocked keywords (excluding those in allowed contexts)
    query_upper = query.upper()
    for keyword in BLOCKED_KEYWORDS:
        # Use word boundary matching to avoid false positives
        pattern = r'\b' + keyword + r'\b'
        if re.search(pattern, query_upper):
            return ValidationResult(
                valid=False,
                query=query,
                reason=f"Blocked keyword: {keyword}"
            )

    # Check that query starts with allowed statement type
    first_word = query_upper.strip().split()[0]
    if first_word not in ALLOWED_PREFIXES:
        return ValidationResult(
            valid=False,
            query=query,
            reason=f"Only SELECT/WITH queries are allowed. Got: {first_word}"
        )

    # Check for subqueries with write operations
    # This is a simplified check - a proper implementation would parse the AST
    subquery_blocked = re.findall(
        r'\(([^)]*(?:INSERT|UPDATE|DELETE|DROP)[^)]*)\)',
        query_upper
    )
    if subquery_blocked:
        return ValidationResult(
            valid=False,
            query=query,
            reason="Query contains write operation in subquery"
        )

    # Add warning for queries without LIMIT
    has_limit = bool(re.search(r'\bLIMIT\b', query_upper))
    has_aggregate = bool(re.search(
        r'\b(COUNT|SUM|AVG|MIN|MAX|GROUP BY)\b',
        query_upper
    ))

    warning = None
    if not has_limit and not has_aggregate:
        warning = "Query has no LIMIT - consider adding LIMIT 100 for safety"

    return ValidationResult(
        valid=True,
        query=query,
        warning=warning
    )


def enforce_limit(query: str, default_limit: int = 100, hard_limit: int = 500) -> str:
    """
    Add or enforce LIMIT on a query.

    If query has no LIMIT, adds default_limit.
    If query LIMIT exceeds hard_limit, replaces with hard_limit.
    """
    query_upper = query.upper()

    # Check if LIMIT already exists
    limit_match = re.search(r'\bLIMIT\s+(\d+)', query_upper)
    if limit_match:
        existing_limit = int(limit_match.group(1))
        if existing_limit > hard_limit:
            # Replace excessive LIMIT with hard limit
            return re.sub(r'\bLIMIT\s+\d+', f'LIMIT {hard_limit}', query, flags=re.IGNORECASE)
        return query

    # Add LIMIT
    return f"{query.rstrip(';')} LIMIT {default_limit}"
