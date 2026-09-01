"""add_query_history_table

Revision ID: f6e5d4c3b2a1
Revises: a1b2c3d4e5f6
Create Date: 2026-09-01 09:30:00.000000

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'f6e5d4c3b2a1'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", UUID(as_uuid=True), nullable=True),
        sa.Column("element_id", sa.String(length=100), nullable=True),
        sa.Column("connection_id", UUID(as_uuid=True), nullable=True),
        sa.Column("query_config", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_query_history_report",
        "query_history", "reports", ["report_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_query_history_connection",
        "query_history", "data_connections", ["connection_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_query_history_user",
        "query_history", "users", ["created_by"], ["id"]
    )
    op.create_index("idx_query_history_report", "query_history", ["report_id"])
    op.create_index("idx_query_history_element", "query_history", ["element_id"])
    op.create_index("idx_query_history_connection", "query_history", ["connection_id"])


def downgrade() -> None:
    op.drop_index("idx_query_history_connection", table_name="query_history")
    op.drop_index("idx_query_history_element", table_name="query_history")
    op.drop_index("idx_query_history_report", table_name="query_history")
    op.drop_table("query_history")
