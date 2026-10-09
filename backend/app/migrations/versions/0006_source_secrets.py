"""secrets of the sources (secret manager)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('source_secrets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('value', sa.String(length=8192), nullable=False),
        sa.Column('hosts', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_source_secrets_name', 'source_secrets', ['name'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_source_secrets_name', table_name='source_secrets')
    op.drop_table('source_secrets')
