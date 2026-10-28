"""states of a referential saved by hand

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-28 20:00:00

A snapshot was until now only something time travel kept on its own. It can also be **saved by hand**, before
touching a referential: such a state carries a label and the account that saved it, and the retention of time
travel never drops it. It is what a restore puts back.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('referential_snapshots',
                  sa.Column('manual', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('referential_snapshots',
                  sa.Column('label', sa.String(length=200), nullable=False, server_default=''))
    op.add_column('referential_snapshots', sa.Column('created_by', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('referential_snapshots', 'created_by')
    op.drop_column('referential_snapshots', 'label')
    op.drop_column('referential_snapshots', 'manual')
