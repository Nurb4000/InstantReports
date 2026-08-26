"""add_api_keys_table

Revision ID: 57a68b59befa
Revises: f4393eaac236
Create Date: 2026-08-26 16:45:00.000000

"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '57a68b59befa'
down_revision: Union[str, None] = 'f4393eaac236'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("key", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("permissions", sa.Text(), nullable=False, server_default="read"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_api_keys_key", "api_keys", ["key"])


def downgrade() -> None:
    op.drop_index("idx_api_keys_key", table_name="api_keys")
    op.drop_table("api_keys")
