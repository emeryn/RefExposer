"""second factor (TOTP, WebAuthn)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('totp_secret', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('totp_enabled', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('totp_enabled_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('totp_last_step', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('recovery_codes', sa.JSON(), nullable=True))

    op.create_table('webauthn_credentials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('credential_id', sa.String(length=1024), nullable=False),
        sa.Column('public_key', sa.String(length=2048), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False),
        sa.Column('transports', sa.JSON(), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('credential_id'),
    )
    op.create_index('ix_webauthn_credentials_user_id', 'webauthn_credentials', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_webauthn_credentials_user_id', table_name='webauthn_credentials')
    op.drop_table('webauthn_credentials')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('recovery_codes')
        batch_op.drop_column('totp_last_step')
        batch_op.drop_column('totp_enabled_at')
        batch_op.drop_column('totp_enabled')
        batch_op.drop_column('totp_secret')
