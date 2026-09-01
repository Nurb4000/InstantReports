"""add_report_templates_table

Revision ID: c7d8e9f0a1b2
Revises: f6e5d4c3b2a1
Create Date: 2026-09-01 11:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'f6e5d4c3b2a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_report_templates_user",
        "report_templates", "users", ["created_by"], ["id"]
    )
    op.create_index("idx_report_templates_name", "report_templates", ["name"])


def downgrade() -> None:
    op.drop_index("idx_report_templates_name", table_name="report_templates")
    op.drop_table("report_templates")
