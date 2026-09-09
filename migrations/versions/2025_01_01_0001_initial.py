"""Initial schema - Phase 0

Revision ID: 2025_01_01_0001
Revises:
Create Date: 2025-01-01 00:01:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2025_01_01_0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # App tables: users
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('email', sa.Text(), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('role', sa.String(50), nullable=False, server_default='analyst'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # App tables: chat_sessions
    op.create_table(
        'chat_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # App tables: chat_messages
    op.create_table(
        'chat_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('chat_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_check_constraint('chat_message_role_check', 'chat_messages', "role IN ('user', 'assistant', 'system')")

    # App tables: agent_runs
    op.create_table(
        'agent_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('message_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('chat_messages.id', ondelete='SET NULL'), nullable=True),
        sa.Column('intent', sa.Text(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='created'),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('warnings', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )

    # App tables: tool_calls
    op.create_table(
        'tool_calls',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('agent_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tool_name', sa.Text(), nullable=False),
        sa.Column('input_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('output_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # App tables: citations
    op.create_table(
        'citations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('agent_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_type', sa.Text(), nullable=False),
        sa.Column('source_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('field_name', sa.Text(), nullable=True),
        sa.Column('chunk_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('text_span', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # Source tables: source_runs
    op.create_table(
        'source_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('source_name', sa.Text(), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('source_type', sa.String(50), nullable=False),
        sa.Column('file_name', sa.Text(), nullable=True),
        sa.Column('file_sha256', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
    )

    # Source tables: raw_source_rows
    op.create_table(
        'raw_source_rows',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('source_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('source_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_name', sa.Text(), nullable=False),
        sa.Column('source_record_key', sa.Text(), nullable=False),
        sa.Column('row_number', sa.Integer(), nullable=True),
        sa.Column('raw_json', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('idx_raw_source_rows_source', 'raw_source_rows', ['source_name', 'source_record_key'], unique=True)

    # Domain tables: vehicles
    op.create_table(
        'vehicles',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('make', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('model_year', sa.Integer(), nullable=False),
        sa.Column('normalized_make', sa.Text(), nullable=False),
        sa.Column('normalized_model', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('idx_vehicles_nmky', 'vehicles', ['normalized_make', 'normalized_model', 'model_year'], unique=True)

    # Domain tables: components
    op.create_table(
        'components',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('normalized_name', sa.Text(), unique=True, nullable=False),
        sa.Column('category', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # Domain tables: complaints
    op.create_table(
        'complaints',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('odi_number', sa.Text(), unique=True, nullable=True),
        sa.Column('vehicle_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('component_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('components.id'), nullable=True),
        sa.Column('source_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('source_runs.id'), nullable=True),
        sa.Column('source_record_key', sa.Text(), nullable=False),
        sa.Column('received_date', sa.Date(), nullable=True),
        sa.Column('incident_date', sa.Date(), nullable=True),
        sa.Column('original_component', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('narrative', sa.Text(), nullable=True),
        sa.Column('crash_flag', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('fire_flag', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('injury_flag', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('death_flag', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('raw_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('idx_complaints_vehicle_component', 'complaints', ['vehicle_id', 'component_id'])
    op.create_index('idx_complaints_received_date', 'complaints', ['received_date'])
    op.create_index('idx_complaints_flags', 'complaints', ['crash_flag', 'fire_flag', 'injury_flag', 'death_flag'])

    # Domain tables: recalls
    op.create_table(
        'recalls',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('campaign_number', sa.Text(), nullable=False),
        sa.Column('component_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('components.id'), nullable=True),
        sa.Column('source_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('source_runs.id'), nullable=True),
        sa.Column('source_record_key', sa.Text(), nullable=False),
        sa.Column('report_received_date', sa.Date(), nullable=True),
        sa.Column('original_component', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('consequence', sa.Text(), nullable=True),
        sa.Column('remedy', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('units_affected', sa.Integer(), nullable=True),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('raw_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('idx_recalls_campaign', 'recalls', ['campaign_number'])
    op.create_index('idx_recalls_component_date', 'recalls', ['component_id', 'report_received_date'])

    # Domain tables: recall_vehicle_links
    op.create_table(
        'recall_vehicle_links',
        sa.Column('recall_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('recalls.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('vehicle_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vehicles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('relation_source', sa.Text(), nullable=False, server_default='source_record'),
    )

    # Domain tables: investigations
    op.create_table(
        'investigations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('investigation_number', sa.Text(), unique=True, nullable=False),
        sa.Column('component_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('components.id'), nullable=True),
        sa.Column('source_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('source_runs.id'), nullable=True),
        sa.Column('source_record_key', sa.Text(), nullable=False),
        sa.Column('open_date', sa.Date(), nullable=True),
        sa.Column('close_date', sa.Date(), nullable=True),
        sa.Column('status', sa.Text(), nullable=True),
        sa.Column('investigation_type', sa.Text(), nullable=True),
        sa.Column('original_component', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('raw_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # Domain tables: investigation_vehicle_links
    op.create_table(
        'investigation_vehicle_links',
        sa.Column('investigation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('investigations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('vehicle_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vehicles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('relation_source', sa.Text(), nullable=False, server_default='source_record'),
    )

    # Domain tables: manufacturer_communications
    op.create_table(
        'manufacturer_communications',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('communication_number', sa.Text(), nullable=False),
        sa.Column('component_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('components.id'), nullable=True),
        sa.Column('source_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('source_runs.id'), nullable=True),
        sa.Column('source_record_key', sa.Text(), nullable=False),
        sa.Column('communication_date', sa.Date(), nullable=True),
        sa.Column('communication_type', sa.Text(), nullable=True),
        sa.Column('original_component', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('raw_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )

    # Domain tables: manufacturer_communication_vehicle_links
    op.create_table(
        'manufacturer_communication_vehicle_links',
        sa.Column('communication_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('manufacturer_communications.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('vehicle_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vehicles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('relation_source', sa.Text(), nullable=False, server_default='source_record'),
    )


def downgrade() -> None:
    op.drop_table('manufacturer_communication_vehicle_links')
    op.drop_table('manufacturer_communications')
    op.drop_table('investigation_vehicle_links')
    op.drop_table('investigations')
    op.drop_table('recall_vehicle_links')
    op.drop_table('recalls')
    op.drop_index('idx_complaints_flags', table_name='complaints')
    op.drop_index('idx_complaints_received_date', table_name='complaints')
    op.drop_index('idx_complaints_vehicle_component', table_name='complaints')
    op.drop_table('complaints')
    op.drop_table('components')
    op.drop_index('idx_vehicles_nmky', table_name='vehicles')
    op.drop_table('vehicles')
    op.drop_index('idx_raw_source_rows_source', table_name='raw_source_rows')
    op.drop_table('raw_source_rows')
    op.drop_table('source_runs')
    op.drop_table('citations')
    op.drop_table('tool_calls')
    op.drop_table('agent_runs')
    op.drop_table('chat_messages')
    op.drop_table('chat_sessions')
    op.drop_table('users')
