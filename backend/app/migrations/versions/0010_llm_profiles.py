"""saved connectors to an inference server

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-24 12:00:00

Several connectors can be saved side by side and switched with one click. The secrets of a profile (the API
key, the values of the extra headers) are kept encrypted in `secrets_enc`.

The profile of an instance that already has an assistant configured is created by the application at startup
(`llmprofiles.bootstrap`), not here: copying the API key over means decrypting it, which needs the key of the
instance and belongs in the application rather than in a migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('provider', sa.String(length=16), nullable=False, server_default='ollama'),
        sa.Column('base_url', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('model', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('verify_tls', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('timeout', sa.Integer(), nullable=False, server_default='120'),
        sa.Column('temperature', sa.Float(), nullable=False, server_default='0.2'),
        sa.Column('top_p', sa.Float(), nullable=False, server_default='0.95'),
        sa.Column('max_tokens', sa.Integer(), nullable=False, server_default='2048'),
        sa.Column('context_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('stop', sa.JSON(), nullable=True),
        sa.Column('options', sa.JSON(), nullable=True),
        sa.Column('small_model', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('secrets_enc', sa.String(length=8192), nullable=True),
        sa.Column('header_names', sa.JSON(), nullable=True),
        sa.Column('last_test_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_test_ok', sa.Boolean(), nullable=True),
        sa.Column('last_test_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.CheckConstraint("provider IN ('ollama', 'openai')", name='ck_llm_profiles_provider'),
    )
    op.create_index('ix_llm_profiles_name', 'llm_profiles', ['name'], unique=True)
    op.create_index('ix_llm_profiles_active', 'llm_profiles', ['active'])


def downgrade() -> None:
    op.drop_table('llm_profiles')
