"""Phase 8: bounded multi-turn conversation state.

Revision ID: 2025_01_01_0004
Revises: 2025_01_01_0003
Create Date: 2025-01-01 00:04:00.000000

Adds chat_turns and chat_turn_citations for session-scoped guarded
multi-turn answering, plus chat_sessions.last_activity_at as the
retention anchor.

Only bounded, application-validated turn outcome is persisted. No provider
prompt, provider raw response, API key, connection string, raw SQL, or raw
Cypher is stored by these tables. Existing data is not modified or removed.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "2025_01_01_0004"
down_revision = "2025_01_01_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Retention anchor on the existing session table. Backfilled from
    # created_at so pre-Phase-8 sessions keep a meaningful expiry basis.
    op.add_column(
        "chat_sessions",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE chat_sessions SET last_activity_at = created_at"
        " WHERE last_activity_at IS NULL"
    )
    op.alter_column(
        "chat_sessions",
        "last_activity_at",
        nullable=False,
        server_default=sa.func.now(),
    )
    op.create_index(
        "ix_chat_sessions_last_activity_at", "chat_sessions", ["last_activity_at"]
    )

    op.create_table(
        "chat_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer, nullable=False),
        sa.Column(
            "user_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "assistant_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("resolved_question", sa.Text, nullable=False),
        sa.Column("context_applied", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("entity_make", sa.String(64), nullable=True),
        sa.Column("entity_model", sa.String(64), nullable=True),
        sa.Column("entity_model_year", sa.Integer, nullable=True),
        sa.Column("entity_component", sa.String(64), nullable=True),
        sa.Column("synthesis_mode", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("abstained", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("abstention_reason", sa.String(128), nullable=True),
        sa.Column("confidence_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(10), nullable=False, server_default="low"),
        sa.Column("claim_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("citation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("warnings", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("session_id", "turn_index", name="uq_chat_turn_session_index"),
    )
    op.create_index("ix_chat_turns_session_id", "chat_turns", ["session_id"])

    op.create_table(
        "chat_turn_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "turn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer, nullable=False),
        sa.Column("citation_id", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_record_key", sa.String(128), nullable=False),
        sa.Column("source_entity_id", sa.String(64), nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("text_span", sa.Text, nullable=False, server_default=""),
        sa.Column("retrieval_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("relation_basis", sa.String(64), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("cited_by_claim", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_chat_turn_citations_turn_id", "chat_turn_citations", ["turn_id"])
    op.create_index("ix_chat_turn_citations_session_id", "chat_turn_citations", ["session_id"])
    op.create_index(
        "ix_chat_turn_citations_source_record_key",
        "chat_turn_citations",
        ["source_record_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_turn_citations_source_record_key", table_name="chat_turn_citations")
    op.drop_index("ix_chat_turn_citations_session_id", table_name="chat_turn_citations")
    op.drop_index("ix_chat_turn_citations_turn_id", table_name="chat_turn_citations")
    op.drop_table("chat_turn_citations")
    op.drop_index("ix_chat_turns_session_id", table_name="chat_turns")
    op.drop_table("chat_turns")
    op.drop_index("ix_chat_sessions_last_activity_at", table_name="chat_sessions")
    op.drop_column("chat_sessions", "last_activity_at")
