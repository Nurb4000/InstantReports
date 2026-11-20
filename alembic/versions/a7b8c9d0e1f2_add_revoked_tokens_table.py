"""add_revoked_tokens_table

Revision ID: a7b8c9d0e1f2
Revises: a3f1c2d4e5b6
Create Date: 2026-11-20 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "a3f1c2d4e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("sub", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_revoked_tokens_jti", "revoked_tokens", ["jti"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_revoked_tokens_jti", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
