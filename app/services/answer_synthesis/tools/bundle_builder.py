"""
Evidence Bundle Builder — Phase 7B.

Builds a bounded, sanitized evidence bundle from tool call results.
Application-owned. No LLM prompt construction here.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.answer_synthesis.tools.base import (
    EvidenceBundle,
    EvidenceItem,
    ToolCallResult,
)

logger = logging.getLogger(__name__)

# Forbidden keys that are stripped from all evidence metadata
_FORBIDDEN_KEYS: set[str] = {
    "password",
    "api_key",
    "apikey",
    "secret",
    "token",
    "credential",
    "database_url",
    "db_url",
    "connection_string",
    "n4j_password",
    "neo4j_password",
    "neo4j_uri",
    "bolt_uri",
    "http_uri",
    "redis_url",
    "uri",
    "url",
    "private_key",
    "bearer",
    "authorization",
}

_MAX_ITEMS = 20
_MAX_TOTAL_CHARS = 16000
_MAX_ITEM_TEXT = 2000


class EvidenceBundleBuilder:
    """
    Builds a bounded evidence bundle from tool call results.

    Responsibilities:
    - Deduplicate repeated evidence by source_record_key
    - Preserve citation-capable metadata
    - Preserve official vs potential relation semantics
    - Sanitize strings (strip forbidden keys recursively)
    - Bound total items, total characters, per-item text length
    - Truncate safely and mark as truncated
    - No raw embeddings, SQL, Cypher, or credentials
    """

    def __init__(
        self,
        max_items: int = _MAX_ITEMS,
        max_total_chars: int = _MAX_TOTAL_CHARS,
        max_item_text: int = _MAX_ITEM_TEXT,
    ):
        self.max_items = max_items
        self.max_total_chars = max_total_chars
        self.max_item_text = max_item_text
        self._items: list[EvidenceItem] = []
        self._tool_calls: list[ToolCallResult] = []
        self._warnings: list[str] = []
        self._seen_keys: set[str] = set()
        self._total_chars = 0

    def add_tool_result(self, result: ToolCallResult) -> None:
        """Process a tool call result and extract evidence items."""
        self._tool_calls.append(result)

        # Propagate warnings from tool results
        if result.warnings:
            for w in result.warnings:
                if w not in self._warnings:
                    self._warnings.append(w)

        if not result.success or result.data is None:
            return

        # Extract evidence items based on tool type
        tool_name = result.tool_name
        data = result.data

        if tool_name == "graphrag_retrieval_tool":
            self._extract_graphrag_evidence(data)
        elif tool_name == "sql_analytics_tool":
            self._extract_sql_evidence(data)
        elif tool_name == "graph_evidence_tool":
            self._extract_graph_evidence(data)
        elif tool_name == "vehicle_resolution_tool":
            self._extract_vehicle_evidence(data)

    def _extract_graphrag_evidence(self, data: dict[str, Any]) -> None:
        """Extract citation items from GraphRAG retrieval results."""
        chunks = data.get("retrieved_chunks", [])
        citations = data.get("citations", [])
        graph_paths = data.get("graph_paths", [])

        # Index citations by source_key for lookup
        citation_map: dict[str, dict] = {}
        for c in citations:
            key = f"{c.get('source_type', '')}:{c.get('source_key', '')}"
            citation_map[key] = c

        # Extract from chunks
        for chunk in chunks:
            source_type = chunk.get("source_type", "unknown")
            source_key = chunk.get("source_record_key", "")
            if not source_key:
                continue

            dedup_key = f"{source_type}:{source_key}"
            if dedup_key in self._seen_keys:
                continue
            self._seen_keys.add(dedup_key)

            citation_id = EvidenceItem.make_citation_id(source_type, source_key)
            item = EvidenceItem(
                evidence_id=EvidenceItem.make_id(source_type, source_key),
                tool_name="graphrag_retrieval_tool",
                evidence_type=source_type,
                source_record_key=source_key,
                source_entity_id=chunk.get("source_id"),
                text=self._safe_truncate(chunk.get("text", "")),
                metadata=_sanitize(chunk.get("metadata", {})),
                relation_basis=None,
                score=chunk.get("score", 1.0),
                citation_label=chunk.get("citation_label"),
                citation_id=citation_id,
            )
            self._add_item(item)

        # Extract from graph paths
        for path in graph_paths:
            source_type = path.get("source_type", "graph")
            source_key = path.get("source_key", "")
            relation = path.get("relation_source", "unknown")
            dedup_key = f"path:{source_type}:{source_key}:{relation}"
            if dedup_key in self._seen_keys:
                continue
            self._seen_keys.add(dedup_key)

            item = EvidenceItem(
                evidence_id=EvidenceItem.make_id(source_type, source_key or "path"),
                tool_name="graphrag_retrieval_tool",
                evidence_type="graph_path",
                source_record_key=source_key or "graph",
                text=self._safe_truncate(path.get("path_text", "")),
                metadata=_sanitize({
                    "relation_source": relation,
                    "confidence": path.get("confidence", 1.0),
                }),
                relation_basis=relation,
                score=path.get("confidence", 1.0),
            )
            self._add_item(item)

    def _extract_sql_evidence(self, data: dict[str, Any]) -> None:
        """Extract SQL result as structured evidence."""
        rows = data.get("rows", [])
        columns = data.get("columns", [])
        operation = data.get("operation", "unknown")

        dedup_key = f"sql:{operation}"
        if dedup_key in self._seen_keys:
            return
        self._seen_keys.add(dedup_key)

        # Format rows as readable text
        table_text = _format_table(rows, columns)
        item = EvidenceItem(
            evidence_id=EvidenceItem.make_id("sql", operation),
            tool_name="sql_analytics_tool",
            evidence_type="sql_result",
            source_record_key=operation,
            text=self._safe_truncate(table_text),
            metadata=_sanitize({
                "operation": operation,
                "columns": columns,
                "row_count": len(rows),
            }),
            relation_basis="sql_analytics",
            score=1.0,
        )
        self._add_item(item)

    def _extract_graph_evidence(self, data: dict[str, Any]) -> None:
        """Extract evidence from graph tool results."""
        operation = data.get("operation", "unknown")
        recalls = data.get("recalls", [])
        shared_recalls = data.get("shared_recalls", [])
        relation_basis = data.get("relation_basis", operation)

        # Extract recalls
        for recall in recalls:
            campaign = recall.get("campaign_number", "")
            dedup_key = f"recall:{campaign}"
            if not campaign or dedup_key in self._seen_keys:
                continue
            self._seen_keys.add(dedup_key)

            citation_id = EvidenceItem.make_citation_id("recall", campaign)
            item = EvidenceItem(
                evidence_id=EvidenceItem.make_id("recall", campaign),
                tool_name="graph_evidence_tool",
                evidence_type="recall",
                source_record_key=campaign,
                text=self._safe_truncate(
                    f"Recall {campaign}: {recall.get('summary', '')}"
                ),
                metadata=_sanitize({
                    "component": recall.get("component"),
                    "remedy": recall.get("remedy", "")[:500],
                    "report_date": recall.get("report_received_date"),
                    "units_affected": recall.get("units_affected"),
                }),
                relation_basis=relation_basis,
                score=1.0,
                citation_id=citation_id,
                citation_label=f"Recall {campaign}",
            )
            self._add_item(item)

        # Extract shared recalls
        for recall in shared_recalls:
            campaign = recall.get("campaign_number", "")
            dedup_key = f"shared:{campaign}"
            if not campaign or dedup_key in self._seen_keys:
                continue
            self._seen_keys.add(dedup_key)

            item = EvidenceItem(
                evidence_id=EvidenceItem.make_id("recall", f"shared-{campaign}"),
                tool_name="graph_evidence_tool",
                evidence_type="recall",
                source_record_key=campaign,
                text=self._safe_truncate(recall.get("summary", "")),
                metadata=_sanitize({
                    "component": recall.get("component"),
                    "relation": "shared_component",
                }),
                relation_basis="potentially_related_by_shared_component",
                score=0.7,
            )
            self._add_item(item)

    def _extract_vehicle_evidence(self, data: dict[str, Any]) -> None:
        """Extract vehicle resolution as evidence."""
        dedup_key = "vehicle_resolution"
        if dedup_key in self._seen_keys:
            return
        self._seen_keys.add(dedup_key)

        status = data.get("status", "unknown")
        vid = data.get("vehicle_id")
        item = EvidenceItem(
            evidence_id=EvidenceItem.make_id("vehicle", data.get("normalized_make", "unknown")),
            tool_name="vehicle_resolution_tool",
            evidence_type="vehicle_resolution",
            source_record_key=data.get("normalized_make", "") + ":" + str(data.get("model_year", "")),
            source_entity_id=vid,
            text=self._safe_truncate(
                f"Vehicle resolution: {data.get('normalized_make', '')} {data.get('normalized_model', '')} {data.get('model_year', '')} — {status}"
            ),
            metadata=_sanitize({
                "resolved": data.get("resolved"),
                "status": status,
                "vehicle_id": vid,
                "normalized_make": data.get("normalized_make"),
                "normalized_model": data.get("normalized_model"),
                "model_year": data.get("model_year"),
            }),
            relation_basis="vehicle_resolution",
            score=1.0,
        )
        self._add_item(item)

    def _add_item(self, item: EvidenceItem) -> bool:
        """
        Add an item to the bundle if within bounds.
        Caller (extraction method) is responsible for deduplication
        by adding dedup keys to _seen_keys before calling this.
        Returns True if added, False if skipped.
        """
        if len(self._items) >= self.max_items:
            return False

        char_count = len(item.text)
        if self._total_chars + char_count > self.max_total_chars:
            # Don't add partial items — just stop
            return False

        self._items.append(item)
        self._total_chars += char_count
        return True

    def _safe_truncate(self, text: str) -> str:
        """Truncate text safely and mark as truncated if needed."""
        if not text:
            return ""
        if len(text) <= self.max_item_text:
            return text
        return text[: self.max_item_text]

    def build(self) -> EvidenceBundle:
        """Build the final evidence bundle."""
        return EvidenceBundle(
            items=self._items,
            tool_calls=self._tool_calls,
            warnings=self._warnings,
            total_characters=self._total_chars,
            truncated=len(self._items) >= self.max_items
            or self._total_chars >= self.max_total_chars,
        )


def _sanitize(d: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively strip forbidden keys from a dict.

    Removes keys whose lowercase form matches any forbidden pattern.
    Strips nested dicts and list-of-dicts.
    """
    if not isinstance(d, dict):
        return d
    result: dict[str, Any] = {}
    for k, v in d.items():
        k_lower = k.lower()
        if any(fk in k_lower for fk in _FORBIDDEN_KEYS):
            continue
        if isinstance(v, dict):
            result[k] = _sanitize(v)
        elif isinstance(v, list):
            result[k] = [
                _sanitize(i) if isinstance(i, dict) else i
                for i in v
            ]
        else:
            result[k] = v
    return result


def _format_table(rows: list[dict], columns: list[str]) -> str:
    """Format SQL rows as readable text for evidence."""
    if not rows or not columns:
        return "No results."
    available = [c for c in columns if c in rows[0].keys()]
    if not available:
        available = list(rows[0].keys())
    header = " | ".join(available)
    sep = "-" * len(header)
    lines = [header, sep]
    for row in rows[:10]:  # cap at 10 rows for evidence
        vals = [str(row.get(c, "")) for c in available]
        lines.append(" | ".join(vals))
    if len(rows) > 10:
        lines.append(f"... ({len(rows) - 10} more rows)")
    return "\n".join(lines)
