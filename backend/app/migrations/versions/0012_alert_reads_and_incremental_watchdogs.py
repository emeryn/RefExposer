"""alerts marked as read, and watchdogs looking only at what is new

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-28 10:00:00

`alert_reads` keeps, per account, the alerts it has acknowledged. An alert is not a row — the feed is computed
from the state of the referentials and the recorded watchdog runs — so the date the alert carried when it was
marked is kept with the marker: the same condition happening again comes back unread.

`watchdog_tasks.only_new_data` asks an investigation to report on what the latest versions added or changed,
instead of the whole data every time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alert_reads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('alert_id', sa.String(length=200), nullable=False),
        sa.Column('at', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'alert_id', name='uq_alert_read'),
    )
    op.create_index('ix_alert_reads_user_id', 'alert_reads', ['user_id'])
    op.add_column('watchdog_tasks',
                  sa.Column('only_new_data', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('watchdog_tasks', 'only_new_data')
    op.drop_index('ix_alert_reads_user_id', table_name='alert_reads')
    op.drop_table('alert_reads')
