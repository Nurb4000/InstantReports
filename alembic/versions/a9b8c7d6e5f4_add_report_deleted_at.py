"""add_report_deleted_at

Add a soft-delete marker to the reports table so deleting a report retains the
row (and its versions/outputs) for audit instead of physically removing it.

Revision ID: a9b8c7d6e5f4
Revises: a7b8c9d0e1f2
Create Date: 2026-11-21 10:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "deleted_at")
