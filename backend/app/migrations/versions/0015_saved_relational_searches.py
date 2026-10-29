"""relational searches kept by an account, and shared

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-29 16:00:00

`saved_searches` holds the *question* of a relational search (investigate.py) — the value, its entity type, the
depth and the scope — not its result: replaying it reads the catalogue as it is then, with the rights of
whoever opens it. `saved_search_shares` says who it is shared with, shaped like `grants`: a user or a group,
never both, at level `read` (replay it) or `edit` (change and reshare it).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'saved_searches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=1000), nullable=False, server_default=''),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('spec', sa.JSON(), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_saved_searches_owner_id', 'saved_searches', ['owner_id'])
    op.create_table(
        'saved_search_shares',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('search_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('group_id', sa.Integer(), nullable=True),
        sa.Column('level', sa.String(length=16), nullable=False, server_default='read'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['search_id'], ['saved_searches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('(user_id IS NULL) <> (group_id IS NULL)', name='ck_search_share_subject'),
        sa.CheckConstraint("level IN ('read', 'edit')", name='ck_search_share_level'),
        sa.UniqueConstraint('search_id', 'user_id', 'group_id', name='uq_search_share'),
    )
    op.create_index('ix_saved_search_shares_search_id', 'saved_search_shares', ['search_id'])
    op.create_index('ix_saved_search_shares_user_id', 'saved_search_shares', ['user_id'])
    op.create_index('ix_saved_search_shares_group_id', 'saved_search_shares', ['group_id'])


def downgrade() -> None:
    op.drop_table('saved_search_shares')
    op.drop_table('saved_searches')
