"""time travel snapshots and scheduled watchdogs

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-23 18:00:00

Governance features: the snapshots kept by the time travel of a referential, and the watchdogs that run the
assistant on a schedule (or after a synchronization) with their execution history.

The governance metadata of a referential (visibility, owner, masked columns) lives in its definition, which is
a JSON document: nothing to migrate for it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'referential_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('referential_id', sa.String(length=64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=True),
        sa.Column('file', sa.String(length=255), nullable=False),
        sa.Column('size', sa.Integer(), nullable=True),
        sa.Column('schema_hash', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('columns', sa.JSON(), nullable=True),
        sa.Column('encrypted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('run_id', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('referential_id', 'file', name='uq_snapshots_file'),
    )
    op.create_index('ix_referential_snapshots_referential_id', 'referential_snapshots', ['referential_id'])
    op.create_index('ix_referential_snapshots_created_at', 'referential_snapshots', ['created_at'])

    op.create_table(
        'watchdog_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('referential_ids', sa.JSON(), nullable=False),
        sa.Column('trigger_type', sa.String(length=16), nullable=False, server_default='cron'),
        sa.Column('schedule', sa.String(length=100), nullable=True),
        sa.Column('prompt', sa.String(length=8000), nullable=False, server_default=''),
        sa.Column('template', sa.String(length=32), nullable=True),
        sa.Column('notify_emails', sa.JSON(), nullable=True),
        sa.Column('notify_channels', sa.JSON(), nullable=True),
        sa.Column('min_severity', sa.String(length=16), nullable=False, server_default='LOW'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("trigger_type IN ('cron', 'on_sync')", name='ck_watchdog_trigger'),
    )
    op.create_index('ix_watchdog_tasks_user_id', 'watchdog_tasks', ['user_id'])

    op.create_table(
        'watchdog_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('trigger', sa.String(length=80), nullable=False, server_default='cron'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='ok'),
        sa.Column('finding_detected', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('severity', sa.String(length=16), nullable=True),
        sa.Column('summary', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('details_markdown', sa.String(length=200000), nullable=False, server_default=''),
        sa.Column('affected_items_count', sa.Integer(), nullable=True),
        sa.Column('steps', sa.JSON(), nullable=True),
        sa.Column('notifications', sa.JSON(), nullable=True),
        sa.Column('error', sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['watchdog_tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status IN ('ok', 'finding', 'error')", name='ck_watchdog_run_status'),
    )
    op.create_index('ix_watchdog_runs_task_id', 'watchdog_runs', ['task_id'])
    op.create_index('ix_watchdog_runs_started_at', 'watchdog_runs', ['started_at'])


def downgrade() -> None:
    op.drop_table('watchdog_runs')
    op.drop_table('watchdog_tasks')
    op.drop_table('referential_snapshots')
