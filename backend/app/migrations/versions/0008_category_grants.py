"""grants on a whole category

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23 12:00:00

The scope of a grant now also accepts "category:<name>", which is longer than a referential identifier.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('grants') as batch:
        batch.alter_column('referential_id', existing_type=sa.String(length=64), type_=sa.String(length=128),
                           existing_nullable=False)


def downgrade() -> None:
    # Grants on a category are dropped: their scope does not fit a referential identifier
    op.execute("DELETE FROM grants WHERE referential_id LIKE 'category:%'")
    with op.batch_alter_table('grants') as batch:
        batch.alter_column('referential_id', existing_type=sa.String(length=128), type_=sa.String(length=64),
                           existing_nullable=False)
