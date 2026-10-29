"""an alert deleted from the feed of one account

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-29 10:00:00

Marking an alert read says it has been seen; `alert_reads.dismissed` says it should be gone. A deleted alert
leaves the feed and its counts for that account only, and comes back — like an alert that was read — when the
thing behind it happens again, because the marker remembers the date of what was acknowledged.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alert_reads',
                  sa.Column('dismissed', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('alert_reads', 'dismissed')
