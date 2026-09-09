"""Phase 9: execution audit columns for agent_runs and tool_calls.

Revision ID: 2025_01_01_0005
Revises: 2025_01_01_0004
Create Date: 2025-01-01 00:05:00.000000

The `agent_runs` and `tool_calls` tables have existed since 2025_01_01_0001 but
were never populated. Phase 9 turns them into a real execution audit trail, so
this revision adds the safe metadata columns that were missing.

Additive only. No existing column is altered or dropped, no historical revision
is rewritten, and no row is modified beyond backfilling the new
`agent_runs.started_at` / `tool_calls.started_at` from `created_at`.

Only non-sensitive execution metadata is recorded. No prompt, provider raw
response, credential, connection string, raw SQL, raw Cypher, or unrestricted
tool argument is stored by these columns; the pre-existing `input_json` and
`output_json` columns are deliberately left empty by the Phase 9 recorder.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "2025_01_01_0005"
down_revision = "2025_01_01_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------- agent_runs
    op.add_column(
        "agent_runs",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "turn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_turns.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column("agent_runs", sa.Column("phase", sa.String(20), nullable=True))
    op.add_column("agent_runs", sa.Column("surface", sa.String(32), nullable=True))
    op.add_column("agent_runs", sa.Column("provider", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("model", sa.String(128), nullable=True))
    op.add_column("agent_runs", sa.Column("synthesis_mode", sa.String(20), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("tool_call_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("fallback_used", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "agent_runs",
        sa.Column("abstained", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column("agent_runs", sa.Column("abstention_reason", sa.String(128), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("confidence_score", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("agent_runs", sa.Column("confidence_level", sa.String(10), nullable=True))
    op.add_column("agent_runs", sa.Column("validation_outcome", sa.String(32), nullable=True))
    op.add_column("agent_runs", sa.Column("error_code", sa.String(64), nullable=True))
    op.execute("UPDATE agent_runs SET started_at = created_at WHERE started_at IS NULL")
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_turn_id", "agent_runs", ["turn_id"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])

    # ---------------------------------------------------------------- tool_calls
    op.add_column("tool_calls", sa.Column("call_id", sa.String(64), nullable=True))
    op.add_column("tool_calls", sa.Column("operation", sa.String(64), nullable=True))
    op.add_column(
        "tool_calls",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("success", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column("tool_calls", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column(
        "tool_calls",
        sa.Column("evidence_item_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "tool_calls",
        sa.Column("truncated", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE tool_calls SET started_at = created_at WHERE started_at IS NULL")
    op.create_index("ix_tool_calls_agent_run_id", "tool_calls", ["agent_run_id"])
    # Idempotency: one row per application-owned call id within a run, so a
    # retried or replayed execution cannot create duplicate audit rows.
    op.create_unique_constraint(
        "uq_tool_call_run_call_id", "tool_calls", ["agent_run_id", "call_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_tool_call_run_call_id", "tool_calls", type_="unique")
    op.drop_index("ix_tool_calls_agent_run_id", table_name="tool_calls")
    for column in (
        "truncated",
        "evidence_item_count",
        "error_code",
        "success",
        "completed_at",
        "started_at",
        "operation",
        "call_id",
    ):
        op.drop_column("tool_calls", column)

    op.drop_index("ix_agent_runs_created_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_turn_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_conversation_id", table_name="agent_runs")
    for column in (
        "error_code",
        "validation_outcome",
        "confidence_level",
        "confidence_score",
        "abstention_reason",
        "abstained",
        "fallback_used",
        "tool_call_count",
        "started_at",
        "synthesis_mode",
        "model",
        "provider",
        "surface",
        "phase",
        "turn_id",
        "conversation_id",
    ):
        op.drop_column("agent_runs", column)
