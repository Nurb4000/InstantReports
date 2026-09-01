"""add_query_templates_table

Revision ID: a1b2c3d4e5f6
Revises: 5d118a82febb
Create Date: 2026-09-01 09:00:00.000000

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '5d118a82febb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("query_config", sa.JSON(), nullable=False),
        sa.Column("connection_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_query_templates_connection",
        "query_templates", "data_connections", ["connection_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_query_templates_user",
        "query_templates", "users", ["created_by"], ["id"]
    )
    op.create_index("idx_query_templates_connection", "query_templates", ["connection_id"])


def downgrade() -> None:
    op.drop_index("idx_query_templates_connection", table_name="query_templates")
    op.drop_table("query_templates")
