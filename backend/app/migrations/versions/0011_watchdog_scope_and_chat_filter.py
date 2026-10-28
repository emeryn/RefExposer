"""a watchdog that looks at everything, and the referential filter of a conversation

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-24 16:00:00

Two optional scopes, both defaulting to the behaviour of before:

- `watchdog_tasks.all_referentials`: the investigation follows the rights of the account it runs with instead
  of a fixed list of referentials.
- `conversations.referential_ids`: the filter a user chose for one conversation of the assistant, so the agent
  stops exploring referentials that have nothing to do with the question.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('watchdog_tasks',
                  sa.Column('all_referentials', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('conversations', sa.Column('referential_ids', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'referential_ids')
    op.drop_column('watchdog_tasks', 'all_referentials')
