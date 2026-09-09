"""
SQL Analytics module for Phase 2.

Provides safe, deterministic, template-based SQL analytics.
This is NOT full LLM Text-to-SQL — it uses approved templates only.

Components:
- schema_registry: defines allowed tables, columns, joins
- templates: SQL query templates for supported analytics
- question_parser: deterministic entity extraction and intent classification
- executor: read-only SQL execution with validation
- service: orchestration layer
"""

from app.services.sql_analytics.service import SqlAnalyticsService, answer_sql_analytics_question

__all__ = ["answer_sql_analytics_question", "SqlAnalyticsService"]
